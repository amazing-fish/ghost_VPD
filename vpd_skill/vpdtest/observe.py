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


class RunStore:
    def __init__(self, runs_root: Path, run_id: Optional[str] = None, kind: str = "run", *, resume: bool = False):
        if resume and not run_id:
            raise ValueError("恢复运行必须显式指定 run_id")
        root = Path(runs_root)
        root.mkdir(parents=True, exist_ok=True)
        for attempt in range(10):
            self.run_id = run_id if run_id is not None else new_id(kind)
            self.dir = root / self.run_id
            try:
                # Claim the run itself atomically, before creating or reading any evidence.
                self.dir.mkdir(parents=True, exist_ok=resume)
            except FileExistsError:
                if run_id is not None or attempt == 9:
                    raise
            else:
                break
        (self.dir / "obs").mkdir(exist_ok=resume)
        self.events_path = self.dir / "events.jsonl"
        existing = [p.name for p in (self.dir / "obs").iterdir()] if (self.dir / "obs").exists() else []
        self._obs_seq = len(existing)

    def event(self, kind: str, **data) -> None:
        rec = {"ts": now_iso(), "event": kind, **data}
        append_jsonl(self.events_path, rec)

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
                 display: Optional[dict] = None, dialogs: Optional[dict] = None, evidence_context=None):
        self.device, self.bundle, self.policy, self.states, self.store = device, bundle, policy, states, store
        self.layout_cfg = layout_mod.config(layout_cfg)
        self.display = display          # 显示参数（像素密度、折叠状态）；窗口尺寸变化时重新读取
        self.last_size = None
        self.last_window = None
        self.last_layout_key = None
        self.layouts_seen: dict = {}    # layout key -> 首次出现的 obs_id
        self.dialogs = dialogs or {}    # 已登记的系统弹窗注册表（system_dialogs.json）
        self.evidence_context = evidence_context  # 运行上下文在独占写盘前附加作用范围与动作/时间截面

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
        """截图只受被测 APP 白名单约束；系统覆盖物与输入内容不拦截取证。"""
        return self.policy.screenshot_allowed(pkg)

    def _cover_evidence(self, d) -> dict:
        """每次空树观测采集当前整屏诊断，避免复用已变化的覆盖物证据。"""
        from .sysdialog import diagnose_cover
        return diagnose_cover(self.device, self.store, d / "cover", self.dialogs)

    def _capture_screenshot(self, obs: dict, d) -> None:
        r = self.device.screenshot(d / "screen.png")
        if r.ok and (d / "screen.png").exists():
            obs["screenshot"] = {"status": "ok", "path": self.store.rel(d / "screen.png"),
                                 "sha256": sha256_file(d / "screen.png")[:16]}
            if r.data.get("fallback_from"):  # screenCap 投屏期间失败，回退 snapshot_display 得到的截图
                obs["screenshot"]["fallback_from"] = r.data["fallback_from"]
        else:
            obs["screenshot"] = {"status": "error", "error_class": r.error_class, "error": r.error, "raw": r.raw}

    def observe(self, label: str = "", screenshot: bool = True) -> tuple:
        oid, d = self.store.next_obs_dir()
        foreground_errors = []

        def foreground(stage):
            value = self.device.foreground()
            error = getattr(self.device, "last_query_error", None)
            if error not in (None, "foreground_unknown", "foreground_changed"):
                detail = dict(getattr(self.device, "last_query_error_detail", None) or {})
                foreground_errors.append({"stage": stage, "error_class": error,
                                          "error": detail.get("error"), "raw": detail.get("raw"),
                                          "detail": detail})
            return value

        fg = foreground("initial")
        fg_err = getattr(self.device, "last_query_error", None)
        alive = self.device.process_alive(self.bundle)
        obs = {"obs_id": oid, "ts": now_iso(), "label": label, "device": self.device.serial,
               "source": self.device.kind, "foreground": fg, "process_alive": alive}
        if getattr(self.device, "process_query_error", None):
            obs["process_error"] = self.device.process_query_error
        if fg_err:
            obs["foreground_error"] = fg_err
            obs["foreground_error_detail"] = getattr(self.device, "last_query_error_detail", None)
        tree = None
        if fg == self.bundle:
            r = self.device.dump_tree(self.bundle, d / "tree.json")
            if r.ok:
                raw = r.data.get("tree")
                write_json(d / "tree.json", raw)
                r.data["tree"] = raw
                tree = UiTree(raw)
                obs["tree"] = {"status": tree.status, "path": self.store.rel(d / "tree.json"), "nodes": len(tree.nodes)}
            else:
                # 解析失败/拉取失败：记录设备的实际采集错误
                obs["tree"] = {"status": "unavailable" if r.error_class == "tree_unavailable" else "error",
                               "error_class": r.error_class, "error": r.error, "raw": r.raw}
        else:
            obs["tree"] = {"status": "not_collected", "reason": "目标 APP 不在前台，不采集其他 APP 的布局"}
        if fg == self.bundle and (obs.get("tree") or {}).get("status") == "empty":
            cover = self._cover_evidence(d)
            obs["screen_cover"] = {k: v for k, v in cover.items() if not k.startswith("_")}
            if cover.get("matched"):
                self.store.event("system_dialog", obs_id=oid, dialog=cover["matched"],
                                 windows=cover.get("windows"))

        foreground_before_shot = fg
        if not screenshot:
            obs["screenshot"] = {"status": "skipped"}
        elif not self._shot_ok(fg):
            obs["screenshot"] = {"status": "withheld", "reason": f"前台 {fg} 不在截图 APP 白名单中"}
        else:
            foreground_before_shot = foreground("before_screenshot")
            if not self._shot_ok(foreground_before_shot):
                obs["screenshot"] = {"status": "withheld", "reason": f"截图前前台 {foreground_before_shot} 不在 APP 白名单中"}
            else:
                self._capture_screenshot(obs, d)

        display = self._display_for(tree)
        if (display or {}).get("execution_error"):
            # 显示重取是附加元数据；保留原错，不否定独立有效的前台/UI 树证据。
            obs["display_error"] = display["execution_error"]
        # 所有设备采集（含显示参数）结束后复查；不能只删除截图而沿用旧树。
        fg_after = foreground("final")
        final_error = getattr(self.device, "last_query_error", None)
        if foreground_errors:
            # 同一次观测后查成功不会补采早先缺失的树/截图；下一次完整观测独立记录。
            obs["foreground_query_errors"] = foreground_errors
        obs["foreground_after"] = fg_after  # 仅保留查询记录；消费者看 foreground/page/fresh。
        obs["fresh"] = fg_after is not None and fg_after == fg == foreground_before_shot
        if not obs["fresh"]:
            reason = final_error or ("foreground_unknown" if fg_after is None else "foreground_changed")
            alive = self.device.process_alive(self.bundle)
            if getattr(self.device, "process_query_error", None):
                obs["process_error"] = self.device.process_query_error
            else:
                obs.pop("process_error", None)
            obs.update(foreground=fg_after, process_alive=alive, foreground_before=fg)
            obs["tree"] = {**obs["tree"], "captured_status": obs["tree"]["status"], "status": "stale", "reason": reason}
            tree, fg = None, fg_after
            fg_err = reason if fg_after is None else None
            if fg_err:
                obs["foreground_error"] = fg_err
                obs["foreground_error_detail"] = getattr(self.device, "last_query_error_detail", None)
            else:
                obs.pop("foreground_error", None)
            if not self._shot_ok(fg_after) and (d / "screen.png").exists():
                (d / "screen.png").unlink()
                capture_error = obs["screenshot"] if obs["screenshot"].get("status") == "error" else None
                obs["screenshot"] = {"status": "withheld", "reason": f"观测末查前台 {fg_after} 不在 APP 白名单中，已删除截图",
                                     **({"capture_error": capture_error} if capture_error else {})}
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
        if self.evidence_context is not None:
            obs.update(self.evidence_context())
        write_json(d / "observation.json", obs, exclusive=True)
        self.store.event("observe", obs_id=oid, label=label, state=obs["page"]["state"],
                         tree=obs["tree"]["status"], screenshot=obs["screenshot"]["status"], layout=lk,
                         panes=len(lay["panes"]), fresh=obs["fresh"])
        return obs, tree

    def screenshot_abs(self, obs: dict) -> Optional[str]:
        s = obs.get("screenshot", {})
        return str(self.store.dir / s["path"]) if s.get("status") == "ok" else None
