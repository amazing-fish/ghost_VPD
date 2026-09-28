# vpd_skill：VPD 手机 APP 智能测试 Skill v0.1

供 Agent 使用的工程化测试 Skill：Agent 读 [SKILL.md](SKILL.md) 做理解、计划、判断与沉淀；
确定性执行器 `vpdtest`（Python，零服务依赖）负责设备操作、观测、超时、证据、判定与安全边界。

- 首版范围：**HarmonyOS + 鸿蒙智行（`app.huawei.hmos.motor`）+ 底部导航 / 我的 / 设置 切片**。不触发车辆运动，可随时结束 APP 恢复。
- 选择理由：本机连接的是 Harmony 真机（SGT-AL10，OpenHarmony 7.0，已装 3.0.3.200），原工程 Harmony 选择器最完整（`MOTOR_HARMONY_CONFIG`），该切片覆盖页面切换、状态识别、弹窗、定位变化，且不需要车辆授权。
- 与原工程的关系：独立目录增量接入，不 import `app` 包、不改原有功能。复用边界见 [references/repo-reuse.md](references/repo-reuse.md)。

## 目录

```
vpd_skill/
  SKILL.md                 Agent 指令（精炼）
  references/              按需加载：契约、能力模型、工作流、知识与 RSI、宿主接入、复用边界
  vpdtest/                 执行器（CLI: python -m vpdtest）
    devices/harmony_hdc.py 真机适配（hdc / uitest / aa / bm）
    devices/replay.py      离线替身设备（回放 world.json）
  data/
    policy.json            运行边界（禁止文本、禁止副作用、隐私、预算）
    apps/motor_hm/         app.json / targets.json（定位策略）/ states.json（状态签名、中断、recipe）
    knowledge/             items.json（有来源的知识）、text_cases/（文字用例）
    cases/  suites/        结构化用例与用例集
    experience/<app>/      经验候选、已采纳 overlay、ledger（运行后自动生成）
  fixtures/worlds/         回放世界：base / drift / popup / locked / typo / crash（合成数据）
  tests/                   pytest
  scripts/demo_offline.py  离线闭环演示
```

## 安装（Windows / PowerShell）

前提：Python 3.9+；真机需 `hdc`（DevEco Studio 或 OpenHarmony SDK 的 toolchains 目录）加入 PATH，手机开启开发者模式并授权 USB 调试。

```powershell
cd <仓库>\vpd_skill
# 首次若提示脚本被禁止执行：
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\install.ps1                  # 创建 .venv、安装固定版本依赖、生成 config.local.json、执行 doctor
.\install.ps1 -LinkSkill       # 另外把 Skill 挂到 %USERPROFILE%\.claude\skills\vpd-app-test（Claude Code）
.\install.ps1 -IndexUrl <公司 pip 镜像>
```

依赖只有 `lxml`、`opencv-python`、`numpy`（与原工程版本一致）和 `pytest`；不需要数据库、Redis、Flask 或网络服务。

## 配置

`config.local.json`（已 gitignore）：

```json
{"device": "hdc:<serial>", "app": "motor_hm"}
```

- `device`：`hdc:<serial>`（`hdc list targets` 查看）或 `replay:<world>.json`。命令行 `--device` 优先。
- 环境变量 `VPD_WORKSPACE` 或 `--workspace` 可指向另一份 `data/`（例如团队共享的知识与用例）。

## 运行

```powershell
.\vpdtest.ps1 doctor --device hdc:<serial>
.\vpdtest.ps1 run TC-MOTOR-HM-001 --device hdc:<serial>
.\vpdtest.ps1 suite motor_hm_smoke --device hdc:<serial> --fail-on failed blocked
.\vpdtest.ps1 explore --start car_tab --budget 30 --device hdc:<serial>
.\vpdtest.ps1 knowledge confirm K-MOT-001 --reviewer <你的名字>
# 离线闭环演示（不需要真机）：
.venv\Scripts\python.exe scripts\demo_offline.py
# 测试：
.venv\Scripts\python.exe -m pytest -q tests
```

每次运行输出到 `runs/<run_id>/`：`report.md`、`run.json`、`events.jsonl`、`obs/<id>/{observation.json, tree.json, screen.png}`。
截图可能含个人信息，`runs/` 已 gitignore，外发前请自行检查。

回归、探索、单步决策、探索转用例、经验晋级的完整步骤见 [references/workflows.md](references/workflows.md)
与 [references/knowledge-rsi.md](references/knowledge-rsi.md)。

## 数据状态说明（重要）

- 仓库和本机均**未找到既有的 VPD 知识库或文字用例**。`data/knowledge/items.json` 中 K-MOT-* 条目提取自原工程代码（附文件行号与提交号），状态均为 **unconfirmed**；`TC-MOTOR-HM-001.md` 是按原工程登录/登出路径整理的种子样例。因此在真机上运行这些用例，结论会是"待确认"而不是"通过"，这是预期行为。
- 由用例负责人 `knowledge confirm` 后，同样的运行才会给出通过/失败。
- `car_tab` / `my_tab` 的状态签名已在真机（3.0.3.200）上校准：选中态 `selected=true` 标在 `TabBar` 下可点击的 `Column` 容器上，而非文字节点，见 `states.json` 的 `source`。`settings` 签名与 `update_dialog` 中断仍是 `review: pending`，需在真机上核对后改为 `reviewed`。

## 验证情况

| 项 | 方式 | 结果 |
|---|---|---|
| hdc 输出解析（锁屏、错误序列号、包不存在时退出码为 0） | 真机采集的输出 → 单元测试 | 通过 |
| 目标不在前台时 `dumpLayout -b` 返回空占位树 → 识别为 `empty` 而不是"元素不存在" | 真机现象 → 单元测试 | 通过 |
| 前置检查：锁屏 / 解锁、亮屏、APP 版本 | 真机 `doctor` | 锁屏时 `screen_unlocked=false`；解锁后四项通过，版本 3.0.3.200 |
| 隐私：前台为其他 APP 时不采集布局、不截图 | 真机 `observe` | 通过（前台为其他 APP 时 tree=not_collected、screenshot=withheld） |
| 满足预期判通过 / 违反预期判失败 / 锁屏与环境事实不足判阻塞 / 判据未确认或缺失判待确认 / 崩溃判失败 / 空树上的"不存在"断言不可判定 | 离线回放 + pytest | 通过 |
| 工具恢复不擦除首次异常；未评审弹窗不自动关闭；从不点击「更新」 | 离线回放 + pytest | 通过 |
| 定位漂移 → 经验候选 → 跨 3 用例 × 2 世界验证 → 采纳 → 回滚 | 离线回放 + pytest | 通过 |
| 探索（禁止文本跳过、覆盖声明）→ 探索记录 → 补齐 → 两次新运行重放 → replay_verified；无真机重放拒绝晋级正式回归 | 离线回放 + pytest | 通过 |
| 无 UI 树页面走模板定位；模板多处匹配拒绝点击；无树时拒绝盲点坐标 | 离线回放 + pytest | 通过 |
| 真机执行完整用例 TC-MOTOR-HM-001（爱车 → 我的 → 上滑找到设置 → 返回） | 真机 `run` | 首次运行 S1 失败，定位为选中态签名写错（属性在容器上），校准后重跑 4 步全部执行成功，8 次动作约 39 s；结论为"待确认"，因为判据 K-MOT-* 未确认 |
| 真机探索 | — | 未运行：设置页含开关类控件，已加 `deny_node_types` 拒绝点击，但尚未在真机上验证 |

## 仍未验证的边界

- 真实弹窗文案、崩溃/无响应提示文案；TC-MOTOR-HM-002/003 未在真机上运行。
- 真机上的模板定位（原工程模板图片 `app/assets/imgs/**` 不在仓库中，需要用 `template-crop` 从真机截图重新裁剪）。
- 长时间运行、多设备并发、弱网与后台切换等扰动；Android 适配器与其他 APP。
- 等待时间经验（`wait_timeout`）只在真机上才会产生，离线回放没有覆盖其自动生成路径。
- 涉及车辆运动的能力：v0.1 由 policy 拒绝，未设计授权流程。
