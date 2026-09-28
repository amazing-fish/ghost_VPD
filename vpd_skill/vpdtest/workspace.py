"""工作区：定位 data/ 下的 policy、APP 描述、知识、用例、经验，以及 runs/ 输出目录。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .devices import create_device
from .observe import Observer
from .policy import Policy
from .states import StateModel
from .util import SKILL_ROOT, read_json


def load_config() -> dict:
    cfg = read_json(SKILL_ROOT / "config.example.json", {})
    cfg.update(read_json(SKILL_ROOT / "config.local.json", {}))
    return cfg


class App:
    def __init__(self, root: Path, app_id: str):
        self.id = app_id
        self.dir = root / "apps" / app_id
        if not self.dir.exists():
            raise ValueError(f"未知 APP: {app_id}（{self.dir} 不存在）")
        self.meta = read_json(self.dir / "app.json")
        self.bundle, self.ability = self.meta["bundle"], self.meta["ability"]
        self.targets = read_json(self.dir / "targets.json")["targets"]
        self.states = StateModel(read_json(self.dir / "states.json"), self.bundle)

    def target(self, ref) -> tuple:
        if isinstance(ref, dict):
            return f"inline:{ref.get('label', '?')}", ref
        if ref not in self.targets:
            raise ValueError(f"targets.json 中没有目标 {ref}")
        return ref, self.targets[ref]


class Workspace:
    def __init__(self, root: Optional[str] = None):
        self.root = Path(root or os.environ.get("VPD_WORKSPACE") or SKILL_ROOT / "data").resolve()
        self.runs_dir = self.root.parent / "runs"
        self.config = load_config()

    def policy(self) -> Policy:
        return Policy(read_json(self.root / "policy.json"))

    def layout_config(self) -> dict:
        return read_json(self.root / "layout.json", {})

    def app(self, app_id: str) -> App:
        return App(self.root, app_id)

    def case_path(self, case_id: str) -> Path:
        return self.root / "cases" / f"{case_id}.json"

    def load_case(self, case_id: str) -> dict:
        return read_json(self.case_path(case_id))

    def load_suite(self, suite_id: str) -> dict:
        return read_json(self.root / "suites" / f"{suite_id}.json")

    def resolve_world(self, arg: str) -> Path:
        for p in (Path(arg), SKILL_ROOT / "fixtures" / "worlds" / arg, self.root.parent / "fixtures" / "worlds" / arg):
            if p.exists():
                return p.resolve()
        raise ValueError(f"找不到回放 world: {arg}")

    def device(self, spec: Optional[str], state_path=None):
        spec = spec or self.config.get("device")
        if not spec:
            raise ValueError("未指定设备：--device hdc:<serial> 或 replay:<world.json>")
        if spec.startswith("replay:"):
            spec = "replay:" + str(self.resolve_world(spec.split(":", 1)[1]))
        return create_device(spec, state_path=state_path)


class RunContext:
    """一次运行共享的上下文：设备、APP、policy、观测器、经验 overlay 与动作预算。"""

    def __init__(self, ws: Workspace, app: App, device, store, overlay, mode: str = "regression",
                 display: Optional[dict] = None):
        self.ws, self.app, self.device, self.store, self.overlay, self.mode = ws, app, device, store, overlay, mode
        self.policy = ws.policy()
        self.observer = Observer(device, app.bundle, self.policy, app.states, store, ws.layout_config(), display)
        self.actions = 0
        self.last_obs, self.last_tree = None, None

    def observe(self, label: str = "", screenshot: bool = True) -> tuple:
        obs, tree = self.observer.observe(label, screenshot)
        self.last_obs, self.last_tree = obs, tree
        return obs, tree
