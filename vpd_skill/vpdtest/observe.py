"""观测契约：一次观测 = 前台应用 + 进程存活 + 限定到目标 bundle 的 UI 树 + 截图 + 页面状态。

每次观测写入 runs/<run>/obs/<obs_id>/，文件以独占方式创建，后续重试/恢复不会覆盖原始证据。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import layout as layout_mod
from .tree import UiTree
from .util import append_jsonl, new_id, now_iso, sha256_file, write_json


class RunStore:
    def __init__(self, runs_root: Path, run_id: Optional[str] = None, kind: str = "run"):
        self.run_id = run_id or new_id(kind)
        self.dir = Path(runs_root) / self.run_id
        (self.dir / "obs").mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        existing = [p.name for p in (self.dir / "obs").iterdir()] if (self.dir / "obs").exists() else []
        self._obs_seq = len(existing)
        self.redactor = None  # 运行带密钥参数时由 runner 设置

    def event(self, kind: str, **data) -> None:
        rec = {"ts": now_iso(), "event": kind, **data}
        append_jsonl(self.events_path, self.redactor(rec) if self.redactor else rec)

    def next_obs_dir(self) -> tuple:
        self._obs_seq += 1
        oid = f"obs-{self._obs_seq:04d}"
        d = self.dir / "obs" / oid
        d.mkdir(parents=True, exist_ok=False)
        return oid, d

    def rel(self, path) -> str:
        return str(Path(path).resolve().relative_to(self.dir.resolve())).replace("\\", "/")


class Observer:
    def __init__(self, device, bundle: str, policy, states, store: RunStore, layout_cfg: Optional[dict] = None,
                 display: Optional[dict] = None):
        self.device, self.bundle, self.policy, self.states, self.store = device, bundle, policy, states, store
        self.layout_cfg = layout_mod.config(layout_cfg)
        self.display = display          # 显示参数（像素密度、折叠状态）；窗口尺寸变化时重新读取
        self.last_size = None
        self.last_window = None
        self.last_layout_key = None
        self.layouts_seen: dict = {}    # layout key -> 首次出现的 obs_id
        self.params = None              # 用于脱敏；由 runner 设置

    def _display_for(self, tree: Optional[UiTree]) -> Optional[dict]:
        win = tree.window_rect() if (tree is not None and tree.status == "ok") else None
        size = [win[2] - win[0], win[3] - win[1]] if win else None
        stale = self.display is None or (size and self.last_size and list(size) != list(self.last_size))
        if stale and hasattr(self.device, "display_info"):
            self.display = self.device.display_info()
            if size and self.last_size:
                self.store.event("display_requery", reason="窗口尺寸变化（旋转/折叠/分屏）", size=size)
        return self.display

    def observe(self, label: str = "", screenshot: bool = True) -> tuple:
        oid, d = self.store.next_obs_dir()
        fg = self.device.foreground()
        fg_err = getattr(self.device, "last_query_error", None)
        alive = self.device.process_alive(self.bundle)
        obs = {"obs_id": oid, "ts": now_iso(), "label": label, "device": self.device.serial,
               "source": self.device.kind, "foreground": fg, "process_alive": alive}
        if fg_err:
            obs["foreground_error"] = fg_err
        tree = None
        if fg == self.bundle:
            r = self.device.dump_tree(self.bundle, d / "tree.json")
            if r.ok and self.params is not None:
                # 输入过的账号/密码可能以明文出现在布局树里：落盘前替换为 ***
                raw = self.params.redact(r.data.get("tree"))
                if raw != r.data.get("tree"):
                    write_json(d / "tree.json", raw)
                    obs["redacted"] = True
                r.data["tree"] = raw
            if r.ok:
                tree = UiTree(r.data.get("tree"))
                obs["tree"] = {"status": tree.status, "path": self.store.rel(d / "tree.json"), "nodes": len(tree.nodes)}
            else:
                obs["tree"] = {"status": "unavailable" if r.error_class == "tree_unavailable" else "error",
                               "error_class": r.error_class, "error": r.error}
        else:
            obs["tree"] = {"status": "not_collected", "reason": "目标 APP 不在前台，不采集其他 APP 的布局"}

        if not screenshot:
            obs["screenshot"] = {"status": "skipped"}
        elif obs.get("redacted"):
            # 截图无法可靠打码：页面上出现了账号/密码明文时不截图
            obs["screenshot"] = {"status": "withheld", "reason": "页面含已输入的敏感值，隐私策略不截图"}
        elif not self.policy.screenshot_allowed(fg):
            obs["screenshot"] = {"status": "withheld", "reason": f"前台为 {fg}，隐私策略不截图"}
        elif not self.policy.screenshot_allowed(fg2 := self.device.foreground()):
            # 取布局树要几秒：期间 APP 可能崩溃、退后台或被别的应用覆盖。截图前再确认一次，避免截到其他应用
            obs["screenshot"] = {"status": "withheld", "reason": f"取树后前台已变为 {fg2}，隐私策略不截图"}
            obs["foreground_after"] = fg2
        else:
            r = self.device.screenshot(d / "screen.png")
            if r.ok and (d / "screen.png").exists():
                obs["screenshot"] = {"status": "ok", "path": self.store.rel(d / "screen.png"),
                                     "sha256": sha256_file(d / "screen.png")[:16]}
            else:
                obs["screenshot"] = {"status": "error", "error_class": r.error_class, "error": r.error}

        display = self._display_for(tree)
        lay = layout_mod.profile(tree, display, self.layout_cfg)
        win = tree.window_rect() if (tree is not None and tree.status == "ok") else None
        if win:
            self.last_window = win
            self.last_size = (win[2] - win[0], win[3] - win[1])
        obs["screen_size"] = list(self.last_size) if self.last_size else None
        obs["layout"] = lay
        lk = layout_mod.key(lay)
        if lk:
            self.layouts_seen.setdefault(lk, oid)
            if self.last_layout_key and lk != self.last_layout_key:
                # 旋转/折叠/展开：记录为事件而不是异常，后续定位都按新形态现算
                self.store.event("layout_changed", obs_id=oid, frm=self.last_layout_key, to=lk)
            self.last_layout_key = lk
        obs["page"] = self.states.identify(tree, fg, alive, lay, fg_err)
        obs["texts"] = tree.visible_texts()[:60] if tree else []
        write_json(d / "observation.json", obs, exclusive=True)
        self.store.event("observe", obs_id=oid, label=label, state=obs["page"]["state"],
                         tree=obs["tree"]["status"], screenshot=obs["screenshot"]["status"], layout=lk,
                         panes=len(lay["panes"]))
        return obs, tree

    def screenshot_abs(self, obs: dict) -> Optional[str]:
        s = obs.get("screenshot", {})
        return str(self.store.dir / s["path"]) if s.get("status") == "ok" else None
