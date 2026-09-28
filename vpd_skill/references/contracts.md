# 三个贯穿契约

所有字段都落在 `runs/<run_id>/` 下：`run.json`（汇总）、`events.jsonl`（追加写的原始事件）、
`obs/<obs_id>/`（每次观测独占创建：`observation.json`、`tree.json`、`screen.png`）、`report.md`。

## 1. 观测（`obs/<id>/observation.json`）

| 字段 | 含义 |
|---|---|
| `obs_id` / `ts` / `label` | 观测编号、时间、触发点（如 `S2#1:after`） |
| `source` | `hdc`（真机）或 `replay`（离线替身） |
| `foreground` / `process_alive` | 前台 bundle、目标进程是否存活（`pidof`） |
| `tree.status` | `ok` 有可用内容 / `empty` 调用成功但只有占位根节点 / `unavailable` 页面不提供 UI 树 / `error` 采集或解析失败 / `not_collected` 目标不在前台，按隐私策略不采 |
| `screenshot.status` | `ok` / `withheld`（前台非目标 APP，不截图）/ `error` / `skipped` |
| `page.state` | 已声明状态 ID，或 `unknown`（附 `signature`）、`ambiguous`（附 `candidates`）、`undetermined`（无可用树）、`out_of_app`（附 `suspected_crash`） |
| `page.interrupts` | 命中的中断：`{id, class: benign/anomaly/unknown, review: reviewed/pending}` |
| `texts` | 可见文本（前 60 条），便于 Agent 快速判断 |

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
| `swipe` | 已知屏幕尺寸 | `direction` | `expect_state` | 同上 | ≤2 | 滚动 |
| `input` | policy 开启输入且目标允许；非敏感框 | `target`,`text` | `expect_state` | 同上 | ≤2 | 改变输入框内容 |
| `ensure_state` | recipe 存在 | `state` | 到达该状态且无中断 | 由子步骤决定 | 子步骤各自 | recipe 中动作的副作用 |

每次尝试记录三层结果，互不替代：

- `dispatch.sent`：命令已发出；
- `dispatch.executor_ok`：执行器返回成功（hdc 退出码恒为 0，所以同时检查输出中的 `[Fail]`、`error:`、`10106102` 等）；
- `post.met`：执行后重新观察，预期业务状态出现且无中断。

尝试状态：`ok`、`target_unresolved`、`target_ambiguous`、`policy_denied`、`device_error`（附 `error_class`）、
`postcondition_not_met`、`interrupted`、`anomaly`、`unobservable`、`budget_exhausted`、`invalid_step`。
定位记录 `locate.attempts[]` 列出每个策略的结果；`adapted=true` 表示首个尝试的策略失败、由后备策略成功。

允许的恢复（`recoveries[]`，追加记录，不覆盖首次失败）：
`reobserve_and_retry`（定位失败）、`dismiss_interrupt`（仅限 `class=benign` 且 `review=reviewed`）、
`redispatch`（执行器瞬时错误，或步骤声明 `idempotent`）。`anomaly`、未评审中断、环境错误不恢复。

步骤结果：`passed` / `passed_after_adaptation` / `passed_after_recovery` / `fail_candidate` / `blocked` / `not_run`。
关键步骤未通过，后续步骤记为 `not_run`，清理步骤仍执行。

## 3. 判定（`assertions[]` 与 `verdict`）

断言类型：`state_is`、`text_present`、`text_absent`、`target_present`、`target_absent`、`target_text_equals`、
`node_selected`、`visual_review`（永远是建议）。每条非建议断言必须有 `oracle_refs` 指向知识条目。

| 断言结果 | 条件 |
|---|---|
| `pass` / `fail` | 证据确定 + 判据全部 confirmed |
| `pending` | 证据确定，但判据 unconfirmed / conflicted / missing；或属主观检查 |
| `undeterminable` | 证据不足（无 UI 树时判断"有/无"、定位多匹配等） |
| `not_run` | 关联步骤未成功 |

用例结论优先级：前置检查/环境要求不满足 → **阻塞（环境）**；有失败（产品）→ **失败**；有阻塞 → **阻塞**；
有待确认/不可判定，或没有任何断言得到确定判定 → **待确认**；否则 **通过**（报告另注明是否经恢复/适配）。
步骤出现崩溃或异常中断时用通用不变量 `K-GEN-001` 判定，不依赖该步骤业务判据是否已确认。
