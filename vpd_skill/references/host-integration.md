# 宿主 Agent 最小接入契约

核心逻辑全部在 `vpdtest`（Python 包 + CLI），与宿主无关。宿主只需要：

1. **运行命令**：能在 `vpd_skill` 目录执行 `.\vpdtest.ps1 ...`（或 `.venv\Scripts\python.exe -m vpdtest ...`）并读取 stdout。
2. **读取 JSON**：stdout 是单个 JSON 对象。退出码：

   | 码 | 含义 | 宿主怎么做 |
   |---|---|---|
   | 0 | 命令完成（不代表用例通过） | 读 `verdict` |
   | 1 | `--fail-on` 命中 | CI 判失败 |
   | 2 | `action_required`：需要现场人工接管（未确认的安全停止、解锁失败等） | 立即转告用户，不重试 |
   | 3 | 参数或工具错误，`{"error", "message"}`；命令行用法错误为 `"error": "UsageError"` | 修正命令后重试 |
   | 130 | 被中断（KeyboardInterrupt），输出 `{"error": "KeyboardInterrupt", ...}` 与已分配运行的诊断位置 | 先核对现场与已下发动作，再决定是否重跑 |

   环境阻塞（缺授权、缺密钥、`--env` 不符）时退出码仍为 0、`verdict=blocked`，逐项条件在 `findings[].detail`
   （`key` / `required` / `actual`）里，按它补参数，不要解析 `reason` 原文。
   `suite` 与 `experience validate` 一旦某次运行要求人工接管，立即暂停剩余计划并退出 2；
   输出 `completed=false`、`action_required` 与按计划顺序排列的 `not_run`。
   用例集保留触发接管的 `results` 行；经验验证另用 `handoff` 给出阶段（baseline/trial）、源 run 和 report。
   未完成的比较不计改进、不晋级经验；暂停前已成立的回退继续保留为 rejected。
   vpd_entry / route_planning 随已委派VPD任务许可，不为这两类反复询问或补 `--authorize`；
   run 的 `authorization_sources` 与会话 authorization事件的 `grant_sources` 区分 task_policy / explicit。
3. **读文件**：能打开返回的 `report.md`、`obs/<id>/observation.json`，可选能查看 `screen.png`（用于未知页面理解与 UI/UX 建议）。
4. **加载指令**：把 `SKILL.md` 作为技能说明加载；`references/` 按需读取。

不需要：常驻服务、数据库、网络（除 hdc 连接设备外）、多 Agent 编排。状态都在 `data/` 与 `runs/` 文件中，
任一宿主会话中断后可以从文件继续。

## HDC 设备未连接

HDC 的 `[Fail][E001005] Device not found or connected` 明确归为 `device_offline`，
即使进程退出码为 0，`doctor` 也不会将 `device_online` 标为通过。后续锁屏、应用和显示查询失败仍保留原始
`execution_error`，未知值保持未知，不能把这些失败当作“确实锁屏”或“没有安装 APP”的证据。
普通 `[Fail]Connect failed` 不足以单独确定原因，仍保留通用命令失败；不因此自动重连或尝试设备操作。

## 已知宿主的挂载方式

- **Claude Code**：`install.ps1 -LinkSkill` 在 `%USERPROFILE%\.claude\skills\vpd-app-test` 建目录联接指向本目录。
- **其他支持 SKILL.md 的宿主**：`install.ps1 -LinkSkill -SkillDir <宿主技能目录>\vpd-app-test`（路径以宿主文档为准）。
- **只支持 system prompt / 工具调用的宿主**：把 SKILL.md 正文放进指令，把 `vpdtest` 注册为一个 shell 工具即可。

CI 或无人值守执行时不需要 Agent：`.\vpdtest.ps1 suite motor_hm_smoke --device hdc:<serial> --fail-on failed error blocked`。

## 单步会话预算

`observe` / `act` 省略 `--app` 时，复用同一 `--session` 已记录的 APP（兼容旧 `session_facts`）；
新会话仍默认 `motor_hm`。显式 `--app` 优先，但不能更改已有会话身份：APP、设备或验证级别冲突仍拒绝。
`--device` 仍须显式提供或在配置中指定，不从会话自动选择设备。`case from-session --app` 仍须明确填写并与来源一致。

`act` 的正常执行结果（包括定位、策略和预算拒绝）在顶层返回 `budget: {used, max, remaining}`。
`used` 是同一 session 已下发或发送事实未知的物理动作累计数，`remaining` 最低为 0；
默认上限仍为 60，observe、wait 和 monitor 本身不消耗动作额度。输入清空、定位滚动、恢复和自动解锁的实际物理动作继续按原规则计数。
本次采用 #55 的“维持计费 + 80% 提前告警”，避免新增解锁豁免或逻辑动作计费旁路。

达到 80% 时顶层追加 `budget_warning`；额度耗尽时追加恢复 `hint`。
若本步因预算拒绝，还会有顶层 `reason`，`result.final_status=budget_exhausted`；
刚好用完额度的成功动作仍保留原 `result.outcome`，不因为剩余为 0 改成失败。
宿主读取结构化预算与原执行结果，不靠告警文字判断动作是否下发；参数或工具异常仍沿原退出码 3 契约处理。

耗尽后先核对当前现场、副作用和待处理交接，确认可继续后再显式使用新的 `--session <名称>`；
上调上限须人工修改 policy。新会话额度不授予动作权限，也不能解除未确认的安全暂停。
这些字段仅回显现有计数，没有新增预算状态文件、自动重置或跨会话预算池。

## 自动选支的消费

宿主直接消费 `assertions[].guard`、`verdict.coverage` 和 `execution`，无需逐步向 LLM 请求选支。
`source/obs/ts/scope/action_epoch/checks/reason` 可在 run/report/Console 核对。
`not_applicable` 不计通过或覆盖；全部不涉及时整例不涉及。`step.when` 的 skipped 不计产品分支覆盖。
`--fail-on not_applicable` 可配置退出码1，人工接管仍优先2。缺独立条件证据是 pending，明确采集异常是 error，正常受阻是 blocked。
业务 `--env` 只保留调度声明；不得据旧故障声明或开始可用反推当前健康。
