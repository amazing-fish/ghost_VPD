---
name: vpd-app-test
description: VPD 手机 APP 智能自动化测试（v0.1，HarmonyOS：鸿蒙智行、乾崑）。用于按已确认文字用例做回归、在允许范围内做探索式测试、把探索结果转成候选用例、根据证据判定通过/失败/阻塞/待确认，以及沉淀和验证定位/执行经验。涉及 VPD APP 的 UI/UX、文案、导航、登录与切车、算路、有人监护的控车过程显示（TOR、运动中信息、暂停/继续、后台返回）、异常路径、无 UI 树页面时使用。不用于车端固定路线泛化测试；控车只在有人监护的授权运行中执行。
---

# VPD APP 测试 Skill v0.1

你负责理解目标、检索知识与用例、选择策略、解读结果与沉淀经验；**所有设备操作、状态采集、超时、
证据和安全边界都由 `vpdtest` 执行器完成**。不要绕过它直接调用 hdc/adb/uitest，不要自己写点击脚本。

运行方式（Windows PowerShell，在 `vpd_skill` 目录）：`.\vpdtest.ps1 <命令> ...`，输出为 JSON。
首次使用见 [README.md](README.md)；命令与字段细节按需读取下方参考资料。

## 工作流（从文字目标出发）

默认输入是一句测试目标，例如"测试收藏车位重命名后，重新进入地图是否仍能按新名称找到它"。路径是未知的：
不要先找一条现成用例或流程照跑，也不要先写完整业务脚本再执行。已有的 targets/flows/用例都是**可检索的经验**，
要在现场核对后才能用。

1. **拆目标**：要做哪些操作（收藏、重命名、退出再进入、搜索）、要证明什么（新名称仍能找到）、会产生哪些副作用
   （user_data 要清理；登录/切车/进 VPD/算路/改收藏要 `--authorize`；车辆运动/车控还要 `--supervisor`，探索永远不带运动授权）。
2. **检索知识**：`knowledge search <关键词…> --app <app> --device <spec>`。结果分三类读：
   **产品要求**回答"应该怎样"（只有 confirmed 且适用于本次版本/形态的才能作判据）；**操作经验**回答"可以怎样操作"
   （targets/flows/状态签名/校准笔记，看 status 与适用性）；**现场观察**回答"某次实际发生了什么"（不能证明"应该是什么"）。
   适用范围无法确认时就写"未知"，不要因为页面相似就当作适用。测试目标里的期望不等于已确认的产品要求：
   找不到独立判据时用 `knowledge add` 记一条 `source.type=test_goal` 的 unconfirmed 条目，结论会是待确认。
3. **前置**：`doctor --device <spec>`。锁屏/离线/未安装、账号未登录、目标车辆未选好 → 报告阻塞，说明需要人做什么，
   不要解锁、猜密码或自动登录。**前台不是被测 APP（用户正在用手机）时不要 launch 抢前台**，报告等待。
4. **观察与探索**：`observe` 看页面与可点元素（label/type/id），`act tap|input --select "text=…"` 现场定位，
   或 `--target` 复用已登记目标。失败、被拒、超时都会留在会话里。允许修正定位、处理适用的弹窗、尝试其他入口；
   但如果原路径本身就是测试对象，替代路径成功不能替原路径判通过。
5. **固化**：`case from-session --session <s> --new-id <id> --app <app> --goal "<原文>"`。与目标无关的试探可以
   `--drop A4=理由` 排除，失败和效果未知的动作不能静默删掉。补齐 purpose / preconditions / assertions
   （`oracle_refs` 必须独立于本会话）/ cleanup / `knowledge_used`（用了哪条知识、判断是否适用及理由、映射到哪一步），
   然后 `case lint`。
6. **执行与判定**：`run <case> --device <spec> --authorize …`。结论只取执行器的 verdict；读 report 的
   "尚未验证的检查""清理""用到的知识与适用性"。退出码 2 或 `action_required` 表示需要人工接管，立即转告用户。
7. **交接**：汇报用到的知识及理由、每一步对应的命令、证据位置（obs/截图）、结论、未覆盖的范围，
   并分开说明离线回放与真机验证。下一位 Agent 只靠用例、知识和证据就能复测。

回归（执行已确认用例）、自动探索（`explore`，只在已声明状态上枚举）与经验验证见 [workflows](references/workflows.md)。

## 判定规则（硬约束）

- 通过/失败只来自"确定性证据 + 已确认判据（knowledge status=confirmed）"。判据未确认、冲突或缺失 → 待确认。
- 结论四类：通过 / 失败 / 阻塞 / 待确认；另有归属：产品 / 工具 / 环境 / 需求不明确。
- 模型（包括你自己看截图）的 UI/UX 意见只能写成建议或 `visual_review` 建议断言，不能直接成为缺陷。
- 看到"待确认""阻塞"时如实汇报原因与缺口，不要为了得到通过去：删步骤、放宽断言、改 expect_state、
  加长超时到 policy 上限以外、把中断标成 reviewed、换成坐标盲点。
- 恢复只允许执行器内置的三种：重新观察、关闭**已评审的良性中断**、幂等步骤重发。首次失败永远保留在报告里。
- 判据要适用于本次运行：知识的 `applies_to`（版本、形态、账号状态）与实测事实比较，未知或不符 → 待确认。
- 必需断言没执行到（前面步骤失败）→ 列为"尚未验证"，不推断成立；引用不存在的步骤 → 阻塞。必需清理未完成 → 不能判通过。
- 未确认的安全停止 → 结论阻塞并要求人工接管（`action_required`，CLI 退出码 2），产品失败仍保留在 findings 里。
- 用例成熟度与产品状态分开：稳定复现同一产品失败的用例可以 `case promote --known-issue <单号>` 固化，照常判失败，
  **不要改预期把缺陷变成通过**。

## 授权、密钥与控车

- 副作用分三级（`data/policy.json`）：硬禁止（不可逆账号变更、升级、支付、清数据）永远不做；gated（account_state、
  vehicle_selection、vpd_entry、route_planning、user_data）要 `--authorize <副作用>` 且目标声明同一副作用；
  supervised（vehicle_motion、vehicle_control）另需 `--supervisor`，vehicle_motion 还要用例 `safety`。缺任何一项 → 运行前判阻塞，一步都不执行。
- 账号、密码只写成 `{"secret": "NAME"}`，值来自环境变量 `VPD_SECRET_NAME`。不要把用户在对话里给的账号、密码、锁屏密码写进任何文件或命令行；
  锁屏始终由人手动解开。执行器会在 UI 树、run.json、events.jsonl 落盘前脱敏，页面含已输入敏感值时不截图。
- 控车过程用 `monitor` 观察（状态序列、TOR/接管、车速、故障文字），主观感受用 advisory `visual_review`。
  运动中出现首次失败、超时或用例结束仍未停 → 执行器自动安全停止并单独记录，首次失败保留；报告要求人工接管时立即转告用户。
- 点击前要确认控件可用的步骤写 `require_enabled`（例如「开始」置灰表示有故障）：不可用时判阻塞，不点。
- 选择器来自原工程的目标标 `unverified`，原工程没有、按描述推测的标 `hypothesis`：真机校准前含假设目标的用例不能晋级。
  方法见 [exploration-method.md](references/exploration-method.md)。

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
| `page.state = unknown` | 看该观测的 `screen.png`，描述页面语义；未声明页面用 `--expect-text` 写后置条件；如需纳入状态模型，提议 states.json 签名（需人工评审） |
| 无 UI 树 / 定位失败 | `--select` 只覆盖有 UI 树的页面，地图等自绘区域（XComponent）不在树里。先考虑已有模板；可用 `template-crop` 从观测裁剪模板并提议登记。只有确有把握时才用 `act tap --point x,y --basis "<看到了什么>"`，且执行器仍会按 policy 拦截 |
| `target_ambiguous` | 不要挑一个点；`diagnosis.candidates` 列出了各候选，用 `--within` 限定容器/分栏或改用 `id=`/`xpath=` |
| `target_stale` / `foreground_changed` | 观察与下发之间页面变了 / 前台被切走：重新 observe 再决定；前台是其他应用时停止并报告 |
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
.\vpdtest.ps1 run TC-QK-101 --device hdc:<serial> --env account_state=logged_out --authorize account_state vehicle_selection vpd_entry route_planning
.\vpdtest.ps1 run TC-QK-103 --device hdc:<serial> --env account_state=logged_in --authorize vpd_entry route_planning vehicle_motion --supervisor <监护人>
.\vpdtest.ps1 suite motor_hm_smoke --device hdc:<serial>
.\vpdtest.ps1 explore --start car_tab --budget 30 --goal "我的页导航" --device hdc:<serial>
.\vpdtest.ps1 knowledge search 收藏 重命名 --app qiankun_hm --device hdc:<serial>
.\vpdtest.ps1 observe --app qiankun_hm --session s1 --device hdc:<serial>
.\vpdtest.ps1 act tap --select "text=重命名" --side-effect user_data --authorize user_data --app qiankun_hm --session s1 --device hdc:<serial>
.\vpdtest.ps1 act input --select "xpath=//TextInput" --allow-input --text 测试名 --side-effect user_data --authorize user_data --app qiankun_hm --session s1 --device hdc:<serial>
.\vpdtest.ps1 session s1
.\vpdtest.ps1 case from-session --session s1 --new-id TC-QK-201 --app qiankun_hm --goal "<目标原文>" [--drop A4=理由]
.\vpdtest.ps1 act tap --target tab_my --expect my_tab --session s1 --device hdc:<serial>
.\vpdtest.ps1 case from-exploration --run <explore-id> --transitions T3 T5 --new-id TC-EXP-001
.\vpdtest.ps1 experience validate <XP-id> --suite motor_hm_smoke --device <spec1> --device <spec2>
```

离线（无真机）把 `--device` 换成 `replay:motor_hm_base.json`、`replay:qiankun_hm_ready.json` 等回放世界；回放结论只证明协议与闭环，必须在汇报中标注"离线回放"。

## 参考资料（按需读取）

- [contracts.md](references/contracts.md)：观测 / 执行 / 判定三个契约的字段与状态
- [capability-model.md](references/capability-model.md)：动作 → 原子能力 → 状态/流程 → 用例 → 用例集，含贯穿示例
- [workflows.md](references/workflows.md)：回归、探索、单步决策、探索转用例、有人监护控车的完整步骤
- [exploration-method.md](references/exploration-method.md)：探索与沉淀方法论、乾崑知识图、真机校准清单
- [knowledge-rsi.md](references/knowledge-rsi.md)：知识、经验候选、晋级条件、版本与回滚
- [host-integration.md](references/host-integration.md)：宿主 Agent 最小接入契约
- [repo-reuse.md](references/repo-reuse.md)：与 ghost_VPD 原工程的复用边界
