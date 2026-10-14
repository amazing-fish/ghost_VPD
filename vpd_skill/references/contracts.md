# 执行与资产契约

所有字段都落在 `runs/<run_id>/` 下：`run.json`（汇总）、`events.jsonl`（追加写的原始事件）、
`obs/<obs_id>/`（每次观测独占创建：`observation.json`、`tree.json`、`screen.png`）、`report.md`。

## 1. 观测（`obs/<id>/observation.json`）

| 字段 | 含义 |
|---|---|
| `obs_id` / `ts` / `label` | 观测编号、时间、触发点（如 `S2#1:after`） |
| `source` | `hdc`（真机）或 `replay`（离线替身） |
| `foreground` / `process_alive` | 前台 bundle、目标进程是否存活（`pidof`） |
| `tree.status` | `ok` 有可用内容 / `empty` 调用成功但只有占位根节点 / `unavailable` 页面不提供 UI 树 / `error` 采集或解析失败 / `not_collected` 目标不在前台，不采其他 APP 的业务树 / `stale` 前台变化，旧树只作证据 |
| `screenshot.status` | `ok` / `withheld`（前台未知或不在 APP 白名单；`reason` 写明）/ `error`（设备实际采集失败）/ `skipped`（调用方关闭截图）。覆盖窗口、空树和输入内容不拦截 |
| `screen_cover` | 目标 bundle 树为空时的独立整屏诊断：`windows`（未知为 null）、`dump_ok`、`dump_error`、`screen_tree`、`texts`、`matched`；每次重新采集，不复用旧覆盖物证据；整屏树不替代业务树 |
| `page.state` | 已声明状态 ID，或 `unknown`（附 `signature`）、`ambiguous`（附 `candidates`）、`undetermined`（无可用树）、`out_of_app`（附 `suspected_crash`） |
| `page.interrupts` | 命中的中断：`{id, class: benign/anomaly/unknown, review: reviewed/pending}` |
| `texts` | 可见文本（前 60 条），便于 Agent 快速判断 |
| `foreground_error` | 前台查询失败的原因（如 `transport_unavailable`）；此时 `page.state=undetermined`，不当成"不在 APP 内" |
| `fresh` | 采集结束时前台明确、与初次及截图前查询一致；否则为 false，旧树只留作证据 |
| `foreground_before` / `foreground_after` | 过时观测的初始前台 / 末次前台查询记录；判定使用规范化后的 `foreground`、`page` 和 `fresh` |
| `layout` | 窗口形态，见下 |
| `page.active` / `page.panes` | 当前生效的状态（分栏时可能多个）/ 每栏各自命中的状态 |

所有分支（无截图、失败、被拦及成功）在设备采集结束后复查前台。截图前的前台检查仍保留，决定能否截图。
前台变化、切走后切回，或末次前台未知时，`fresh=false`、`tree.status=stale`，返回的树为 `None`，旧 `tree.json` 保留。
同时重查目标进程存活，用末次前台和无树重新识别页面，清空旧树派生的 texts/ui_alerts，记录 `observation_stale`。
最终断言装载器不把 stale 原始树重新恢复成可判定树；判定端也独立拒绝过时状态/树。
监视按逐项 obs 引用检查新鲜度：有效命中可支持已出现事实；没有有效命中但有过时采样时为 undeterminable，不以证据缺口证明未出现。
已离开目标 APP 为 out_of_app；末次前台未知为 undetermined，不用旧树制造后置通过。Observer 不自动重采，重试由调用方决定。

### 窗口形态 `layout`

| 字段 | 含义 |
|---|---|
| `window_px` / `window_rect` / `source` | APP 窗口尺寸与位置；优先取 UI 树（分屏、悬浮窗时小于屏幕），否则取显示参数 |
| `density` / `density_source` | 像素密度（真机：`hidumper -s DisplayManagerService` 的 `VirtualPixelRatio`，只读 Screen/Display 段） |
| `width_vp` / `width_class` / `wide` | vp 宽度、按 `data/layout.json` 的分档、是否宽屏档；无密度时为 null，依赖它们的 `when` 策略记为跳过 |
| `orientation` / `fold_status` / `device_form` | 横竖、折叠状态、直板或折叠屏（设备属性，只记录，不驱动定位） |
| `panes` / `pane_count` | UI 树中实际并排的大容器（高 ≥ 窗口一半、宽 ≥ 阈值、互不重叠、合计覆盖父容器大部分宽度） |

窗口尺寸变化（旋转、折叠/展开、分屏）时重新读取显示参数，并追加 `layout_changed` 事件，之后的定位都按新形态现算。
`run.json.layout` 汇总前置检查读到的显示参数与本次运行中出现过的形态。

### 定位策略里的形态相关字段

| 字段 | 作用 |
|---|---|
| `within` | 只在某个容器内查找：`{"type": "TabBar"}`、`{"id": "..."}`、`{"pane": 0 / -1 / "first" / "last"}`；单栏窗口里 pane 引用指整个窗口 |
| `when` | 只在某些形态下使用该策略：`width_class`、`wide`、`orientation`、`panes`、`fold_status`、`device_form`；形态未知时记为 `skipped_layout_unknown` |
| `region` | 相对窗口的比例 `[x1, y1, x2, y2]`，不是像素 |
| 模板 `density` | 模板采集时的像素密度；已知时只在"当前密度 / 采集密度"附近缩放搜索 |
| 目标 `min_visible_fraction` | 默认 0.5：目标可见部分低于该比例时判 `partially_visible`，先按目标 `scroll` 声明在滚动容器内滑动，不点边缘 |

点击点 = 目标节点**可见矩形**的中心：可见矩形 = 自身 bounds ∩ 窗口 ∩ 祖先中 List/Scroll/Grid/WaterFlow/Swiper 等裁剪容器的可见区域。
`node_at`（policy 判断点到了什么）也只看可见矩形，所以被列表裁掉、实际落在底部导航上的点击会按底部导航检查。

组合策略：有 UI 树 → 结构化定位（xpath/text/id，与原工程 hmdriver2 XPath 语义一致）；无 UI 树 → 截图模板；
二者都不可靠 → 重新观察，仍不行则结束为阻塞（工具）。Agent 看截图得到的判断只能作为 `act --point --basis` 的提议，
或作为建议，不会变成证据。OCR 在 v0.1 未接入（本机未安装 OCR 引擎），接入点是 `locate.resolve` 的新策略类型。

## 2. 执行（`steps[].attempts[]` 与 `recoveries[]`）

每个原子能力的约束：

| 能力 | 前置条件 | 参数 | 后置条件（默认） | 超时 | 重试 | 副作用 |
|---|---|---|---|---|---|---|
| `launch` | 设备在线、未锁屏、已安装 | — | `expect_state`（常用 `@in_app`） | 步骤 `timeout_s`，上限 policy `max_wait_s` | ≤2 | 启动/恢复 APP |
| `stop_app` | — | — | 无 | 同上 | ≤2 | 结束进程 |
| `tap` | 目标唯一定位；policy 允许 | `target` 或（agent 模式）`point`+`basis` | `expect_state` | 同上 | ≤2 | 取决于目标 `side_effect` |
| `back` / `home` | 未锁屏；明确识别被测 APP 或桌面在前台 | — | `expect_state` | 同上 | ≤2 | 导航 |
| `swipe` | 有可滚动容器或已知窗口尺寸 | `direction`，可选 `within` | `expect_state` | 同上 | ≤2 | 滚动；起止点取容器可见区域的 75%/25% 高度，分栏时不会滑到另一栏；滑动前后内容指纹不变记为 `scroll_end` |
| `input` | policy 开启输入且目标允许；非敏感框 | `target`,`text` | `expect_state` | 同上 | ≤2 | 改变输入框内容 |
| `ensure_state` | recipe 存在 | `state` | 到达该状态且无中断 | 由子步骤决定 | 子步骤各自 | recipe 中动作的副作用 |
| `resume` | APP 已启动过 | — | `expect_state`；另记 `resume.{process_alive_before, cold_start}` | 同上 | ≤2 | 从后台拉回（与 launch 同一命令） |
| `wait` | — | `duration_s`（上限 policy `max_monitor_s`） | `expect_state` | — | — | 无；运动中等待仍计入看门狗 |
| `read` | 目标唯一定位，或 `read_scope: page` | `target`、`read_scope`（node 节点文字 / self 节点子树 / page 整页）、`pattern`（取第 1 组） | 读到值（`measurement.value` 非空） | 同上 | ≤2 | 无 |
| `monitor` | — | `duration_s`、`interval_s`、`watch{名: 正则}`、`until_state` / `until_text` / `until_text_gone`、`stop_on`、`abort_on` | 见下方「监视」 | `duration_s`（上限 `max_monitor_s`） | 不重试 | 无（只观测） |
| `flow` | `flows.json` 中存在 | `flow`、`params` | 每个子步骤各自的后置条件 | 子步骤各自 | 子步骤默认 1 | 子步骤的副作用 |

前台查询返回未知（`None`）时，`back`/`home` 按 `state_indeterminate` 停下，即使查询命令成功也不下发。
已识别的桌面允许导航；真机 `aa dump` 没有前台 mission 不能作为桌面证据，当前适配器保持返回未知，
直到有未锁屏桌面及系统层窗口的样本支持正面识别。

动作预算和运动超时在统一下发关口中复核，关闭弹窗、滚动查找、解锁及启动/停止 APP 都使用本次运行的同一计数。
只有实际下发才计数；清空输入框逐个点击/按键计数，其外层 `dispatch.aggregate=true` 仅汇总结果，不重复计数。
`_safety` 步骤及其子动作豁免预算和运动超时，状态、触摸归属与 policy 检查仍生效。
独立 `unlock` CLI 无运行上下文，dispatch 的预算/运动检查记为 `applicable=false`；run preflight 和动作恢复使用运行上下文。
解锁前置失败由判定层生成 `kind=unlock` 的 alert，CLI 顶层显示具体接管原因并以退出码 2 提醒调用方。

会话执行与转用例共用字段映射：monitor 的 `expect`/`expect_text` 分别为 `until_state`/`until_text`，
文字未出现时不能通过；其他动作仍使用 `expect_state`/`expect_text`。

`tap`/`input` 的可选字段：

| 字段 | 作用 |
|---|---|
| `require_enabled` | 点之前按 `enabled` 与目标 `enabled_by`（如 `{"attr": "opacity", "min": 1.0}`）检查控件可用；不可用 → `target_disabled`（阻塞·环境），不点击，证据在 `enabled_check` |
| `expect_text` | 后置条件另要求页面出现匹配该正则的文字（例如点地图后出现「收藏」才算弹出了车位卡片） |
| `skip_if_absent` | 本页没有匹配该正则的文字就跳过（记 `skipped`，不算失败），用于可选步骤 |
| `at` / `random_point` | 在目标容器可见区域内按比例取点 / 按种子随机取点（重试换点），不写像素 |
| `idempotent` | 只有字面 `true` 才申请重发；已发目标须明确为 navigation/dismiss_dialog。输入与受控副作用不可用此字段覆盖；返回/随机点选仅在后置未成立的可靠观测下保留重试，不允许丢回执后盲重发 |

`input` 的 `text` 可写 `${var:x}`（用例数据）/ `${param:x}`（流程参数）；来自 `{"secret": ...}` 的值只能输入 `secret: true` 的目标，尝试记录里只有 `input.{ref, secret, length}`，没有值。

**监视**：不下发动作，按间隔连续观测，`monitor.timeline[]` 记录每次采样的 `t`、状态、新出现的文字、`watch` 命中、中断、运动计时。结束条件依次为：进程退出/异常中断（`anomaly`）、阻塞类中断（`interrupted`）、`abort_on` 命中（`monitor_abort`）、运动超时、`until_state` / `until_text` / `until_text_gone`（须先出现过再消失）、`stop_on` 命中、到达时长。有 until 条件却到时仍未满足 → `postcondition_not_met`。

每次尝试记录三层结果，互不替代：

- `dispatch.sent`：命令已发出；
- `dispatch.executor_ok`：执行器返回成功（hdc 退出码恒为 0，所以同时检查输出中的 `[Fail]`、`error:`、`10106102` 等）；
- `post.met`：执行后重新观察，预期业务状态出现且无中断。

尝试状态：`ok`、`target_unresolved`、`target_ambiguous`、`target_partially_visible`、`policy_denied`、
`device_error`（附 `error_class`，含 `transport_unavailable`）、`observation_failed`（观测查询失败，归环境）、
`postcondition_not_met`、`interrupted`、`anomaly`、`unobservable`、`budget_exhausted`、`invalid_step`、
`target_disabled`（`require_enabled` 且控件置灰，阻塞·环境）、`monitor_abort`、`motion_timeout`（运动超过 `max_motion_s`）。
agent 模式的 `--point` 记录提出时的窗口尺寸（`point_window`）；执行前窗口尺寸变了（旋转/折叠/分屏）就丢弃该坐标。
定位记录 `locate.attempts[]` 列出每个策略的结果；`adapted=true` 表示首个尝试的策略失败、由后备策略成功。

**策略文字语境（#35）**：`check_tap` 检查最近可点击容器的各条 text/description。policy-0.6 仅对完整标签「被授权的车辆」「附近充电站」（允许外围空白）排除对应的授权/充电关键词误命中；同容器其他文字、其他规则、页面级 deny/effect 与目标显式副作用继续检查。混合标签、换行追加指令、充电站启动与充电费不按该例外放行。纯文字断言不经过动作门禁；切车仍须目标声明 vehicle_selection 且本次授权。该修复只缩窄既有配置，不改变 Policy / Executor / Dispatcher 接口。

**不确定页面与通用确认（#43）**：页面限制取 `active`、全窗 `candidates`、各栏候选与旧记录 `state` 的并集；
歧义仍是歧义，不为取规则制造 active。默认「确定/确认/OK」（可由 policy 的 `generic_confirmation_text` 配置）
的页面正向来源须由按钮所属范围的每个候选对每个确认标签给出同一唯一副作用；空规则、未匹配和效果冲突仍是未知，限制并集不受影响。
按当前点击容器的 text/description 判断，匹配时处理外围空白，证据保留原文字。
通用确认须有对实际按钮生效的页面/控件规则、同一最近 Dialog 中 `confirmation_contexts` 明确登记的首个非交互、非输入字段 Text 标题，
或当前同一 Dialog 完整命中已评审良性中断及其声明的关闭按钮。目标自填 side_effect/review/source、持有授权或
弹窗里的“算路中”等状态说明不单独证明语义。限制并集不作跨栏的正向来源；页面effect的正向依据须属于当前按钮所在栏。上下文不抵消硬禁止或多候选副作用冲突；
受控动作仍需目标声明与本次授权一致。没有可用树时，已知通用确认标签不能借模板免除语义核验。
无树时文本/description XPath及含括号表达式（包括函数）、变量、动态属性或attribute轴的XPath无法核验，不允许模板回退；普通ID字面定位仍按既有模板规则处理。字符串字面量内的括号或属性名称不作动态表达式。
无树时非精确文本选择器（exact=false）也不能借模板回退；精确文本导航及有树的部分文本定位保留，通用确认仍受当前语义检查。
生产候选 `qk_start_confirm` 也遵守当前来源门槛：已有小弹窗仍显示 `qk_route_result` 的「开始」时可按登记效果及授权执行；全窗弹窗遮住该签名且无独立来源时拒绝并交接。前一次 `qk_start`、`source_flow` 文本或运动授权不自动继承为当前确认的证明。真实布局/来源的支持仍需独立取证与评审，不从合成变体新增生产签名。
良性关闭来源还须绑定当前可点击控件：同一最近弹窗内 `dismiss_text` 按可点击容器去重后必须唯一；`dismiss_target` 从登记目标的当前树策略独立解析（含when/within/region/可见比例），坐标/模板回退不能替代绑定。实际触摸接收控件、定位容器和登记关闭容器必须相同。此来源只证明导航/关闭/只读，不能证明受控副作用。
Executor、act、探索候选和 Dispatcher 重检共用判定，重定位后从当前树重建来源；
输出 `confirmation_source` 或 `confirmation_semantics_unknown`。纯导航/安全停止仍可执行，停止容器混有确认词时拒绝。输入字段的值不作为确认按钮，嵌套弹窗与其他栏不提供正向来源。
系统弹窗原子能力继续使用既有登记、prefer/forbid 与前后复核。升级 policy-0.7 后旧绑定依赖的审核/重放须按现有机制复核，
Agent 不自动确认产品知识或批准生产资产。

允许的恢复（`recoveries[]`，追加记录，不覆盖首次失败）：
`reobserve_and_retry`（定位失败）、`dismiss_interrupt`（仅限 `class=benign` 且 `review=reviewed`）、
`redispatch` 只允许可靠未下发，或上述明确低风险幂等步骤，并受原有重试预算限制。已发/可能已发的非幂等动作、输入与受控副作用，遇超时、命令错误或动作后观测失败不重放原动作；错误名不证明未执行。

`clear.sent` 保留清空阶段的下发事实，后续输入未发不能将整步改成未发；聚合 clear 的 sent 采用三态（任一字面 true→true、全部字面 false→false、其余→null）。会话对未知发送事实保守计入预算，记录 `effect_unknown`。只有清空成功而未完成输入仍为部分执行，不整步清空重跑。

定位内滚动及弹窗处置的辅助物理下发也保留 sent；子动作已发失败或发送未知时，不能仅因父 dispatch 缺失就完整重试。Dispatcher 的动作预算对未知发送事实同样保守计数。恢复复核使用原 attempt 的 motion_mark，不重置运动超时/晚停的事实边界。

良性中断/已登记系统弹窗的原子处置继续可执行，随后重新核验原步骤完整后置条件。后置满足可恢复通过；不满足且原动作不可重放则停止原步骤，不以关闭弹窗代替原业务后置。首次异常、恢复动作与复核证据分别保留。

单次 act、最后一次尝试、flow/recipe 子步骤与 cleanup 同样保留部分执行/未知效果的人工交接信号。最终失败存在未知物理效果时，即使该动作本可安全幂等重试，也因重试已结束而保留 `effect_unknown/action_required`，不能让重试资格掩盖交接。`run` 的 verdict/report 将原子步骤交接列入 `step_recovery` alerts；安全停止警告优先。`anomaly`、未评审中断仍不自动恢复，未知车辆运动发送事实保守挂看门狗。

恢复手势在 `attempt.recovery_dispatches[]` 保留原始 `sent`、`ok` 及 `verified`；`verified=false` 且可能下发时，记录未知效果和人工交接并停止原动作重放。系统弹窗能力的 `dismiss.sent` 同样保留三态，不将未知压成 false。已确认良性关闭后，若原业务后置仍未成立，重新按原步骤的幂等与副作用规则判断剩余重试资格。

clear 与 input 均有可靠成功回执、只在可靠观测下原业务后置未成立时，保留产品失败和禁止重放，但不标记 `effect_unknown`；未完成序列或缺可靠下发/回执仍标记未知效果。执行器与会话共用同一物理证据判定。

恢复以 `after_attempt` 绑定原尝试；仅属于最后一次尝试的可靠原步骤后置可用于最终 `outcome/attribution/final_status/final_obs`，旧恢复不能覆盖后续新错误或观测。原始异常继续保存在 `attempt.status` 和 `first_failure`。例如覆盖物消失后原业务后置未成立，最终为产品 `fail_candidate`，而不是恢复前的工具 `unobservable` 阻塞。

Agent `diagnosis.status` 消费最终 `final_status`，并以匹配最后尝试和 `final_obs` 的恢复后置提供 `post/obs_id/recovery` 证据；不会引用旧恢复，成功恢复返回无失败诊断。可靠业务后置未达的诊断与产品 `fail_candidate` 一致，首次覆盖异常仅在历史失败记录中保留。

可靠下发及成功回执后的可观测 `anomaly`、已知晚停/停止失败等确定失败保留交接，不因此标记 `effect_unknown`；该字段只取未知物理证据、部分执行或明确不可判的观测（如无可用树、查询失败、中断下业务状态不可判、停止查询未知）。可靠已知状态上的中断不因此变成未知执行。执行器三条汇总/恢复路径共用此判断，不用“不是 postcondition_not_met”代替未知性。

步骤结果：`passed` / `passed_after_adaptation` / `passed_after_recovery` / `fail_candidate` / `blocked` / `not_run`。
关键步骤未通过，后续步骤记为 `not_run`，清理步骤仍执行。

**授权与运动安全**（`run.json`）：`authorized`（本次授予的副作用）、`supervision.{supervisor, supervised_granted, safety}`、`motion[]`（看门狗事件：`armed` 附触发步骤与控件；由 `moving_states` 观测挂上的记 `step: observed`、`label: state:<状态>`；`stopped_observed` 附状态与耗时）、`safety_stops[]`（触发原因、执行的安全停止步骤、`confirmed_stopped`；未确认时 `action_required` 要求人工接管）。安全停止记录独立于步骤结果，不覆盖首次失败。

**原文取证（#32 裁决）**：树、可见文字、设备错误、事件和运行汇总保留采集原文，不脱敏、不按系统窗口扣留。窗口表未知不代表整屏 dump 失败；`dump_error` 仅反映实际采集错误或设备不支持 dump。截图仍受 APP 白名单约束，白名单内 APP 相互切换时保留截图，但旧业务树按 `fresh=false` 失效。旧 `redacted`、`private_windows`、`system_overlays`、`clipboard_may_hold_secret` 字段及 `secret_typed` 事件不再生成；旧会话事件可以读取但不再控制取证。密钥的外置引用、缺失检测、输入目标授权及生成用例的外置参数分类保持；回放设备的输入值只在内存中，恢复状态仍沿用现有格式。

## 3. 判定（`assertions[]` 与 `verdict`）

断言类型：`state_is`、`text_present`、`text_absent`、`target_present`、`target_absent`、`target_text_equals`、
`node_selected`、`visual_review`（永远是建议），以及：`text_present_any`（任一文字出现）、`text_seen` / `state_seen`（在该步骤的监视时间线里出现过，步骤没有监视时看它的终态观测；`expected: false` 表示整个窗口内都不应出现）、`target_enabled`（按 `enabled` 与 `enabled_by` 判断；`absent_is: "disabled"` 表示不出现也算不可用）、`value_present`（`read` 读到的值匹配正则）。每条非建议断言必须有 `oracle_refs` 指向知识条目；未确认时结果为 `pending`，并给出 `observed_holds`（观测本身是否成立）。

| 断言结果 | 条件 |
|---|---|
| `pass` / `fail` | 证据确定 + 判据全部 confirmed 且适用于本次运行 |
| `pending` | 证据确定，但判据 unconfirmed / conflicted / missing / invalid / out_of_scope / applicability_unknown / not_oracle；或属主观检查 |
| `undeterminable` | 证据不足（无 UI 树时判断"有/无"、定位多匹配等）；文字断言只认可见矩形非空的节点（被列表裁掉的不算） |
| `not_run` | 关联步骤未成功（列入 `verdict.not_verified`）；引用的步骤不存在 → 该断言单独判阻塞 |

判据适用性：`knowledge_snapshot[].applicability` 把条目 `applies_to` 与 `run.json.facts`（APP、实测版本、屏幕形态、
`--env` 声明）逐项比较。事实缺失或版本描述不可比较（如"内部debug版 phone_fc78649"）→ `applicability_unknown`；
不相等 → `out_of_scope`。`operation` / `observation` 类条目不是判据（`not_oracle`）。

`env_requirements` 与判据使用同一组运行事实：`app` 来自被测 APP 定义，`app_version`、`device_form`
来自本次设备前置检查；这三项即使未知也不能用 `--env` 替代。版本/形态不符或必需实测值缺失时，
运行判环境阻塞，业务步骤与清理不执行，自动解锁和已登记弹窗关闭也不下发。解锁后重新实测并复核。
账号、车辆、园区及任务状态等当前适配器不能测量的业务条件，仍需现场人工确认后用 `--env` 声明。

普通 JSON 更新仍通过同目录临时文件原子替换。Windows 文件替换出现 5/32/33 时最多尝试 5 次，
累计等待 0.5 秒；持续失败仍报错并保留旧文件及待写内容。原始证据的独占创建不使用该重试，
目标已存在仍直接报错；文件替换重试不触发设备动作重发。
知识条目状态只允许 `unconfirmed` / `confirmed` / `deprecated`；未知、缺失或非字符串状态派生为
判据 `invalid`，废弃条目的判据为 `missing`。`superseded_by` 只记录历史替代关系，不是可写入的知识状态。

用例结论优先级：前置检查/环境要求不满足 → **阻塞（环境）**；未确认的安全停止 → **阻塞（环境）** 且
`action_required`、`alerts[]` 非空（CLI 退出码 2），产品失败仍在 findings 与 `product_failed`；有失败（产品）→ **失败**；
有阻塞（含必需清理未完成）→ **阻塞**；有待确认/不可判定，或没有任何断言得到确定判定 → **待确认**；否则 **通过**。
`verdict.failure_signature` 是失败项的稳定特征，用于判断已知缺陷是否按同一方式复现（`known_issue.reproduced`）。
步骤出现崩溃或异常中断时用通用不变量 `K-GEN-001` 判定，不依赖该步骤业务判据是否已确认。

## 4. 探索资产审核与运行准入（#30）

生成资产新增 `origin.type=session/exploration`；旧资产按 status、sources、history 与主步骤/cleanup/safe_stop
的 from_session/from_transition 联合识别。仅手改 status 或已有 reviewer 来源不解除审核要求。
`case approve` 为旧探索资产补持久 origin；不会自动审核现有资产。

- 用例 `asset_review`：schema=1、非空 reviewer/basis、带时区 ISO ts、binding。
- binding：case_id、原 content_hash/dependency_hash、review_hash、policy_hash、engine_hash、context_hash。
  review_hash 排除 status/history/replays/product_status/asset_review，其他字段均绑定；原重放指纹算法保持。
- `run.json.asset_review`：required、valid、state（approved/stale/pending/not_required）、origin_signals、
  current、record、use（single/batch）。这是执行前快照，历史运行不按未来配置重算；CLI 与报告展示审核状态。
- `require_review`：探索来源未有效审核、或批量用例状态不在合法生命周期中时，以 PermissionError 拒绝。
  suite/经验验证先预检全部成员，每次 run 在 RunStore/device/preflight 前再复检；其他授权与判定门槛保持。
- `informal`：status 不为 regression，或探索来源无有效审核。单条诊断可以运行，不能从 lint 或重放推出人审完成。

policy、layout 与 dialogs 使用本次运行准入检查时捕获的配置，Observer、preflight 弹窗关闭与恢复诊断共用该弹窗表。
引擎源码与模板按调用入口指纹核对，运行中外部修改尚无文件锁保障。reviewer 为本地声明身份，不是认证或签名。

## 5. 会话（`runs/session-<name>/`）与会话生成的用例

- `events.jsonl`：`session_facts`（版本/形态/验证级别，只记一次）、`agent_observe`、`act_request`（先于执行写入）、
  `act_rejected` / `act_error` / `act_result`（`outcome`、`dispatched`、`effect_unknown`、前后观测），以及执行器内部事件
  （`attempt`、`revalidate`、`dispatch`、`clear_text`、`scroll_search`、`recovery`、`foreground_changed`、
  `state_indeterminate_before_dispatch`…）。有请求无结果 = 中断。
- 会话只经 `session.trail()` 读取：它核对 `session_open` 身份（act/observe 核对 APP/设备/验证级别，from-session 核对 `--app`），
  并以请求与结束事件之间的 `dispatch`/`clear_text`/`scroll_search` 判定是否已下发。`act_error` 在下发后出现 = 效果未知的步骤；
  只有没下发的 `act_rejected`/`act_error` 才进 `rejected`。有请求无结果一律按效果未知处理。
- 设备查询三态（True/False/None）：锁屏、前台查询失败（None）时不发任何手势或按键，判 `state_indeterminate`（环境阻塞、需人工确认）；
  `unlock` 只在明确锁屏时唤醒/上滑/输 PIN，否则返回 `indeterminate`。`clear_text` 的 MoveEnd 失败即中止、不删字。
- `steps.jsonl`：每个动作的完整执行记录；`attempts[].resolved` 记解析出的节点、bounds、可见比例、实际点击点、
  窗口 px、layout key、密度、形态，`attempts[].revalidate` 记下发前复核结果。
- 会话生成的用例：每步 `from_session`（会话、序号、结果、`effect_unknown`、前后观测）；内联目标 `observed`（解析时的节点、
  形态、版本、验证级别）；`sources[0]` 列出 `included` / `excluded`（带理由）/ `rejected`（均未下发）/ `incomplete` /
  `effect_unknown`。lint 对 `rejected` 里 `dispatched: true` 的记录和未写 `effect_reviewed` 的效果未知步骤报错。
- 用例成熟度（`status`）与产品状态（`product_status`：`conforming` / `known_failure` + `issue` + `signature`）分开记录：
  当前内容哈希下至少 2 次确定且一致的重放才能晋级；待确认/阻塞的重放不计入并列在 `ignored`；通过与失败混合 → 不稳定。
  内容哈希在用例声明时包含 `authorizations` 与 `safety`。


### 未确认停止后的运行暂停

`safe_stop` 未确认停止时，运行上下文登记 `safety_hold`（触发、时间、原因），本次运行不解除。
下发关口拒绝所有普通动作，返回 `safety_hold` 并记录未下发的 dispatch；逻辑步骤也在观测、定位与展开前复用该判定。
只有明确继承 `_safety` 的停止步骤可继续执行，状态及 policy 检查仍保留。
清理步骤记为 `not_run`，原因为“安全停止未确认，清理暂停，需人工接管”；`cleanup_done` 仍写入。
判定及报告将这些清理列为尚未验证，而不是清理失败，安全停止 alert 保留人工接管信号。

lint 共用 `iter_case_steps` 遍历主步骤、cleanup、safe_stop 和引用的 flow/recipe 子步骤。
`tap` 与 `input` 都必须引用已登记目标，内联目标仍须满足现场选择器来源要求；像素坐标、未声明终点及缺失 recipe 的检查适用于全部来源。
问题字段保留来源路径，如 `cleanup.C2`、`safe_stop.X1`、`steps.F1→flow:...X1`；递归引用会报错，不无限展开。
