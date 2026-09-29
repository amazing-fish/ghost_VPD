# 三个贯穿契约

所有字段都落在 `runs/<run_id>/` 下：`run.json`（汇总）、`events.jsonl`（追加写的原始事件）、
`obs/<obs_id>/`（每次观测独占创建：`observation.json`、`tree.json`、`screen.png`）、`report.md`。

进度事件（控制台据此显示进度，只读）：用例 `run_start`（含 `steps` 计划）→ `step_start`/`step_done` → `run_end`；
用例集在 `runs/suite-*/events.jsonl` 写 `suite_start`（`total`）→ `case_start`/`case_done`（`run_id`、`verdict`）→ `suite_end`；
探索 `explore_start` → `explore_end`。没有结束事件且超过 120 s 未更新的运行显示为"疑似中断"。

## 1. 观测（`obs/<id>/observation.json`）

| 字段 | 含义 |
|---|---|
| `obs_id` / `ts` / `label` | 观测编号、时间、触发点（如 `S2#1:after`） |
| `source` | `hdc`（真机）或 `replay`（离线替身） |
| `foreground` / `process_alive` | 前台 bundle、目标进程是否存活（`pidof`） |
| `tree.status` | `ok` 有可用内容 / `empty` 调用成功但只有占位根节点 / `unavailable` 页面不提供 UI 树 / `error` 采集或解析失败 / `not_collected` 目标不在前台，按隐私策略不采 |
| `screenshot.status` | `ok` / `withheld`（前台非目标 APP，不截图）/ `error` / `skipped`；`fallback_from` 表示首选截图方式失败后改用了 `snapshot_display`（如控制台帧流运行时 `screenCap` 不可用） |
| `page.state` | 已声明状态 ID，或 `unknown`（附 `signature`）、`ambiguous`（附 `candidates`）、`undetermined`（无可用树）、`out_of_app`（附 `suspected_crash`） |
| `page.interrupts` | 命中的中断：`{id, class: benign/anomaly/unknown, review: reviewed/pending}` |
| `texts` | 可见文本（前 60 条），便于 Agent 快速判断 |
| `foreground_error` | 前台查询失败的原因（如 `transport_unavailable`）；此时 `page.state=undetermined`，不当成"不在 APP 内" |
| `layout` | 窗口形态，见下 |
| `page.active` / `page.panes` | 当前生效的状态（分栏时可能多个）/ 每栏各自命中的状态 |

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
| `back` / `home` | APP 在前台 | — | `expect_state` | 同上 | ≤2 | 导航 |
| `swipe` | 有可滚动容器或已知窗口尺寸 | `direction`，可选 `within` | `expect_state` | 同上 | ≤2 | 滚动；起止点取容器可见区域的 75%/25% 高度，分栏时不会滑到另一栏；滑动前后内容指纹不变记为 `scroll_end` |
| `input` | policy 开启输入且目标允许；非敏感框 | `target`,`text` | `expect_state` | 同上 | ≤2 | 改变输入框内容 |
| `ensure_state` | recipe 存在 | `state` | 到达该状态且无中断 | 由子步骤决定 | 子步骤各自 | recipe 中动作的副作用 |
| `resume` | APP 已启动过 | — | `expect_state`；另记 `resume.{process_alive_before, cold_start}` | 同上 | ≤2 | 从后台拉回（与 launch 同一命令） |
| `wait` | — | `duration_s`（上限 policy `max_monitor_s`） | `expect_state` | — | — | 无；运动中等待仍计入看门狗 |
| `read` | 目标唯一定位，或 `read_scope: page` | `target`、`read_scope`（node 节点文字 / self 节点子树 / page 整页）、`pattern`（取第 1 组） | 读到值（`measurement.value` 非空） | 同上 | ≤2 | 无 |
| `monitor` | — | `duration_s`、`interval_s`、`watch{名: 正则}`、`until_state` / `until_text` / `until_text_gone`、`stop_on`、`abort_on` | 见下方「监视」 | `duration_s`（上限 `max_monitor_s`） | 不重试 | 无（只观测） |
| `flow` | `flows.json` 中存在 | `flow`、`params` | 每个子步骤各自的后置条件 | 子步骤各自 | 子步骤默认 1 | 子步骤的副作用 |

`tap`/`input` 的可选字段：

| 字段 | 作用 |
|---|---|
| `require_enabled` | 点之前按 `enabled` 与目标 `enabled_by`（如 `{"attr": "opacity", "min": 1.0}`）检查控件可用；不可用 → `target_disabled`（阻塞·环境），不点击，证据在 `enabled_check` |
| `expect_text` | 后置条件另要求页面出现匹配该正则的文字（例如点地图后出现「收藏」才算弹出了车位卡片） |
| `skip_if_absent` | 本页没有匹配该正则的文字就跳过（记 `skipped`，不算失败），用于可选步骤 |
| `at` / `random_point` | 在目标容器可见区域内按比例取点 / 按种子随机取点（重试换点），不写像素 |
| `idempotent` | 允许重发（重复点击无害的步骤） |

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

允许的恢复（`recoveries[]`，追加记录，不覆盖首次失败）：
`reobserve_and_retry`（定位失败）、`dismiss_interrupt`（仅限 `class=benign` 且 `review=reviewed`）、
`redispatch`（执行器瞬时错误，或步骤声明 `idempotent`）。`anomaly`、未评审中断、环境错误不恢复。

步骤结果：`passed` / `passed_after_adaptation` / `passed_after_recovery` / `fail_candidate` / `blocked` / `not_run`。
关键步骤未通过，后续步骤记为 `not_run`，清理步骤仍执行。

**授权与运动安全**（`run.json`）：`authorized`（本次授予的副作用）、`supervision.{supervisor, supervised_granted, safety}`、`motion[]`（看门狗事件：`armed` 附触发步骤与控件；由 `moving_states` 观测挂上的记 `step: observed`、`label: state:<状态>`；`stopped_observed` 附状态与耗时）、`safety_stops[]`（触发原因、执行的安全停止步骤、`confirmed_stopped`；未确认时 `action_required` 要求人工接管）。安全停止记录独立于步骤结果，不覆盖首次失败。

**脱敏**：带密钥参数的运行里，`tree.json` 落盘前把密钥值替换为 `***`，观测里的文字取自脱敏后的树；`run.json`、`events.jsonl` 写盘前同样脱敏；被脱敏的观测 `redacted: true` 且 `screenshot.status=withheld`。回放设备的输入值只在内存里，不写 `replay_state.json`。

## 3. 判定（`assertions[]` 与 `verdict`）

断言类型：`state_is`、`text_present`、`text_absent`、`target_present`、`target_absent`、`target_text_equals`、
`node_selected`、`visual_review`（永远是建议），以及：`text_present_any`（任一文字出现）、`text_seen` / `state_seen`（在该步骤的监视时间线里出现过，步骤没有监视时看它的终态观测；`expected: false` 表示整个窗口内都不应出现）、`target_enabled`（按 `enabled` 与 `enabled_by` 判断；`absent_is: "disabled"` 表示不出现也算不可用）、`value_present`（`read` 读到的值匹配正则）。每条非建议断言必须有 `oracle_refs` 指向知识条目；未确认时结果为 `pending`，并给出 `observed_holds`（观测本身是否成立）。

| 断言结果 | 条件 |
|---|---|
| `pass` / `fail` | 证据确定 + 判据全部 confirmed |
| `pending` | 证据确定，但判据 unconfirmed / conflicted / missing；或属主观检查 |
| `undeterminable` | 证据不足（无 UI 树时判断"有/无"、定位多匹配等） |
| `not_run` | 关联步骤未成功 |

用例结论优先级：前置检查/环境要求不满足 → **阻塞（环境）**；有失败（产品）→ **失败**；有阻塞 → **阻塞**；
有待确认/不可判定，或没有任何断言得到确定判定 → **待确认**；否则 **通过**（报告另注明是否经恢复/适配）。
步骤出现崩溃或异常中断时用通用不变量 `K-GEN-001` 判定，不依赖该步骤业务判据是否已确认。
