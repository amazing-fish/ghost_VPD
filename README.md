# ghost_VPD

VPD 手机 APP 智能自动化测试（HarmonyOS：鸿蒙智行、乾崑）。Agent 借助确定性执行器 `vpdtest`，
产出可重复执行、可独立判定、证据可追溯的功能测试用例。

- 执行器与 Skill：[`vpd_skill/`](vpd_skill/)，安装与运行见 [vpd_skill/README.md](vpd_skill/README.md)，
  Agent 指令见 [vpd_skill/SKILL.md](vpd_skill/SKILL.md)。
- 本地 CI：[docs/LOCAL_CI.md](docs/LOCAL_CI.md)，在本机执行固定依赖与全量离线测试，日常提交不再自动启动云端 CI。
- 协作规则：[docs/ISSUE_COLLABORATION.md](docs/ISSUE_COLLABORATION.md)；当前方向：[docs/PROJECT_DIRECTION.md](docs/PROJECT_DIRECTION.md)。

根目录原有的旧服务（Flask + Huey，`run.py` / `app/`）已删除，没有迁移，也不具备 Skill 的授权、
复核和运动看门狗能力。需要查阅时，旧代码保存在提交
[`90545b9`](https://github.com/amazing-fish/ghost_VPD/tree/90545b9) 中。
