"""观测契约：一次观测 = 前台应用 + 进程存活 + 限定到目标 bundle 的 UI 树 + 截图 + 页面状态。

每次观测写入 runs/<run>/obs/<obs_id>/，文件以独占方式创建，后续重试/恢复不会覆盖原始证据。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import layout as layout_mod
from .tree import UiTree
import re

from .util import append_jsonl, new_id, now_iso, sha256_file, write_json

# 页面内失败提示：真机上见过「停车场信息加载失败，」「请重试」。只做提示，不参与判定
ANOMALY_TEXT = re.compile(r"失败|异常|出错|错误|解析|请重试|重新加载|网络不给力|超时|无法连接|服务繁忙")
# 盖在被测 APP 上、会带出个人内容的系统窗口（窗口名前缀；hidumper 把名称截到 20 个字符）。
# 2026-09-30 真机：聊天通知横幅盖住乾崑搜索栏，按 bundle 取的树里被遮部分消失，截图带出了消息内容
PRIVATE_WINDOWS = ("SCBBannerNotificatio", "SCBScreenLock", "SCBDropdownPanel", "SCBNegativeScreen", "SCBGlobalSearch")
# uitest inputText 经系统剪贴板输入，输入法的「来自剪贴板」栏会显示最近一次输入的原文
KEYBOARD_WINDOWS = ("softKeyboard", "SCBKeyboardPanel")


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
        self.secret_typed = False       # 本次运行/会话输入过密钥：之后输入法显示时不截图（剪贴板栏会显示原文）

    def private_windows(self) -> Optional[list]:
        """正在显示的、会带出个人内容的系统窗口；设备不支持窗口查询 → []，查询失败 → None。"""
        if not hasattr(self.device, "shown_windows"):
            return []
        shown = self.device.shown_windows()
        if shown is None:
            return None
        names = PRIVATE_WINDOWS + (KEYBOARD_WINDOWS if self.secret_typed else ())
        return [w for w in shown if w.startswith(names)]

    def _display_for(self, tree: Optional[UiTree]) -> Optional[dict]:
        win = tree.window_rect() if (tree is not None and tree.status == "ok") else None
        size = [win[2] - win[0], win[3] - win[1]] if win else None
        stale = self.display is None or (size and self.last_size and list(size) != list(self.last_size))
        if stale and hasattr(self.device, "display_info"):
            self.display = self.device.display_info()
            if size and self.last_size:
                self.store.event("display_requery", reason="窗口尺寸变化（旋转/折叠/分屏）", size=size)
        return self.display

    def _shot_ok(self, pkg) -> bool:
        """截图门禁：白名单只说明哪些 APP 能测，截图还要求前台就是本次的被测 APP（白名单里的另一个 APP 也不截）。"""
        return self.policy.screenshot_allowed(pkg) and (
            pkg == self.bundle or not self.policy.privacy.get("screenshot_only_when_target_foreground", True))

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

        foreground_before_shot = fg
        if not screenshot:
            obs["screenshot"] = {"status": "skipped"}
        elif obs.get("redacted"):
            # 截图无法可靠打码：页面上出现了账号/密码明文时不截图
            obs["screenshot"] = {"status": "withheld", "reason": "页面含已输入的敏感值，隐私策略不截图"}
        elif not self._shot_ok(fg):
            obs["screenshot"] = {"status": "withheld", "reason": f"前台为 {fg}，隐私策略不截图"}
        elif fg == self.bundle and (obs.get("tree") or {}).get("status") == "empty":
            # 真机：锁屏界面（带通知内容）盖在 APP 上时，aa dump 仍报 APP 在前台，但按 bundle 取的树只有空根。
            # 屏幕上显示的不是被测 APP，不截图
            obs["screenshot"] = {"status": "withheld", "reason": "前台报为被测 APP 但其布局树为空（锁屏/系统弹层覆盖），隐私策略不截图"}
        elif not self._shot_ok(foreground_before_shot := self.device.foreground()):
            # 取布局树要几秒：期间 APP 可能崩溃、退后台或被别的应用（包括白名单里的另一个被测 APP）覆盖。
            # 截图前再确认一次，必须仍是本次的被测 APP
            obs["screenshot"] = {"status": "withheld", "reason": f"取树后前台已变为 {foreground_before_shot}，隐私策略不截图"}
        elif (over := self.private_windows()) is None or over:
            # 通知横幅/下拉栏/锁屏盖在 APP 上时 aa dump 仍报 APP 在前台，按 bundle 取的树也不含这些窗口
            obs["screenshot"] = {"status": "withheld", "reason": "窗口查询失败，无法确认没有系统弹层，隐私策略不截图"
                                 if over is None else f"系统窗口 {over} 正在显示（通知/锁屏/输入法剪贴板），隐私策略不截图"}
            obs["system_overlays"] = over
        else:
            r = self.device.screenshot(d / "screen.png")
            # 截图要约 1 秒：期间可能弹出横幅，也可能切到别的 APP（private_windows 只认系统弹层，
            # 认不出普通应用）。截完再查前台和系统窗口，任一不能确认就删掉
            late = self.private_windows() if r.ok else []
            if r.ok and late != [] and (d / "screen.png").exists():
                (d / "screen.png").unlink()
                obs["screenshot"] = {"status": "withheld", "reason": "截图期间出现系统弹层或无法确认，已删除截图"}
                obs["system_overlays"] = late
            elif r.ok and (d / "screen.png").exists():
                obs["screenshot"] = {"status": "ok", "path": self.store.rel(d / "screen.png"),
                                     "sha256": sha256_file(d / "screen.png")[:16]}
            else:
                obs["screenshot"] = {"status": "error", "error_class": r.error_class, "error": r.error}
        if "system_overlays" in obs:  # 查询失败（None）也记：报告里写"查询失败"
            self.store.event("system_overlay", obs_id=oid, windows=obs["system_overlays"])

        display = self._display_for(tree)
        # 所有设备采集（含显示参数）结束后复查；不能只删除截图而沿用旧树。
        fg_after = self.device.foreground()
        final_error = getattr(self.device, "last_query_error", None)
        obs["foreground_after"] = fg_after  # 仅保留查询记录；消费者看 foreground/page/fresh。
        obs["fresh"] = fg_after is not None and fg_after == fg == foreground_before_shot
        if not obs["fresh"]:
            reason = final_error or ("foreground_unknown" if fg_after is None else "foreground_changed")
            alive = self.device.process_alive(self.bundle)
            obs.update(foreground=fg_after, process_alive=alive, foreground_before=fg)
            obs["tree"] = {**obs["tree"], "captured_status": obs["tree"]["status"], "status": "stale", "reason": reason}
            tree, fg = None, fg_after
            fg_err = reason if fg_after is None else None
            if fg_err:
                obs["foreground_error"] = fg_err
            else:
                obs.pop("foreground_error", None)
            if (d / "screen.png").exists():
                (d / "screen.png").unlink()
                obs["screenshot"] = {"status": "withheld", "reason": f"观测期间前台变为 {fg_after}，已删除截图"}
            self.store.event("observation_stale", obs_id=oid, foreground_before=obs["foreground_before"],
                             foreground_before_shot=foreground_before_shot, foreground_after=fg_after,
                             reason=reason)

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
        # 页面内的失败/异常提示（不是弹窗中断，状态识别照样能认出页面）：单独列出，Agent 与报告都要先处理它
        hits = [t for t in (tree.visible_texts() if tree else []) if ANOMALY_TEXT.search(t)]
        if hits:
            obs["ui_alerts"] = {"texts": hits[:10], "ts": obs["ts"],
                                "procedure": "先记录时间与截图，点右上角「反馈」填写（时间/页面/原文/前一步/车辆/版本）并提交，"
                                             "再决定是否重试；首次异常保留在报告里"}
            self.store.event("ui_alert", obs_id=oid, texts=hits[:10])
        write_json(d / "observation.json", obs, exclusive=True)
        self.store.event("observe", obs_id=oid, label=label, state=obs["page"]["state"],
                         tree=obs["tree"]["status"], screenshot=obs["screenshot"]["status"], layout=lk,
                         panes=len(lay["panes"]), fresh=obs["fresh"])
        return obs, tree

    def screenshot_abs(self, obs: dict) -> Optional[str]:
        s = obs.get("screenshot", {})
        return str(self.store.dir / s["path"]) if s.get("status") == "ok" else None
