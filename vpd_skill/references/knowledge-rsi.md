# 知识、经验与 RSI v0.1

## 五类信息分开管理

| 类别 | 存放 | 谁能写 | 能否支撑判定 |
|---|---|---|---|
| 有来源的需求/业务知识 | `data/knowledge/items.json` | `knowledge add`（一律 unconfirmed） | 仅 `confirmed` 能支撑通过/失败 |
| 本次运行的观察事实 | `runs/<id>/obs/`、`events.jsonl` | 执行器 | 只作证据，不能反过来当判据 |
| 定位/等待/恢复经验 | `data/experience/<app>/items.json`、`active.json`、`ledger.jsonl` | 运行后自动生成候选 | 不涉及判定，只影响执行方式 |
| 探索生成的候选用例 | `data/cases/*.json`（status=exploration_record/candidate） | `case from-exploration` | 非正式，报告标注"仅供参考" |
| 正式回归用例 | `data/cases/*.json`（status=regression） | 只能经 `case promote` | 是 |

知识条目字段：`id, kind, subject, statement, expected, applies_to{app, app_version, ...}, source{type, ref, commit},
status, confirmed_by, history`。`source.type` 取值：requirement_doc / text_case / repo_code / reviewer /
skill_policy / model_inference / observation。

- 确认：`knowledge confirm <id> --reviewer <人>`；model_inference / observation 来源必须加 `--basis`。
- 冲突：同一 `subject` 下多个有效条目的 `expected` 不同即视为冲突，引用它的断言判为待确认；
  `knowledge resolve --subject ... --keep <id> --reviewer ... --reason ...` 裁决，被废弃条目保留并记录理由。
- 原工程选择器：`knowledge import-repo --repo .. --file app/config/motor_config.py --var MOTOR_HARMONY_CONFIG`
  用 AST 读取（不 import），生成带文件、行号、提交号的定位线索，状态为 unconfirmed_locator_hints。

## 探索路径 → 候选用例

`case from-exploration` 只生成**探索记录**（步骤 + 来源证据）。晋级为候选前 `case lint` 必须为空，要求补齐：
目的、前置条件、测试数据（或 `test_data_none: true`）、步骤（目标已登记、终点为已声明状态）、
至少一条非建议断言且每条有 `oracle_refs`、清理、来源。判据不能引用"本次探索观察"类知识（不独立）。

| 晋级 | 条件 |
|---|---|
| exploration_record / draft → candidate | lint 无问题 |
| candidate → replay_verified | 当前内容版本（`content_hash`）下 ≥2 次不同运行的**通过**重放（`run --record-replay`）；判据未确认时重放只会是待确认，因此不会晋级 |
| replay_verified → regression | 评审人 + 全部判据 confirmed + 至少 1 次真机通过重放（仅离线时须显式 `--allow-offline`） |

修改步骤/断言/前置/清理会改变 `content_hash`，旧重放不再计数。

## 经验候选（RSI v0.1 的范围）

"根据运行证据提出定位策略、执行策略或 Skill 的改进候选，并通过重放与回归验证决定是否采纳"。

| kind | 来源 | 采纳 |
|---|---|---|
| `locator_preference` | 某目标首选策略失败、后备策略成功（`locate.adapted`） | 验证通过后可自动采纳 |
| `wait_timeout` | 后置条件在超时窗口后 30% 才出现 | 验证通过后可自动采纳，上限 policy `max_wait_s` |
| `interrupt_rule` / `state_signature` / `skill_note` | 人或 Agent 提议 | 必须 `--reviewer` |
| `assertion_change` / `step_removal` / `policy_change` | — | 禁止走经验通道：断言与关键步骤走用例评审，policy 单独人工修改 |

经验 overlay 只能调整策略顺序或等待时间，不能删除仓库来源的原始策略，也读不到 policy。

**验证**：`experience validate <id> --suite <集合> --device <spec> [--device <spec2>]`。对每个用例 × 设备/世界
分别跑基线（当前已采纳 overlay）与试验（基线 + 候选）：

- 任一用例试验结论劣于基线 → `rejected`；基线判出的产品失败在试验组变成阻塞/待确认也算回退
  （执行改动不能把缺陷藏到工具问题后面）；
- 覆盖不足 → 保持 `candidate`：定位偏好只在来源用例上被用到；等待时长绑定单个用例步骤，
  只在来源设备上走到过该步骤（要在另一台设备/另一个世界上复现）；
- 没有可测改进（试验组从未通过变为通过，或结论不变而适配次数减少）→ 保持 `candidate`；
- 否则 `validated`。

**采纳与回滚**：`experience adopt <id> [--reviewer]` 生成 overlay 新版本并保存上一版快照；
`experience rollback --to-version N --reason ...` 恢复快照（版本号继续递增），被移除条目标记 rolled_back。
所有动作追加到 `ledger.jsonl`；每次运行在 `run.json.overlay` 记录所用 overlay 版本与试验条目。
