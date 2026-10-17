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
        self.states = StateModel(read_json(self.dir / "states.json"), self.bundle, self.targets)
        self.flows = read_json(self.dir / "flows.json", {"flows": {}})["flows"]

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
                 display: Optional[dict] = None, policy=None, layout_cfg=None, dialogs=None):
        self.ws, self.app, self.device, self.store, self.overlay, self.mode = ws, app, device, store, overlay, mode
        self.facts = {}  # Runner 在实测 preflight 后绑定；--env 声明不成为条件证据
        from .conditions import definitions
        self.conditions = definitions({}, app)
        if not isinstance(self.conditions, dict):
            raise ValueError("conditions 须为命名条件定义对象")  # noqa: TRY004 - invalid asset
        self.step_results = {}  # 完整步骤 ID → 本次执行结果，供 when 引用已执行的来源
        self.policy = policy or ws.policy()
        from .dispatch import Dispatcher
        self.dispatcher = Dispatcher(device, store, self)
        from .sysdialog import load_dialogs
        self.observer = Observer(device, app.bundle, self.policy, app.states, store,
                                 ws.layout_config() if layout_cfg is None else layout_cfg, display,
                                 dialogs=load_dialogs(ws.root) if dialogs is None else dialogs,
                                 evidence_context=lambda: {"action_epoch": self.actions, "clock_s": self.clock(),
                                                           "scope": {"app": app.id, "app_version": self.facts.get("app_version")}})
        self.params = None        # 用例参数（含密钥引用），由 runner 设置
        self.authorized = self.policy.effective_grants(set())  # 任务许可；gated/supervised 仍需显式授予
        self.supervisor = None    # 在场监护人（supervised 类授权的前提），记入证据
        self.seed = store.run_id  # 随机动作的默认种子，记入证据以便复现
        self.actions = 0
        self.safety_hold = None  # 本运行停止未确认后的暂停记录；不自动解除
        self.last_obs, self.last_tree = None, None
        self._virtual_s = 0.0     # 回放设备不真等待：等待/监视时长累加到虚拟时钟，保证离线可复现
        from .safety import MotionGuard
        self.motion = MotionGuard(None, self.clock, store)

    def clock(self) -> float:
        import time
        return time.monotonic() + self._virtual_s

    def sleep(self, seconds: float) -> None:
        import time
        if seconds <= 0:
            return
        if self.device.poll_interval > 0:
            time.sleep(seconds)
        else:
            self._virtual_s += seconds

    def observe(self, label: str = "", screenshot: bool = True) -> tuple:
        obs, tree = self.observer.observe(label, screenshot)
        self.last_obs, self.last_tree = obs, tree
        self.motion.on_observation(obs)
        return obs, tree
