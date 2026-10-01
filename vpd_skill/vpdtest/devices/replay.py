"""离线替身设备：按 world.json 回放 Harmony 风格 UI 树，用于在没有真机时验证协议与闭环。

回放结果只证明执行器/判定逻辑，不代表真实产品行为；run.json 会标记 device.kind=replay。
world 支持 "extends" 继承与 "patch_screens" 覆盖，方便构造漂移、弹窗、锁屏、崩溃等变体。

有状态的交互（登录后页面不同、搜索结果随输入变化）用两种机制模拟：
- flags：节点 set_flag 在点击时写入；屏幕 if_flag 按 flag 改道到另一屏；节点 show_if_flag / hide_if_flag / text_flag。
- bind：输入框声明 bind=<名字> 时记下输入值；节点 text_bound / show_if_bound / show_if_match 按输入值显示。
  输入值只存在内存里、不写 replay_state.json（输入的可能是账号），因此跨 CLI 进程的单步 act 不保留输入。
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Optional

from . import DeviceResult
from ..tree import CLIP_TYPES, intersect
from ..util import read_json, write_json

LAUNCHER = "com.ohos.sceneboard"
PLACEHOLDER_ATTRS = ("accessibilityId", "backgroundColor", "backgroundImage", "blur", "bounds", "checkable", "checked",
                     "clickable", "clip", "description", "displayId", "enabled", "focused", "hitTestBehavior",
                     "hostWindowId", "id", "key", "longClickable", "opacity", "origBounds", "originalText",
                     "scrollable", "selected", "text", "type", "zIndex")
# 2026-09-29 在 SGT-AL10 / OpenHarmony-7.0.0.105 锁屏状态下执行 aa start 的真实输出
LOCKED_LAUNCH_OUTPUT = ("error: failed to start ability.\nError Code:10106102  Error Message:The device screen is locked "
                        "during the application launch, unlock screen failed.")


def placeholder_tree() -> dict:
    """真机在目标窗口不在前台时，dumpLayout -b 返回的就是这种全空占位根节点。"""
    attrs = {k: "" for k in PLACEHOLDER_ATTRS}
    attrs["bounds"] = "[0,0][0,0]"
    return {"attributes": attrs, "children": []}


def expand(spec: dict, bundle: str = "") -> dict:
    attrs = {k: "" for k in PLACEHOLDER_ATTRS}
    b = spec.get("b", [0, 0, 0, 0])
    attrs.update({"type": spec.get("t", "Column"), "text": spec.get("text", ""), "id": spec.get("id", ""),
                  "description": spec.get("desc", ""), "bounds": f"[{b[0]},{b[1]}][{b[2]},{b[3]}]",
                  "clickable": "true" if spec.get("click") or spec.get("go") else "false",
                  "selected": "true" if spec.get("sel") else "false", "enabled": "true", "visible": "true",
                  "scrollable": "true" if spec.get("scroll") else "false", "bundleName": bundle})
    attrs.update({k: str(v) for k, v in spec.get("attrs", {}).items()})
    kids = [expand(c, bundle) for c in spec.get("children", []) if not c.get("hidden_from_tree")]
    return {"attributes": attrs, "children": kids}


def load_world(path) -> dict:
    p = Path(path)
    world = read_json(p)
    if "extends" in world:
        base = load_world(p.parent / world["extends"])
        if "rescale" in world:  # 先把继承来的屏幕按比例换算到新分辨率，再应用本 world 的覆盖（覆盖用新坐标）
            _rescale(base, world["rescale"])
        for k, v in world.items():
            if k == "rescale":
                continue
            if k == "patch_screens":  # 整屏替换
                for sid, scr in v.items():
                    base["screens"][sid] = scr
            elif k == "fragments":  # 片段合并（新增片段不覆盖继承来的片段）
                base.setdefault("fragments", {}).update(v)
            elif k == "insert_children":  # 在屏幕根节点最前面插入子节点
                for sid, kids in v.items():
                    root = base["screens"][sid]["root"]
                    root["children"] = list(kids) + root.get("children", [])
            elif k == "replace_text":  # 替换屏幕内的文案
                for sid, mapping in v.items():
                    for node, _ in _walk(base["screens"][sid]["root"]):
                        if node.get("text") in mapping:
                            node["text"] = mapping[node["text"]]
            elif k not in ("extends",):
                base[k] = v
        world = base
    for scr in world.get("screens", {}).values():
        scr["root"] = _use_fragments(scr["root"], world.get("fragments", {}))
    world["_path"] = str(p)
    return world


def _use_fragments(spec: dict, frags: dict) -> dict:
    """{"use": "tabbar", "sel": "我的"}：复用片段，并把文字为 sel 的节点标为选中。"""
    if "use" in spec:
        out = copy.deepcopy(frags[spec["use"]])
        for node, parents in _walk(out):
            if spec.get("sel") and node.get("text") == spec["sel"]:
                # 与真机一致：选中态标在最近的可点击容器上
                holder = next((p for p in reversed(parents) if p.get("click") or p.get("go")), node)
                holder["sel"] = True
        return out
    if spec.get("children"):
        spec = {**spec, "children": [_use_fragments(c, frags) for c in spec["children"]]}
    return spec


def _walk(spec: dict, parents=()):
    yield spec, parents
    for c in spec.get("children", []):
        yield from _walk(c, parents + (spec,))


def _hits(root: dict, x: int, y: int, screen) -> list:
    """点击命中：与真机一致，滚动容器外被裁掉的部分点不到（会落到其上层/相邻的底部导航等控件）。"""
    out = []

    def rec(spec, parents, clip):
        b = spec.get("b")
        vis = intersect(tuple(b), clip) if b else None
        if vis and vis[0] <= x < vis[2] and vis[1] <= y < vis[3]:
            out.append((spec, parents))
        child_clip = (vis if b else clip) if (spec.get("scroll") or spec.get("t") in CLIP_TYPES) else clip
        for c in spec.get("children", []):
            rec(c, parents + (spec,), child_clip)
    rec(root, (), (0, 0, screen[0], screen[1]))
    return out


def _rescale(world: dict, to: list) -> None:
    """按宽高分别缩放所有 bounds，模拟"直板机不同分辨率/比例主要是压缩"。这只是近似：
    真实 APP 按 vp 布局，窄屏上是重排而不是等比缩小，需要逐屏覆盖的地方用 patch_screens。"""
    fw, fh = world["screen"]
    sx, sy = to[0] / fw, to[1] / fh
    roots = [s["root"] for s in world["screens"].values()] + list(world.get("fragments", {}).values())
    for root in roots:
        for node, _ in _walk(root):
            if node.get("b"):
                b = node["b"]
                node["b"] = [round(b[0] * sx), round(b[1] * sy), round(b[2] * sx), round(b[3] * sy)]
    world["screen"] = list(to)


class ReplayDevice:
    kind = "replay"

    def __init__(self, world_path, state_path=None):
        self.world = load_world(world_path)
        self.world_path = str(world_path)
        self.spec = f"replay:{world_path}"
        self.serial = f"replay-{Path(world_path).stem}"
        self.poll_interval = 0.0
        self.bundle = self.world["bundle"]
        self.state_path = Path(state_path) if state_path else None
        if self.state_path and self.state_path.exists():
            self.state = read_json(self.state_path)
        else:
            init = self.world.get("initial", {})
            self.state = {"locked": init.get("locked", False), "screen_on": init.get("screen_on", True),
                          "foreground": init.get("foreground", LAUNCHER), "alive": False, "stack": [],
                          "popup": None, "installed": init.get("installed", True), "events": 0}
        self.state.setdefault("flags", dict(self.world.get("initial_flags", {})))
        self.bound: dict = {}  # 输入框的输入值：只在内存里
        self.last_query_error = None

    def _save(self):
        self.state["events"] += 1
        if self.state_path:
            write_json(self.state_path, self.state)

    def _res(self, op, ok=True, err=None, msg=None, raw="", data=None) -> DeviceResult:
        self._save()
        return DeviceResult(op, True, ok, err, msg, raw, 0, data or {})

    def current_screen(self) -> Optional[str]:
        if self.state["popup"]:
            return self.state["popup"]
        return self.state["stack"][-1] if self.state["stack"] else None

    def _in_app(self) -> bool:
        return (not self.state["locked"]) and self.state["foreground"] == self.bundle and self.state["alive"]

    def _screen_spec(self, sid: str) -> dict:
        spec, seen = self.world["screens"][sid], {sid}
        while spec.get("if_flag"):  # {"flag": "logged_in", "then": "discover_in"}：按 flag 改道
            rule = spec["if_flag"]
            nxt = rule["then"] if self.state["flags"].get(rule["flag"]) else None
            if not nxt or nxt in seen:
                break
            seen.add(nxt)
            spec = self.world["screens"][nxt]
        return spec

    def _live(self, root: dict) -> dict:
        """按 flags / 输入值展开节点：隐藏不满足条件的节点，替换带变量的文字。点击命中与布局树都用它，保持一致。"""
        flags, bound = self.state["flags"], self.bound

        def visible(n):
            if n.get("show_if_flag") and not flags.get(n["show_if_flag"]):
                return False
            if n.get("hide_if_flag") and flags.get(n["hide_if_flag"]):
                return False
            if n.get("show_if_bound") and not bound.get(n["show_if_bound"]):
                return False
            if n.get("show_if_match"):  # [输入名, 另一个输入名]：两次输入的值相同才显示（例如按收藏名搜索）
                a, b = n["show_if_match"]
                if not bound.get(a) or bound.get(a) != bound.get(b):
                    return False
            return True

        def rec(n):
            n = dict(n)
            if n.get("text_bound"):
                n["text"] = n.get("text_format", "{}").format(bound.get(n["text_bound"], n.get("text", "")))
            if n.get("text_flag"):
                n["text"] = str(flags.get(n["text_flag"], n.get("text", "")))
            if n.get("children"):
                n["children"] = [rec(c) for c in n["children"] if visible(c)]
            return n
        return rec(root)

    def _composed(self) -> Optional[dict]:
        """当前屏幕 + 弹窗（弹窗与底层页面在同一窗口树，真机 Dialog 也是这样出现在布局中）。"""
        base = self.state["stack"][-1] if self.state["stack"] else None
        if base is None:
            return None
        root = copy.deepcopy(self._screen_spec(base)["root"])
        if self.state["popup"]:
            root.setdefault("children", []).append(copy.deepcopy(self._screen_spec(self.state["popup"])["root"]))
        return self._live(root)

    # ---- 状态采集 ----
    def info(self) -> dict:
        d = self.world.get("device", {})
        return {"serial": self.serial, "kind": self.kind, "model": d.get("model"), "os": d.get("os"),
                "world": self.world_path}

    def app_version(self, bundle: str) -> Optional[str]:
        return self.world.get("app_version") if bundle == self.bundle and self.state["installed"] else None

    def env_facts(self) -> dict:
        return dict(self.world.get("env_facts", {}))

    def display_info(self) -> dict:
        d = self.world.get("display", {})
        w, h = self.world["screen"]
        return {"width_px": w, "height_px": h, "density": d.get("density"), "rotation": d.get("rotation", "0"),
                "fold_status": d.get("fold_status"), "device_form": d.get("device_form"),
                "device_form_basis": "回放 world 声明", "source": "replay world"}

    def preflight(self, bundle: str) -> dict:
        s = self.state
        checks = [{"check": "device_online", "ok": True, "error_class": None},
                  {"check": "screen_awake", "ok": s["screen_on"], "value": "AWAKE" if s["screen_on"] else "SLEEP",
                   "error_class": None if s["screen_on"] else "screen_off"},
                  {"check": "screen_unlocked", "ok": not s["locked"], "value": str(s["locked"]).lower(),
                   "error_class": "screen_locked" if s["locked"] else None}]
        ver = self.app_version(bundle)
        checks.append({"check": "app_installed", "ok": ver is not None, "value": ver,
                       "error_class": None if ver else "app_missing"})
        return {"ok": all(c["ok"] for c in checks), "checks": checks, "device": self.info(), "app_version": ver,
                "display": self.display_info()}

    def _query_fails(self, name: str) -> bool:
        # 测试用：replay_state 里 query_fail 列出的查询返回"查询失败"（真机 hidumper/aa dump 偶发失败）
        return name in (self.state.get("query_fail") or [])

    def foreground(self) -> Optional[str]:
        self.last_query_error = "command_failed" if self._query_fails("foreground") else None
        if self.last_query_error:
            return None
        return LAUNCHER if self.state["locked"] else self.state["foreground"]

    def process_alive(self, bundle: str) -> Optional[bool]:
        return self.state["alive"] if bundle == self.bundle else None

    def dump_tree(self, bundle: str, local_path) -> DeviceResult:
        if not self._in_app() or bundle != self.bundle:
            tree = placeholder_tree()
        else:
            if self._screen_spec(self.state["stack"][-1]).get("tree") == "unavailable":
                self._tick()
                return self._res("dump_tree", False, "tree_unavailable", "该页面不提供 UI 树（模拟 XComponent/自绘页面）")
            tree = {"attributes": {"type": "orgRoot", "bounds": "[0,0][%d,%d]" % tuple(self.world["screen"])},
                    "children": [expand(self._composed(), self.bundle)]}
            self._tick()  # 本次返回的是推进前的画面，下一次观测才看到变化
        write_json(local_path, tree)
        return self._res("dump_tree", raw="DumpLayout saved", data={"tree": tree})

    def screenshot(self, local_path) -> DeviceResult:
        try:
            import cv2  # noqa: F401
        except ImportError:
            return self._res("screenshot", False, "unsupported", "未安装 opencv-python，回放设备无法渲染截图")
        render(self._composed() if self._in_app() else None, self.world["screen"], local_path,
               popup=self.state["popup"] is not None)
        return self._res("screenshot", raw="ScreenCap saved")

    # ---- 动作 ----
    def tap(self, x: int, y: int) -> DeviceResult:
        if self.state["locked"] and hasattr(self, "_pin_buffer"):
            self._pin_buffer += str(x)
            expected = str(self.world.get("initial", {}).get("lock_pin", ""))
            if len(self._pin_buffer) >= len(expected):
                self.state["pin_attempts"] = self.state.get("pin_attempts", 0) + 1
                self.state["locked"] = self._pin_buffer != expected
            return self._res("tap", raw="PIN digit")
        if not self._in_app():
            return self._res("tap", raw="tap outside target app")
        sid = self.current_screen()
        hits = _hits(self._live(self._screen_spec(sid)["root"]), x, y, self.world["screen"])
        if hits:
            spec, parents = max(hits, key=lambda sp: len(sp[1]))
            for node in (spec,) + tuple(reversed(parents)):
                if str(node.get("attrs", {}).get("enabled", "true")).lower() == "false":
                    break  # 置灰控件点了没反应（与真机一致）
                self.state["flags"].update(node.get("set_flag", {}))
                if node.get("go"):
                    self._go(node["go"])
                    break
        return self._res("tap", raw=f"click {x} {y}")

    def _tick(self) -> None:
        """时间推进：屏幕声明 after_obs {n, go} 时，被观测 n 次后自动切到 go。
        用来模拟泊车过程（准备 → 泊入中 → 接管提示 → 完成）、Toast 自动消失等不需要点击的变化。"""
        cur = self.current_screen()
        spec = self._screen_spec(cur).get("after_obs") if cur else None
        tick = self.state.get("tick") or [None, 0]
        if tick[0] != cur:
            tick = [cur, 0]
        tick[1] += 1
        if spec and tick[1] >= int(spec["n"]):
            if spec.get("replace") and not self.state["popup"] and self.state["stack"]:
                self.state["stack"][-1] = spec["go"]  # 同一流程内的阶段切换，不叠加返回栈
            else:
                self._go(spec["go"])
            tick = [None, 0]
        self.state["tick"] = tick

    def _go(self, go: str) -> None:
        s = self.state
        if go == "@back":
            self._back()
        elif go == "@dismiss":
            s["popup"] = None
        elif go == "@root":  # 回到首屏（例如登录成功后关掉登录页栈）
            s.update(stack=s["stack"][:1], popup=None)
        elif go == "@crash":
            s.update(alive=False, foreground=LAUNCHER, stack=[], popup=None)
        elif go in self.world["screens"]:
            target = self._screen_spec(go)
            if target.get("kind") == "popup":
                s["popup"] = go
                return
            s["popup"] = None  # 从弹窗里跳到别的页面：弹窗随之关闭
            if target.get("tab") and s["stack"]:
                s["stack"][-1] = go
            elif target.get("stage") and s["stack"] and self._screen_spec(s["stack"][-1]).get("stage"):
                s["stack"][-1] = go  # 同一流程的阶段页（泊入中/暂停/完成）互相切换，不叠加返回栈
            else:
                s["stack"].append(go)

    def _back(self) -> None:
        s = self.state
        if s["popup"]:
            if self.world.get("popup_back", "dismiss") == "dismiss":
                s["popup"] = None
        elif len(s["stack"]) > 1:
            s["stack"].pop()
        else:
            s.update(stack=[], foreground=LAUNCHER)

    def swipe(self, x1, y1, x2, y2, speed: int = 2000) -> DeviceResult:
        if self._in_app() and not self.state["popup"]:
            spec = self._screen_spec(self.state["stack"][-1])
            nxt = spec.get("on_swipe_" + ("up" if y2 < y1 else "down"))
            scrollers = [s["b"] for s, _ in _walk(spec["root"]) if s.get("scroll") and s.get("b")]
            # 声明了滚动容器的屏幕：起点必须落在容器内才会滚动（和真机一样，滑到别的栏上不会滚这个列表）
            inside = not scrollers or any(b[0] <= x1 < b[2] and b[1] <= y1 < b[3] for b in scrollers)
            if nxt and inside:
                self.state["stack"][-1] = nxt
        return self._res("swipe", raw="swipe")

    def input_text(self, x: int, y: int, text: str) -> DeviceResult:
        if self._in_app():
            hits = _hits(self._live(self._screen_spec(self.current_screen())["root"]), x, y, self.world["screen"])
            if hits:
                spec, parents = max(hits, key=lambda sp: len(sp[1]))
                box = next((n for n in (spec,) + tuple(reversed(parents)) if n.get("bind")), None)
                if box is not None:
                    self.bound[box["bind"]] = text
                    if box.get("on_input"):
                        self._go(box["on_input"])
        return self._res("input_text", raw="inputText")

    def screen_locked(self) -> Optional[bool]:
        if self._query_fails("screen_locked"):
            return None
        return bool(self.state["locked"] or not self.state["screen_on"])

    def wake(self):
        self.state["screen_on"] = True
        return self._res("wake")

    def lock_pin_pad(self):
        self._pin_buffer = ""
        return {str(n): (n, 0) for n in range(10)}

    def wake_unlock(self, pin, dispatcher=None):
        from ..unlock import wake_unlock
        return wake_unlock(self, pin, dispatcher, sleep=lambda _: None)

    def shown_windows(self) -> Optional[list]:
        # 合成世界默认只有被测 APP 的窗口；测试可在 replay_state 里放 windows（通知横幅等），null 表示查询失败
        return self.state.get("windows", ["app0"])

    def clear_text(self, x: int, y: int, count: int, dispatcher, preconditions) -> DeviceResult:
        if self._in_app():
            hits = _hits(self._live(self._screen_spec(self.current_screen())["root"]), x, y, self.world["screen"])
            if hits:
                spec, parents = max(hits, key=lambda sp: len(sp[1]))
                box = next((n for n in (spec,) + tuple(reversed(parents)) if n.get("bind")), None)
                if box is not None:
                    self.bound.pop(box["bind"], None)
        return self._res("clear_text", raw=f"deleted {count}")

    def key(self, name: str) -> DeviceResult:
        if name == "back" and self._in_app():
            self._back()
        elif name == "home":
            # on_background：退到后台时页面的变化（例如泊车中退后台 → 车端自动暂停）；kill_on_background 模拟进程被回收
            if self._in_app() and self.state["stack"]:
                spec = self._screen_spec(self.state["stack"][-1])
                if spec.get("on_background"):
                    self.state["stack"][-1] = spec["on_background"]
                    self.state["popup"] = None
                if spec.get("kill_on_background"):
                    self.state.update(alive=False, stack=[], popup=None)
            self.state["foreground"] = LAUNCHER
        elif name != "back":
            return self._res(f"key_{name}", False, "unsupported", f"不支持的按键 {name}")
        return self._res(f"key_{name}", raw="keyEvent")

    def launch(self, bundle: str, ability: str) -> DeviceResult:
        s = self.state
        if s["locked"]:
            return self._res("launch", False, "screen_locked", LOCKED_LAUNCH_OUTPUT, LOCKED_LAUNCH_OUTPUT)
        if bundle != self.bundle or not s["installed"]:
            return self._res("launch", False, "command_failed", "error: bundle not found", "error: bundle not found")
        s["foreground"] = bundle
        if not s["alive"] or not s["stack"]:
            s.update(alive=True, stack=[self.world["launch_screen"]], popup=self.world.get("launch_popup"))
        elif self._screen_spec(s["stack"][-1]).get("on_resume"):
            s["stack"][-1] = self._screen_spec(s["stack"][-1])["on_resume"]
        return self._res("launch", raw="start ability successfully.")

    def stop(self, bundle: str) -> DeviceResult:
        if bundle == self.bundle:
            self.state.update(alive=False, stack=[], popup=None)
            if self.state["foreground"] == bundle:
                self.state["foreground"] = LAUNCHER
        return self._res("stop", raw="force stop process successfully.")


def _draw(img, root: dict) -> None:
    import cv2
    import numpy as np
    for spec, _ in _walk(root):
        b = spec.get("b")
        if not b:
            continue
        x1, y1, x2, y2 = b
        if spec.get("t") == "Dialog":
            cv2.rectangle(img, (x1, y1), (x2, y2), (250, 250, 250), -1)
        cv2.rectangle(img, (x1, y1), (x2 - 1, y2 - 1), (205, 205, 205), 2)
        if spec.get("icon"):
            rng = np.random.default_rng(int(hashlib.md5(spec["icon"].encode()).hexdigest()[:8], 16))
            cells = rng.integers(0, 255, (8, 8, 3), dtype=np.uint8)
            img[y1:y2, x1:x2] = cv2.resize(cells, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST)
        elif spec.get("text"):
            seed = int(hashlib.md5(spec["text"].encode("utf-8")).hexdigest()[:6], 16)
            tw, cy = min(x2 - x1 - 8, 24 * len(spec["text"])), (y1 + y2) // 2
            cv2.rectangle(img, (x1 + 4, cy - 10), (x1 + 4 + tw, cy + 10),
                          (seed & 255, (seed >> 8) & 255, (seed >> 16) & 255), -1)


def render(root: Optional[dict], size, path, popup: bool = False) -> None:
    """合成截图：文本渲染为按文字哈希着色的色块，图标渲染为确定性纹理，便于模板匹配测试。"""
    import cv2
    import numpy as np
    w, h = size
    img = np.full((h, w, 3), 255 if root else 60, np.uint8)
    if root:
        base = copy.deepcopy(root)
        top = base["children"].pop() if popup and base.get("children") else None
        _draw(img, base)
        if top:
            img[:] = (img * 0.5).astype(np.uint8)
            _draw(img, top)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", img)[1].tofile(str(path))
