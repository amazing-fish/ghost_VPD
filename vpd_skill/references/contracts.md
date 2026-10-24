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

`ensure_state` 已处于目标且无中断时直接复用；否则执行 recipe，并自动追加一次
`wait(duration_s=0, expect_state=目标, timeout_s=0, retry=0)` 验证。recipe 作者只声明到达路径，
无需手写这条末尾检查；动作自身的后置等待仍保留。验证复用既有条件、观测、中断处置和结果契约，
关键子步骤失败、未知或交接时不继续。目标未到达不能完成，操作完成也不代替独立产品断言。
父 `when` 也覆盖自动终态验证：若其正证只存在于起始页，离页后未知仍为 pending。
只约束一次导航的条件应放在该导航步骤上，不将它声明为整个能力的持续条件。

`act swipe` 不是定点拖动：当前明确拒绝 `--point`、`--basis`、`--duration`。
`act` 的 `target_obscured` 诊断回传最后一次下发复核中的 `blocker`、`expected` 和关联 `obs_id`；无该次证据时字段为空，不从旧尝试补造遮挡结论。诊断仅解释拒发，不改变触摸归属或授权检查。
CLI 参数错误在会话锁、设备初始化和信息查询前拒绝，不创建会话，也不追加请求/拒绝事件。
直接 step/flow 中的 `point`、`basis`、`point_window`、`point_basis`、`duration`、`duration_s` 同样在观测、解锁和动作前拒绝为
`invalid_step`（工具阻塞），lint 也报告定义错误。旧会话曾接受这些参数并不证明手势按提议执行，
`case from-session` 拒绝将其静默固化为普通滚动；需重新采样或通过已有 `--drop A序号=理由` 显式排除。
不带这些参数的容器滚动、`within` 与既有随机地图滑动继续沿原路径执行。
`--duration` 的单位为秒，仅用于 wait/monitor；设备 `uitest uiInput swipe` 的末参数为速度 px/s，
不是时长，参见 [OpenHarmony 参数说明](https://github.com/openharmony/docs/blob/master/zh-cn/application-dev/application-test/uitest-guidelines.md)。

前台查询返回未知（`None`）时，`back`/`home` 按 `state_indeterminate` 停下，即使查询命令成功也不下发。
已识别的桌面允许导航；真机 `aa dump` 没有前台 mission 不能作为桌面证据，当前适配器保持返回未知，
直到有未锁屏桌面及系统层窗口的样本支持正面识别。

动作预算和运动超时在统一下发关口中复核，关闭弹窗、滚动查找、解锁及启动/停止 APP 都使用本次运行的同一计数。
只有实际下发才计数；清空输入框逐个点击/按键计数，其外层 `dispatch.aggregate=true` 仅汇总结果，不重复计数。
`_safety` 步骤及其子动作豁免预算和运动超时，状态、触摸归属与 policy 检查仍生效。
独立 `unlock` CLI 无运行上下文，dispatch 的预算/运动检查记为 `applicable=false`；run preflight 和动作恢复使用运行上下文。
PIN 结果的 `entered_digits` 仅累计 `sent is True` 的可靠下发数；`pin_entered` 表示至少一位已下发或可能下发。首位回执 `sent=None` 也保留设备防重入记录，下一次不再发唤醒、上滑或 PIN；首位明确 `sent=False` 且此前无可能输入时才允许重新尝试。PIN 值和数字序列不写入记录。
锁屏键盘默认只按数字 `text` / `originalText` 识别。无标签 Canvas 键盘仅允许在执行源码所在的
`vpd_skill/config.local.json` 按精确设备显式配置，示例：

```json
{"unlock_pin_layouts": {"hdc:<exact-serial>": "standard_3x4_blank"}}
```

默认 `unlock_pin_layouts={}`，CLI `--device` 优先于配置默认设备；其他设备、通配符、`hdc:` 自动发现、Replay、
未知布局值或非对象配置均不开启几何识别。已有完整数字标签优先；部分/冲突标签识别失败后，整屏仍有可见单数字
`text` / `originalText` 时不得降级为几何。几何路径只接受整树恰好12个可点格：键及子树的
`text/originalText/description/id` 全空，键和完整祖先链可见且启用，根节点给出完整窗口 bounds；
全部键完整位于当前可见区、等大、无重叠、3列×4行等间距，中心实际触摸接收者属于该键。
只映射前三行1–9及末行中心0，底部两角不作为数字。无法确认时零次 PIN 输入并交接，不猜坐标。
识别成功的解锁 `evidence` 增加 `stage=pin_pad`、`pad_source=labels|configured_geometry`，不输出数字坐标表；
每次查询先清空来源，采集/解析错误沿既有 `execution_error` 保留。该配置只声明设备布局，不改变每位锁屏复核、
Dispatcher、secret映射及防重入记录。当前只有合成树验证，OH7现场兼容性仍需工作机单次受控复验。
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

`input.dismiss_obstruction_when` 可显式声明 `{"condition": "已登记菜单条件", "equals": true}`，仅支持当前观测，禁止历史 `at`；条件正证须为指向实际遮挡容器的 `target_present`，反证仍须独立定义。仅在 `target_obscured` 且 clear/input 可靠未下发、其余辅助动作无未知效果时，执行一次继承父条件及身份范围的受控 back。关闭前核对当前真实接收者恰为该容器；关闭后条件明确为 false 才重新观察、定位并重试原输入一次。`retry:0` 保持普通重试关闭，显式菜单恢复有独立的一次额度；该恢复用后不会再普通重试。无菜单不额外动作；未知菜单、父条件变化、清空已下发、关闭未证实或任一发送未知均停止，不以 `@in_app` 当作输入完成。首次失败、`recoveries[].attempt/condition_after` 与 `attempt.recovery_dispatches` 保留，只有重试原输入成功才 `passed_after_recovery`。lint、Runner 前置定义检查和依赖指纹共用该字段。

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

树策略发现目标被裁时，定位尝试与 `diagnosis` 的 `clipped_candidates` 最多保留5个候选，
包含节点、可见矩形/比例、窗口和裁剪祖先链。`window_source=ui_tree_bounds` 明确表示窗口仍由
当前树的包围盒推导，不是独立的实际窗口证明。子节点越出祖先也可能是正常滚动/翻页，诊断不会自动扩大裁剪区域。

有可用树的坐标点击若没有可见节点、实际容器退到根节点，或落在没有自身文字/描述且没有可点击祖先的
非交互布局容器上，返回 `policy_denied`、`deny.class=no_receiver_at_point`；`point_evidence` 给出
落点、命中节点及最多5个落点内被裁候选。整页子树文字不再作为该空白落点的副作用依据。
此诊断不把候选变为可点目标；已登记 Canvas/XComponent 的正常树定位仍沿现有定位和触摸复核。
若Dispatcher重定位后的最终策略拒绝，CLI诊断读取该次 `dispatch_recheck.checks.tap.policy`，
初检 `attempt.policy` 仍保留作独立审计；不会用初检通过的旧策略掩盖最终接收者拒发。

折叠布局疑似树窗口/祖先包围盒错误时，工作机先保留同一 `obs_id` 的原树、截图、`layout.window_rect`
与本次诊断，核对候选的裁剪祖先和实际按钮位置。若要扩大可见区域，仍需该帧独立窗口/布局证据与
List/Scroll/Grid、隐藏Swiper页及真实浮层负例；本诊断切片不声称修复右缘按钮或完成真机验收。
定位记录 `locate.attempts[]` 列出每个策略的结果；`adapted=true` 表示首个尝试的策略失败、由后备策略成功。

**策略文字语境（#35）**：`check_tap` 检查最近可点击容器的各条 text/description。policy-0.6 仅对完整标签「被授权的车辆」「附近充电站」（允许外围空白）排除对应的授权/充电关键词误命中；同容器其他文字、其他规则、页面级 deny/effect 与目标显式副作用继续检查。混合标签、换行追加指令、充电站启动与充电费不按该例外放行。纯文字断言不经过动作门禁；切车仍须目标声明 vehicle_selection 且本次授权。该修复只缩窄既有配置，不改变 Policy / Executor / Dispatcher 接口。

**搜索筛选语境（#84，policy-0.9）**：`qk_spot_search` 用既有 `effect_text` 精确登记「充电桩」为
navigation，并用 `navigation_context_target` 引用既有搜索输入目标。navigation映射仅作为正向分类候选，不加入页面硬限制；
未取得正向证据时仍由原车控关键词拒绝，不报“navigation被禁止”。状态签名须在当前树重核，登记输入框须唯一、
可见且位于同一栏；未知/歧义页面、中断或模态层不产生该正向来源。既有 `confirmation_sources` 通道同时承载这类
页面动作语义，在 Executor 与 Dispatcher 处各自重新计算。Policy 还要求「充电桩」「电梯口」各有唯一可见可点击
接收者，同属于一个非root、非可点击且不含搜索输入框的纯双标签组；组与输入框位于同一栏，组内不能有编辑字段、
额外无字接收者或嵌套点击控件。实际落点须由该组内「充电桩」容器接收，不能借背景搜索框放行同名非模态悬浮按钮。
满足这些结构证据时才覆盖带稳定 ID 的全局车控关键词误分类。其他全局规则和页面级限制不豁免；
结果记录 `navigation_source` 的状态、搜索输入、筛选组与接收者索引，命中记录区分 policy/state 及规则ID。
注册目标的自填字段、单独页面名称或全页存在搜索文案不能代替这些证据。该离线修复不确认标签筛选产品规则，
实际设备的完整父链未随Issue提供，本实现只支持上述可核结构契约，不声称真实布局已校准；不满足时继续拒发并现场核对。
修改后旧审核/重放按既有 policy/engine/context 绑定重新核对。

**不确定页面与通用确认（#43）**：页面限制取 `active`、全窗 `candidates`、各栏候选与旧记录 `state` 的并集；
歧义仍是歧义，不为取规则制造 active。默认「确定/确认/OK」（可由 policy 的 `generic_confirmation_text` 配置）
的页面正向来源须由按钮所属范围的每个候选对每个确认标签给出同一唯一副作用；空规则、未匹配和效果冲突仍是未知，限制并集不受影响。
按当前点击容器的 text/description 判断，匹配时处理外围空白，证据保留原文字。
通用确认须有对实际按钮生效的页面/控件规则、同一最近 Dialog 中 `confirmation_contexts` 明确登记的首个非交互、非输入字段 Text 标题，
或当前同一 Dialog 完整命中已评审良性中断及其声明的关闭按钮。目标自填 side_effect/review/source、持有授权或
弹窗里的“算路中”等状态说明不单独证明语义。限制并集不作跨栏的正向来源；页面effect的正向依据须属于当前按钮所在栏。上下文不抵消硬禁止或多候选副作用冲突；
受控动作仍需目标声明与实际许可类别一致（任务策略许可或显式授权）。没有可用树时，已知通用确认标签不能借模板免除语义核验。
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

**清空后非提示文字（#91）**：clear的成功回执不证明字段值为空。追加input前，以及Dispatcher因遮挡/解锁刷新树后，
复用同一known-only检查：当前唯一、可见的直接TextInput/TextArea/SearchField须有显式字符串`text`和独立
`hint`/`placeholder`（显式空字符串有效）；仅当非空text与所有提示不同、无冲突originalText或可识别的自定义/聚合来源时，
以`clear_not_confirmed`（error/tool）拒绝追加并交接。clear原回执、部分执行及禁止重放继续保留，不追加删除或Back。
`originalText`缺失不参与比较；只要显式提供，其类型异常或与text不相等（含空字符串对非空text）均为来源冲突，保持unknown。
`clear.value_checks`保留每次当前obs/节点/来源、长度和三态；不复制文本或其hash。提示、缺属性、冲突、聚合和不可观测为unknown，
保持原输入契约；secret/掩码不读回。显式空text也不输出verified=true，本检查不承诺所有设备/字段都已清空；实际现场hint来源仍需校准。

定位内滚动及弹窗处置的辅助物理下发也保留 sent；子动作已发失败或发送未知时，不能仅因父 dispatch 缺失就完整重试。Dispatcher 的动作预算对未知发送事实同样保守计数。恢复复核使用原 attempt 的 motion_mark，不重置运动超时/晚停的事实边界。

良性中断/已登记系统弹窗的原子处置继续可执行，随后重新核验原步骤完整后置条件。后置满足可恢复通过；不满足且原动作不可重放则停止原步骤，不以关闭弹窗代替原业务后置。首次异常、恢复动作与复核证据分别保留。

单次 act、最后一次尝试、flow/recipe 子步骤与 cleanup 同样保留部分执行/未知效果的人工交接信号。最终失败存在未知物理效果时，即使该动作本可安全幂等重试，也因重试已结束而保留 `effect_unknown/action_required`，不能让重试资格掩盖交接。`run` 的 verdict/report 将原子步骤交接列入 `step_recovery` alerts；安全停止警告优先。`anomaly`、未评审中断仍不自动恢复，未知车辆运动发送事实保守挂看门狗。

恢复手势在 `attempt.recovery_dispatches[]` 保留原始 `sent`、`ok` 及 `verified`；`verified=false` 且可能下发时，记录未知效果和人工交接并停止原动作重放。系统弹窗能力的 `dismiss.sent` 同样保留三态，不将未知压成 false。已确认良性关闭后，若原业务后置仍未成立，重新按原步骤的幂等与副作用规则判断剩余重试资格。

系统弹窗的 `dismiss.back_if_still_present=true` 显式允许安全按钮可靠完成后的一次受控后退；默认不启用。点击后及紧邻后退前均须确认同一标题与唯一登记窗口，且点击前、点击后、后退前完整窗口集合稳定；后退仍经 Dispatcher 的预算、Hold 与已解锁检查。独立整屏树和原窗口消失证据不足时不报关闭成功。`dismiss` 保留首按钮兼容回执，`dispatches[]` 按顺序保留按钮和后退的三态回执及原始错误；Runner 前置发生处置尝试但未确认关闭时停止，不启动业务后再次处置。未确认关闭且可能下发时，`action_required` / `effect_unknown` 沿 sysdiag、run、suite 传播，CLI 退出码为 2；明确采集/执行错误保留阶段与原始错误，归入 error。

clear 与 input 均有可靠成功回执、只在可靠观测下原业务后置未成立时，保留产品失败和禁止重放，但不标记 `effect_unknown`；未完成序列或缺可靠下发/回执仍标记未知效果。执行器与会话共用同一物理证据判定。

恢复以 `after_attempt` 绑定原尝试；仅属于最后一次尝试的可靠原步骤后置可用于最终 `outcome/attribution/final_status/final_obs`，旧恢复不能覆盖后续新错误或观测。原始异常继续保存在 `attempt.status` 和 `first_failure`。例如覆盖物消失后原业务后置未成立，最终为产品 `fail_candidate`，而不是恢复前的工具 `unobservable` 阻塞。

Agent `diagnosis.status` 消费最终 `final_status`，并以匹配最后尝试和 `final_obs` 的恢复后置提供 `post/obs_id/recovery` 证据；不会引用旧恢复，成功恢复返回无失败诊断。可靠业务后置未达的诊断与产品 `fail_candidate` 一致，首次覆盖异常仅在历史失败记录中保留。

已登记 `expect_state` 的后置失败附 `state_diagnostics`（`diag_only=true`）：保留期望状态的声明签名、
当前状态与候选、观测ID、树状态/路径，并仅核对 `require_text` 在当前整窗可见文本里的已见/未见项。
它不重做完整签名匹配；pattern、selected、forbid及分栏等仍由既有状态识别负责，声明快照只供核对。
无可用树、明确采集错误或过时观测时 `available=false`，`present/missing=null`，不能写成确定全缺。
未声明 `require_text` 时列表为空，不产生“全部锚点缺失”的结论；特殊 `@in_app` 与未登记状态不制造签名。
最终诊断优先取绑定当前尝试的恢复后置，首次诊断另留 `first_failure`；恢复成功不残留当前失败诊断。
结构化证据进入最终步骤及 finding.evidence，报告沿既有 reason 显示简短诊断与观测引用。
缺锚点只说明当前识别证据，不证明车不在支持园区、距离超限或其他环境原因；unknown/undetermined也不是独立环境反证。
该诊断不改变原归因、Oracle、接管、重放与产品结论；环境阻塞仍需独立且当前的前置证据。

可靠下发及成功回执后的可观测 `anomaly`、已知晚停/停止失败等确定失败保留交接，不因此标记 `effect_unknown`；该字段只取未知物理证据、部分执行或明确不可判的观测（如无可用树、查询失败、中断下业务状态不可判、停止查询未知）。可靠已知状态上的中断不因此变成未知执行。执行器三条汇总/恢复路径共用此判断，不用“不是 postcondition_not_met”代替未知性。

步骤结果：`passed` / `passed_after_adaptation` / `passed_after_recovery` / `fail_candidate` / `error` / `blocked` / `pending` / `skipped`（操作条件明确不满足，未触发）/ `not_run`。
关键步骤未通过，后续步骤记为 `not_run`，清理步骤仍执行。

**授权与运动安全**（`run.json`）：`authorized`（本次授予的副作用）、`supervision.{supervisor, supervised_granted, safety}`、`motion[]`（看门狗事件：`armed` 附触发步骤与控件；由 `moving_states` 观测挂上的记 `step: observed`、`label: state:<状态>`；`stopped_observed` 附状态与耗时）、`safety_stops[]`（触发原因、执行的安全停止步骤、`confirmed_stopped`；未确认时 `action_required` 要求人工接管）。安全停止记录独立于步骤结果，不覆盖首次失败。

按 #49 用户裁决，`task_side_effects` 的 vpd_entry / route_planning 随已委派测试任务许可，无需逐次询问或显式 grant。
任务点击及任务类别输入还须取得当前 UI 的精确动作标签（task_action_labels）或具体已登记确认来源；目标声明、策略 label 或无树模板本身不是依据。
宽泛 deny_text 命中只施加限制，不证明任务动作；带任务关键词的帮助/取消/记录/状态或混合控件不按关键词自动放行。
直接任务许可核对完整接收控件的动作标签；隐藏文字和编辑子树不能借用。复合控件含未分类文字时保持未知，不能为通过测试扩充生产允许范围；正例简化仅在临时合成世界中进行。
可编辑值也不是任务动作依据；没有独立动作语义的 task 类输入同样拒绝，注册目标不能跳过此检查。
无可核验语义时返回 task_semantics_unverified，不下发，也不改问重复授权。沿途找车位/默认车位按精确算路标签分类；不提升生产定位资产状态。
`authorized` 包括这两类任务许可与显式授予；`authorization_sources` 及 authorization事件的 `grant_sources`
逐项标识 `task_policy` / `explicit`。旧 `--authorize vpd_entry route_planning` 写法可用，但不增加权限。
其他 gated/supervised 与硬禁止保持；task类别不能与它们重叠。目标副作用与实际文本必须一致，未知通用确认和混合运动/车控容器继续拒绝。

**原文取证（#32 裁决）**：树、可见文字、设备错误、事件和运行汇总保留采集原文，不脱敏、不按系统窗口扣留。窗口表未知不代表整屏 dump 失败；`dump_error` 仅反映实际采集错误或设备不支持 dump。截图仍受 APP 白名单约束，白名单内 APP 相互切换时保留截图，但旧业务树按 `fresh=false` 失效。旧 `redacted`、`private_windows`、`system_overlays`、`clipboard_may_hold_secret` 字段及 `secret_typed` 事件不再生成；旧会话事件可以读取但不再控制取证。密钥的外置引用、缺失检测、输入目标授权及生成用例的外置参数分类保持；回放设备的输入值只在内存中，恢复状态仍沿用现有格式。

## 3. 判定（`assertions[]` 与 `verdict`）

断言类型：`state_is`、`text_present`、`text_absent`、`target_present`、`target_absent`、`target_text_equals`、
`node_selected`、`visual_review`（永远是建议），以及：`text_present_any`（任一文字出现）、`text_seen` / `state_seen`（在该步骤的监视时间线里出现过，步骤没有监视时看它的终态观测；`expected: false` 表示整个窗口内都不应出现）、`target_enabled`（按 `enabled` 与 `enabled_by` 判断；`absent_is: "disabled"` 表示不出现也算不可用）、`value_present`（`read` 读到的值匹配正则）。每条非建议断言必须有 `oracle_refs` 指向知识条目；未确认时结果为 `pending`，并给出 `observed_holds`（观测本身是否成立）。

| 断言结果 | 条件 |
|---|---|
| `pass` / `fail` | 证据确定 + 判据全部 confirmed 且适用于本次运行 |
| `pending` | 证据确定，但判据 unconfirmed / conflicted / missing / invalid / out_of_scope / applicability_unknown / not_oracle；或属主观检查 |
| `error` | 所需观测有明确的采集/解析/查询错误；附工具或环境归因、阶段及原始证据。截图失败不影响可用的文字/状态证据 |
| `undeterminable` | 证据不足（无 UI 树时判断"有/无"、定位多匹配等）；文字断言只认可见矩形非空的节点（被列表裁掉的不算） |
| `not_applicable` | 当前有效、独立的 guard 明确 false，不计通过/失败或覆盖 |
| `not_run` | 关联步骤未成功（列入 `verdict.not_verified`）；引用的步骤不存在 → 用例定义错误，汇总为 error/tool |

判据适用性：`knowledge_snapshot[].applicability` 把条目 `applies_to` 与产品判据绑定的 `run.json.oracle_facts`逐项比较；
不使用含业务声明的调度 `run.json.facts`。真机当前仅绑定实测APP、版本和形态；独立条件证据另沿既有guard链取证。事实缺失或版本描述不可比较（如"内部debug版 phone_fc78649"）→ `applicability_unknown`；
不相等 → `out_of_scope`。`operation` / `observation` 类条目不是判据（`not_oracle`）。

`env_requirements` 消费调度 `run.facts`，产品判据消费独立绑定的 `run.oracle_facts`，两者不能混用。
共同的实测基础中，`app` 来自被测APP定义，`app_version`、`device_form`
来自本次设备前置检查；这三项即使未知也不能用 `--env` 替代。版本/形态不符或必需实测值缺失时，
运行判环境阻塞，业务步骤与清理不执行，自动解锁和已登记弹窗关闭也不下发。解锁后重新实测并复核。
`layout_requirements`（宽窄屏、方向、折叠状态等）在同一恢复前门槛中按实测布局检查，解锁后也重新计算；
不能因为没有同时声明 `env_requirements` 而跳过。
账号、车辆、园区及任务等业务 `--env` 仅满足旧整例调度门禁，不自动进入真机 `oracle_facts`。
`run.facts` / `env.facts` 保留旧调度投影，`run.declarations` 明示来源与 measured=false；产品 Oracle 另绑定实测 `oracle_facts`。
Replay world 的 env_facts 只是标明 offline_replay 的合成环境，不代表真机取证。

普通 JSON 更新仍通过同目录临时文件原子替换。Windows 文件替换出现 5/32/33 时最多尝试 5 次，
累计等待 0.5 秒；持续失败仍报错并保留旧文件及待写内容。原始证据的独占创建不使用该重试，
目标已存在仍直接报错；文件替换重试不触发设备动作重发。
知识条目状态只允许 `unconfirmed` / `confirmed` / `deprecated`；未知、缺失或非字符串状态派生为
判据 `invalid`，废弃条目的判据为 `missing`。`superseded_by` 只记录历史替代关系，不是可写入的知识状态。

#56 第一切片新增执行错误口径：设备命令失败、明确的采集/解析/查询失败、停止能力未完成记 `error`，
归因 `tool/environment`，保留 `stage/error_class/error/evidence`。设备回执失败保留完整 `dispatch.raw`；
前置查询错误不伪装锁屏并触发 PIN 恢复。空树、过时、前台未知但无查询错误仍是证据不足或流程受阻；
策略拒绝、预算耗尽、安全暂停、等待外部输入不自动变成 error。页面“加载失败”等文字仍由产品判据评价。
自动设备发现失败保留 list targets 原始错误；成功返回空列表才表示没有设备。pidof 明确失败输出不能当成存活证据，
正常空输出仍可表示进程未运行。显示/形态取证的明确失败进入前置 execution_error，形态未知不能推成直板；
正常属性不存在不算工具错误。设备名称、型号等附加元数据查询失败仅保留 query_errors，不单独阻止运行。
运行中的显示重取失败附在观测 display_error 中，不抹除独立有效的 UI 树与产品断言证据。
同次观测的前台查询错误按阶段留在 foreground_query_errors，末查成功不补回先前缺失的证据；新观测独立记录。
失败截图的部分文件仍按白名单删除，原错保留在 screenshot.capture_error；正常扣留本身不变成执行错误。
解锁必需的尺寸、键盘采集/解析错误沿既有 execution_error 留证；有效树未识别键盘仍交接，不增加 PIN 权限。
锁屏查询重试耗尽且仍未知时保留本轮明确错误；获得确定状态即清除，新一轮查询不继承旧错。
已恢复的错误留在首次异常和原始 attempts，不将已成功恢复的步骤重新标为 error。

`verdict` 保留兼容摘要，新增以下投影，均从现有步骤、断言与 findings 生成：

- `execution.status=completed/blocked/error`；`execution.errors[]` 递归保留最终执行错误，包括非关键流程子步骤与可选清理。父级复制摘要不重复计数；已恢复错误不重新升级。
  advisory 只抑制产品失败，不丢弃其所需证据的采集错误；产品与执行投影仍独立。
  visual_review 所需截图明确采集失败也记 error；withheld/skipped/缺失仍待确认。非视觉断言可继续使用独立有效的树证据。
- `product_verdict=passed/failed/pending/not_applicable`；`product_failed` 与产品 `failure_signature` 继续独立保留。
- 未确认安全停止仍优先阻塞并要求人工接管；否则 error 优先于产品 failed、blocked、pending，最后才可能 passed。
  产品失败与执行错误并存时，摘要为 error，产品失败及两类证据均保留。无确定非建议断言不能通过。

`run/suite --fail-on error` 可配置退出码 1；人工接管仍优先退出码 2，定义/程序异常仍退出码 3。
suite 摘要及 Console 接受 error；经验验证不对 error 做产品结论排序，未解决错误、错误转 pending 或
同为 error 但少适配都不能验证通过。可靠的 error→passed 才可计改善。晋级只计无执行错误的确定重放。
新依赖指纹包含 `result_contract=7`，旧重放需按新口径重新积累；旧无条件内容哈希不变；新 conditions 内容与外部条件定义/目标/规则前置均绑定指纹，历史原始 run/verdict 不改写。
探索资产现有 engine_hash 也会因引擎变更使审核失效，仍须由人重新审核，Agent 不自动批准。

复合步骤按真实子结果判定：`critical` 只决定失败后是否继续执行，不抑制已发生的产品失败候选、待确认或阻塞。
flow/recipe 汇总完成不能消除非关键子步骤的未恢复结果；已恢复的历史失败仍只留在 attempts/first_failure。
子步骤显式 `oracle_refs` 优先；没有子判据时保留最近父调用的判据上下文，无任何判据仍 pending。
继承规则的条件也绑定子步骤自己的 `final_obs`，不能借父流程后续成功的观测证明先前场景成立。
父级直接传播失败时记录 `failure_source`；只有该来源确为后代且 outcome/final_status/final_obs 一致时去重，
父级独立终态失败仍单独保留。findings 记录真实子步骤、观测、父级路径与判据来源，知识快照包含嵌套步骤引用。
必需清理内的未恢复子失败阻止整体通过；外层 `required=false` 仍表示可选清理，内部缺省值不将其变为必需，
明确执行错误始终独立保留，既有嵌套未知分支也保留 pending，不因清理可选而消除。
通用崩溃不变量仅用于真正发生 anomaly 的节点，不能传播为同一流程其他子步骤的产品判据。
结果契约 6 使早期因复合包装丢失失败或错用父判据的通过记录不能用于新依赖晋级；
契约 7 进一步要求 `ensure_state` 实际到达目标，不能沿用仅完成 recipe 的旧通过记录。
历史报告继续按原始记录展示，不重新计算或改写既有 raw run/verdict。

步骤崩溃/异常中断仅在归因为 product 时引用通用不变量 `K-GEN-001`；工具/环境候选不经产品 Oracle 转为失败。
`knowledge.applicability` 与 confirmed 门槛保持；引用判据不适用仍 pending，不能据此把业务断言改成不涉及。

### 断言前置与操作选支（#56 第二切片）

三个职责互不代替：`step.when` 决定是否操作；`assertion.guard` 决定产品断言是否适用；
现有断言及独立 `oracle_refs` 评价预先定义的响应契约。命中 `expect_state`/monitor 终态只代表步骤后置满足，不能自动得到产品 passed。

条件定义放 `case.conditions` 或 APP 的 `app.json.conditions`（同名 case 定义覆盖 APP，仍受内容/依赖指纹及资产人审绑定）。
下面是**合成示例**，目标与文案不注册到生产：

```json
{
  "conditions": {
    "vehicle_fault": {
      "kind": "task",
      "true": {"type": "text_present", "text": "合成独立诊断：故障"},
      "false": {"type": "text_present", "text": "合成独立诊断：健康"},
      "scope": {"vehicle": "synthetic_vehicle_identity", "task": "synthetic_task_identity"}
    }
  },
  "assertions": [
    {"id": "A-common", "at": "S5", "type": "text_present", "text": "合成任务可见", "oracle_refs": ["K-SYNTH-TASK"]},
    {"id": "A-fault", "at": "S5", "guard": {"condition": "vehicle_fault", "equals": true},
     "type": "target_enabled", "target": "synthetic_start", "expected": false, "oracle_refs": ["K-SYNTH-FAULT"]}
  ]
}
```

- `kind=page/account/vehicle/task`。APP/build、设备、观测编号与采集时间随观测记录；account 必需当前账户身份目标，vehicle 必需车辆身份目标，task 必需车辆与任务身份目标。
  身份目标必须在当前树唯一定位并读到非空可见 text，`--env` 的身份声明不能填补。生产已有条件文字不会被自动解析为读取器。
- `true` / `false` 各为独立肯定型 `text_present` / `text_present_any` / `target_present` / `state_is` 检查。
  正证命中且反证未命中为 true，反证命中且正证未命中为 false；两者都命中为冲突，都未命中为未知；历史来源即使同 epoch，也不能覆盖当前观测明确相反的独立证据。
  缺树、过期、缺身份或范围变化不能解释成 false。`target_enabled` 与 absence 不能作为“健康”的反推证据。
  作者与评审仍须核对语义独立：禁止用被测的开始可用、算路成功、预期提示本身定义场景前置。
- `guard={condition,equals,at?}` 默认用断言所关联步骤的终态观测；`at` 显式引用另一已成功步骤的终态。
  所有当前 provider 都来自 UI，只有同一 `action_epoch`、相同 APP/build/身份范围的观测才可复用；任何实际或可能下发动作使旧条件失效，拒绝下发不失效。
  这是 UI 来源的有效性边界，不是把车辆真实状态永久绑定页面。可按具体事实声明正有限 `max_age_s`；没有统一默认秒数。
  显式事实有效期还在实际下发关口按当前时钟复核，长定位不能用采样时点绕过到期。
  `clock_s` 使用执行器时钟（Replay 含虚拟等待），对断言当时证据截面评价；之后 cleanup 不会反向作废已保存证据。
- 规则可声明 `condition_requirements={条件名:bool}`，与显式 guard 合并，不允许调用方漏掉已确认规则的前提。
  多前置中任何明确采集异常→error，其次未知/冲突→pending，才可能 false→不涉及；已知 false 不掩盖另一条件的未知/错误。
  缺 provider 只保留 pending。显式互斥的条件要求也进入知识冲突/resolve 的同源作用范围，健康与故障的对立预定义规则不互相制造冲突；真正重叠的冲突仍待确认。K-QK-USR-002 将原确认文字中的故障前提机器化，没有新增生产读取器或确认健康事实。
- `step.when` 使用同样引用，执行前自动新采样，恢复/重试继续复核。false 记录 `skipped`（未触发），不作为动作成功；unknown 记 pending，关键步骤停止后续，error 沿第一切片留归因与原错。
  `when.at` 与 `guard.at` 都须引用 case.steps 中已定义的顶层步骤；Runner 在设备构造前拒绝未知步骤引用。已定义但尚无成功终态证据时仍为 pending。
  原动作前的每次弹窗清理也须复核完整操作条件链，不能先关闭弹窗再选支；动作后的后置/恢复继续按原有响应契约处理。
  条件动作的准备弹窗操作异常保留 error（`prepare_interrupt` 阶段）、原始回执和首次失败；已下发而效果未知时同时要求人工交接，不用后续选支抹去异常。
  正常策略拒绝、预算和外部输入交接仍为 blocked。flow/recipe 的每个子步骤仍走现有 Executor/Dispatcher；父条件全链继承并与子条件同时复核，父选择的车辆/任务/账户身份不能暗中更换。
  已执行部分动作但有必需子步骤未执行，或任一子步骤条件未知时，父操作保留 pending，不能从最后页面制造执行完成或分支覆盖。可选 false 子步骤可跳过，unknown 仍需补证。
  默认必需（critical=true）的子步骤 skipped 时，flow/recipe 立即 pending 并停止后继；顶层必需步骤 skipped 也阻断后继并将它们记 not_run/pending 原因。仅显式 critical=false 的可选分支允许跳过后继续。末尾分支 skipped 不抹掉已完成公共断言或该分支独立 guard 的不涉及结论。
  read 的定位滚动及事实有效期同样在测量前复核，辅助滚动原文保留，不用滚动后的读数冒充原分支已验证；没有第二套 Planner。
  read/tap/input 共用定位循环，每次辅助 swipe 前重核完整操作条件链；前一次动作使历史来源失效或当前反证/未知时不再下发下一次滚动。
  操作 skipped 本身不能证明产品不适用：只有产品 guard 自身为 false 才能不涉及，否则关联检查未执行，不能通过。

`assertions[].guard` 及 `assertion_selected` 事件保存 condition/status/value/source/obs/ts/scope/action_epoch/checks/reason，
操作记录用 `steps[].condition`；报告与 Console 显示相同记录，不重新选支。缺失或非法定义在下发前由 lint/Runner 拒绝。
非法 conditions 容器保留为结构化 lint 问题，不中断后续资产检查；Runner/执行上下文仍拒绝执行，依赖指纹也保留无效容器输入而不冒充空定义。

聚合与覆盖：公共断言通过、故障断言不涉及时保留公共结论；全部非建议断言不涉及时整例 `not_applicable`。
适用且 confirmed 的断言违反规则仍 failed；执行 error 与独立产品 failed 的双方证据及签名继续保留。
`verdict.coverage` 按断言分别列 verified / not_applicable / unverified；只有适用且 confirmed 的 pass/fail 算该断言已验证。
N/A、未知、error、未触发操作与未执行均不计分支覆盖。逐断言覆盖/Oracle/事实进入 replay 摘要，
条件资产晋级须每条所需断言在当前指纹下至少两次真实 verified 的一致证据；共同断言通过不能替代未触发分支。
经验对照须有相同适用及已验证分支，不能通过跳过困难分支制造改善。

旧无条件资产仍顺序执行。依赖契约升级使旧重放需重新积累；历史原始文件不改写。
未分类存储/程序异常仍保留堆栈并退出 3，不宽泛吞异常绕过停止/清理。211/212 登录资产迁移留 #31，真机分支可用性仍需独立验收。

## 4. 探索资产审核与运行准入（#30）

生成资产新增 `origin.type=session/exploration`；旧资产按 status、sources、history 与主步骤/cleanup/safe_stop
的 from_session/from_transition 联合识别。仅手改 status 或已有 reviewer 来源不解除审核要求。
`case approve` 为旧探索资产补持久 origin；不会自动审核现有资产。

- 用例 `asset_review`：schema=1、非空 reviewer/basis、带时区 ISO ts、binding。
- binding：case_id、原 content_hash/dependency_hash、review_hash、policy_hash、engine_hash、context_hash。
  review_hash 排除 status/history/replays/product_status/asset_review，其他字段均绑定；重放资格仍按当前内容/依赖指纹核对。
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
  当前内容哈希下至少 2 次确定且一致的重放才能晋级；待确认/阻塞/执行错误的重放不计入并列在 `ignored`；通过与失败混合 → 不稳定。
  内容哈希在用例声明时包含 `authorizations` 与 `safety`。


### 未确认停止后的运行暂停

`safe_stop` 未确认停止时，运行上下文登记 `safety_hold`（触发、时间、原因），本次运行不解除。
下发关口拒绝所有普通动作，返回 `safety_hold` 并记录未下发的 dispatch；逻辑步骤也在观测、定位与展开前复用该判定。
只有明确继承 `_safety` 的停止步骤可继续执行，状态及 policy 检查仍保留。
清理步骤记为 `not_run`，原因为“安全停止未确认，清理暂停，需人工接管”；`cleanup_done` 仍写入。
判定及报告将这些清理列为尚未验证，而不是清理失败，安全停止 alert 保留人工接管信号。

lint、Runner 与选中的 Executor 步骤共用 `execution_structure_issues`，沿 `iter_case_steps` 展开
主步骤、cleanup、safe_stop 和引用的 flow/recipe。递归引用、运行 ID 冲突及不支持的 swipe 参数属于定义错误：
Runner 在创建设备、解锁或关闭系统弹窗前拒绝；直接 Executor 沿用 `invalid_step` 工具阻塞，既有 Safety Hold 优先。
ID 按实际运行名称核对：flow/recipe 的子步骤为 `父ID.序号`，ensure_state 的自动尾检也占一个 ID；
cleanup 默认 `C序号`，safe_stop 固定 `SAFE序号`。声明的 flow 子 ID 可局部重复，同一合法 flow 可多次复用。
已声明的递归 recipe 是无效定义，不能用当前已达目标绕过；缺少 recipe 但当前已达目标仍可直接复用。
此检查不强制完整资产 lint、人审或成熟度；未选中的参数输入交接仍沿原条件执行契约。
`tap` 与 `input` 都必须引用已登记目标，内联目标仍须满足现场选择器来源要求；像素坐标、未声明终点及缺失 recipe 的检查适用于全部来源。
问题字段保留来源路径，如 `cleanup.C2`、`safe_stop.X1`、`steps.F1→flow:...X1`；递归引用会报错，不无限展开。

选支重试后条件变为 false/unknown 不构成原动作恢复：此前未恢复的 error 或产品失败候选及原错归因继续保留。
结果契约版本 5 同时使早期候选版本 2/3/4 的重放失效，包括准备弹窗/逐次滚动前尚未复核条件及必需步骤跳过后仍继续的运行；历史 raw run/verdict 不改写。
