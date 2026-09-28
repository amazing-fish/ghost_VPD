---
name: vpd-app-test
description: VPD 手机 APP 智能自动化测试（v0.1，HarmonyOS 鸿蒙智行）。用于按已确认文字用例做回归、在允许范围内做探索式测试、把探索结果转成候选用例、根据证据判定通过/失败/阻塞/待确认，以及沉淀和验证定位/执行经验。涉及 VPD APP 的 UI/UX、文案、导航、登录态、异常路径、无 UI 树页面时使用。不用于车端固定路线泛化测试，不做真实车辆运动。
---

# VPD APP 测试 Skill v0.1

你负责理解目标、检索知识与用例、选择策略、解读结果与沉淀经验；**所有设备操作、状态采集、超时、
证据和安全边界都由 `vpdtest` 执行器完成**。不要绕过它直接调用 hdc/adb/uitest，不要自己写点击脚本。

运行方式（Windows PowerShell，在 `vpd_skill` 目录）：`.\vpdtest.ps1 <命令> ...`，输出为 JSON。
首次使用见 [README.md](README.md)；命令与字段细节按需读取下方参考资料。

## 工作流

1. **理解目标**：明确是回归（执行已确认用例）、探索（围绕功能目标找状态与迁移）还是整理用例/知识。
   目标涉及车辆运动或车控 → 停止，说明 v0.1 不在授权范围内。
2. **获取知识与用例**：`knowledge list`、读 `data/cases/*.json` 与 `data/knowledge/text_cases/`。
   找不到依据时如实说明缺口，**不要编造业务规则**；新规则用 `knowledge add` 记为 unconfirmed。
3. **前置检查**：`doctor --device <spec>`。锁屏、离线、未安装 → 结论为阻塞（环境），告诉用户需要做什么，不要尝试解锁或猜密码。
4. **执行**：回归用 `run <case>`；探索用 `explore --start <state> --budget N --goal ...`；
   需要你逐步决策时用 `observe` + `act`（见 [workflows](references/workflows.md)）。
5. **读结论**：打开返回的 `report.md`。结论只取执行器给出的 verdict，不要自行改判。
6. **沉淀**：对运行产生的经验候选执行 `experience validate`（跨用例、跨设备/世界与基线对比）；
   探索路径用 `case from-exploration` 生成探索记录，补齐字段后 `case lint` / `case promote`。

## 判定规则（硬约束）

- 通过/失败只来自"确定性证据 + 已确认判据（knowledge status=confirmed）"。判据未确认、冲突或缺失 → 待确认。
- 结论四类：通过 / 失败 / 阻塞 / 待确认；另有归属：产品 / 工具 / 环境 / 需求不明确。
- 模型（包括你自己看截图）的 UI/UX 意见只能写成建议或 `visual_review` 建议断言，不能直接成为缺陷。
- 看到"待确认""阻塞"时如实汇报原因与缺口，不要为了得到通过去：删步骤、放宽断言、改 expect_state、
  加长超时到 policy 上限以外、把中断标成 reviewed、换成坐标盲点。
- 恢复只允许执行器内置的三种：重新观察、关闭**已评审的良性中断**、幂等步骤重发。首次失败永远保留在报告里。

## 屏幕形态（直板 / 折叠 / 分栏）

- 用例、目标、经验里**不写像素坐标**。点击点每次按当前 UI 树现算（目标可见部分的中心），滑动按可滚动容器的边界现算；`case lint` 会拒绝含坐标的步骤。
- 每次观测记录 `layout`：窗口 px ÷ 像素密度 = vp 宽度，按 `data/layout.json` 分档（sm 窄屏 / md 以上宽屏）。
  直板机不论比例都是窄屏，差异主要是压缩；折叠屏展开是宽屏，折叠后又是窄屏，所以看窗口宽度而不是设备型号。
- 是否左右分栏以 UI 树里实际并排的大容器为准（`layout.panes`）。分栏时状态按栏识别，`page.active` 可能同时有两个状态（如 `my_tab+settings`）。
- 宽屏与窄屏布局不同的目标，用策略上的 `within`（限定容器/分栏）或 `when`（限定形态）表达，不要为某台设备写死区域或坐标。
- 形态阈值（用户估计约 640 vp，ArkUI 默认 600/840 vp）尚未在被测 APP 上确认；确认后只改 `data/layout.json`。

## 何时需要你的判断

| 情况 | 你要做的 |
|---|---|
| `page.state = unknown` | 看该观测的 `screen.png`，描述页面语义；如需纳入状态模型，提议 states.json 签名（需人工评审） |
| 无 UI 树 / 定位失败 | 先考虑已有模板；可用 `template-crop` 从观测裁剪模板并提议登记。只有确有把握时才用 `act tap --point x,y --basis "<看到了什么>"`，且执行器仍会按 policy 拦截 |
| `target_ambiguous` | 不要挑一个点；提议更精确的策略（`within` 容器/分栏、id），写入 targets.json 候选并评审 |
| `target_partially_visible` | 目标被列表裁剪或被底部导航挡住；为目标补 `scroll` 声明，而不是改成点边缘 |
| 只在宽屏/折叠屏上成立的用例 | 在用例上写 `layout_requirements`（如 `{"wide": true}`）；形态不符时判环境阻塞，不判失败 |
| `observation_failed` | 设备连接或查询失败（如 hdc `E000004`），属环境问题；等连接恢复后重跑，不要改用例 |
| 失败归属产品 | 结合证据写缺陷描述：判据条目、预期、实际、观测 ID、截图路径；注明验证级别（离线回放/真机） |
| 探索选路 | 用 `--prefer` 指定优先尝试的文字；预算用尽或无路可走时如实报告覆盖范围 |
| 经验/用例晋级 | 只通过 `experience adopt` / `case promote`；需要评审的类型请用户提供评审人 |

## 常用命令

```powershell
.\vpdtest.ps1 doctor --device hdc:<serial>
.\vpdtest.ps1 run TC-MOTOR-HM-001 --device hdc:<serial> [--env account_state=logged_out] [--record-replay]
.\vpdtest.ps1 suite motor_hm_smoke --device hdc:<serial>
.\vpdtest.ps1 explore --start car_tab --budget 30 --goal "我的页导航" --device hdc:<serial>
.\vpdtest.ps1 observe --session s1 --device hdc:<serial>
.\vpdtest.ps1 act tap --target tab_my --expect my_tab --session s1 --device hdc:<serial>
.\vpdtest.ps1 case from-exploration --run <explore-id> --transitions T3 T5 --new-id TC-EXP-001
.\vpdtest.ps1 experience validate <XP-id> --suite motor_hm_smoke --device <spec1> --device <spec2>
```

离线（无真机）把 `--device` 换成 `replay:motor_hm_base.json` 等回放世界；回放结论只证明协议与闭环，必须在汇报中标注"离线回放"。

## 参考资料（按需读取）

- [contracts.md](references/contracts.md)：观测 / 执行 / 判定三个契约的字段与状态
- [capability-model.md](references/capability-model.md)：动作 → 原子能力 → 状态/流程 → 用例 → 用例集，含贯穿示例
- [workflows.md](references/workflows.md)：回归、探索、单步决策、探索转用例的完整步骤
- [knowledge-rsi.md](references/knowledge-rsi.md)：知识、经验候选、晋级条件、版本与回滚
- [host-integration.md](references/host-integration.md)：宿主 Agent 最小接入契约
- [repo-reuse.md](references/repo-reuse.md)：与 ghost_VPD 原工程的复用边界
