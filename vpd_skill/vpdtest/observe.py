"""观测契约：一次观测 = 前台应用 + 进程存活 + 限定到目标 bundle 的 UI 树 + 截图 + 页面状态。

每次观测写入 runs/<run>/obs/<obs_id>/，文件以独占方式创建，后续重试/恢复不会覆盖原始证据。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

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

    def event(self, kind: str, **data) -> None:
        append_jsonl(self.events_path, {"ts": now_iso(), "event": kind, **data})

    def next_obs_dir(self) -> tuple:
        self._obs_seq += 1
        oid = f"obs-{self._obs_seq:04d}"
        d = self.dir / "obs" / oid
        d.mkdir(parents=True, exist_ok=False)
        return oid, d

    def rel(self, path) -> str:
        return str(Path(path).resolve().relative_to(self.dir.resolve())).replace("\\", "/")


class Observer:
    def __init__(self, device, bundle: str, policy, states, store: RunStore):
        self.device, self.bundle, self.policy, self.states, self.store = device, bundle, policy, states, store
        self.last_size = None

    def observe(self, label: str = "", screenshot: bool = True) -> tuple:
        oid, d = self.store.next_obs_dir()
        fg = self.device.foreground()
        alive = self.device.process_alive(self.bundle)
        obs = {"obs_id": oid, "ts": now_iso(), "label": label, "device": self.device.serial,
               "source": self.device.kind, "foreground": fg, "process_alive": alive}
        tree = None
        if fg == self.bundle:
            r = self.device.dump_tree(self.bundle, d / "tree.json")
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
        elif not self.policy.screenshot_allowed(fg):
            obs["screenshot"] = {"status": "withheld", "reason": f"前台为 {fg}，隐私策略不截图"}
        else:
            r = self.device.screenshot(d / "screen.png")
            if r.ok and (d / "screen.png").exists():
                obs["screenshot"] = {"status": "ok", "path": self.store.rel(d / "screen.png"),
                                     "sha256": sha256_file(d / "screen.png")[:16]}
            else:
                obs["screenshot"] = {"status": "error", "error_class": r.error_class, "error": r.error}

        if tree is not None and tree.screen_size():
            self.last_size = tree.screen_size()
        obs["screen_size"] = list(self.last_size) if self.last_size else None
        obs["page"] = self.states.identify(tree, fg, alive)
        obs["texts"] = tree.visible_texts()[:60] if tree else []
        write_json(d / "observation.json", obs, exclusive=True)
        self.store.event("observe", obs_id=oid, label=label, state=obs["page"]["state"],
                         tree=obs["tree"]["status"], screenshot=obs["screenshot"]["status"])
        return obs, tree

    def screenshot_abs(self, obs: dict) -> Optional[str]:
        s = obs.get("screenshot", {})
        return str(self.store.dir / s["path"]) if s.get("status") == "ok" else None
