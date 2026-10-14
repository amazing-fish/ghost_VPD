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
skill_policy / model_inference / observation / test_goal（测试目标里的期望，找不到独立判据时记录，仍是 unconfirmed）。
`knowledge add <file>` 的文件至少要有 `id`、`kind`、`statement`、`source`（`{"type": …, "ref": …}`）。

- 确认：`knowledge confirm <id> --reviewer <人>`；model_inference / observation 来源必须加 `--basis`。
- 知识状态只允许 `unconfirmed` / `confirmed` / `deprecated`。未知、缺失或非字符串状态在判据端为
  `invalid`，不能支撑通过/失败；`confirm` 只接受有效条目，不能复活废弃或无效知识。
- `deprecated` 条目保留历史检索，但只作为 background，不提供可调用的判据/操作指引；
  非建议断言引用废弃或无效条目时 `case lint` 拒绝，运行时判据仍为待确认。
- 冲突：同一 `subject` 下多个有效条目的 `expected` 不同即视为冲突，引用它的断言判为待确认；
  `knowledge resolve --subject ... --keep <id> --reviewer ... --reason ...` 裁决，被废弃条目保留并记录理由。
  部分范围重叠时仍需先拆分或明确独立裁决，不能扩大保留条目的范围来绕过保护。
- 2026-10-13 用户裁决（[#33](https://github.com/amazing-fish/ghost_VPD/issues/33)）：
  K-QK-001 已 `deprecated`，附 `superseded_by: K-AV-001`；K-AV-001 被选为入口基准，
  仍为 `unconfirmed`，仅保持原内部 debug 范围。选定基准不等于确认产品要求，也不代表公网版适用。
- 原工程选择器：`knowledge import-repo --repo .. --file app/config/motor_config.py --var MOTOR_HARMONY_CONFIG`
  用 AST 读取（不 import），生成带文件、行号、提交号的定位线索，状态为 unconfirmed_locator_hints。

## 探索路径 → 候选用例

`case from-exploration` 只生成**探索记录**（步骤 + 来源证据）。晋级为候选前 `case lint` 必须为空，要求补齐：
目的、前置条件、测试数据（或 `test_data_none: true`）、步骤（目标已登记、终点为已声明状态）、
至少一条非建议断言且每条有 `oracle_refs`、清理、来源。判据不能引用"本次探索观察"类知识（不独立）。

| 晋级 | 条件 |
|---|---|
| exploration_record / draft → candidate | lint 无问题；有探索/会话来源时另需当前有效 asset_review |
| candidate → replay_verified | 有探索/会话来源时另需当前有效 asset_review；当前内容与依赖版本下 ≥2 次不同运行的确定、一致重放（`run --record-replay`）；稳定失败须 `--known-issue`，待确认/阻塞不计入 |
| replay_verified → regression | 有探索/会话来源时另需当前有效 asset_review；终态评审人 + 全部判据 confirmed 且适用 + 至少 1 次计入的真机重放（仅离线时须显式 `--allow-offline`）；known_failure 保持失败，不改预期 |

修改步骤/断言/前置/清理会改变 `content_hash`，旧重放不再计数。
外部判据退役或状态无效会改变 `dependency_hash`，旧重放不再支撑晋级；
有效条目的 unconfirmed → confirmed 及历史追加不改变该指纹。仅修改 `knowledge_used`
中的历史说明不改变既有重放内容/依赖指纹，但会改变资产审核指纹，探索资产须重审。

`case review` 只读，`case approve --reviewer <人> --basis <依据>` 记录资产人审，操作步骤与版本范围见
[workflows.md](workflows.md#探索资产人工审核30)。人工确认不会自动晋级、确认知识或授予设备动作；
原有 regression 终态 reviewer、判据适用性、真机或显式 allow_offline 门槛保持。suite 与经验验证都要求
探索资产先有效审核；单条 run 可非正式取证，重放记录本身不构成人工确认。

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
