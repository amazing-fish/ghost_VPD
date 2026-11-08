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
- `domain_note` 的 `kind` 保留不变。未确认时检索仍显示背景（观察来源显示现场观察）；确认后按
  `requirement` 展示已有判据资格。`can_be_oracle` / `usable_oracles` 仍由现有 Oracle 检查决定：
  适用事实缺失或不符、冲突、退役和无效状态均不可用；`operation` / `observation` 仍非判据。
  检索不执行确认，不替评审人确认知识内容，也不修改生产条目的状态。
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

## 观察判据与用例来源身份

`observation` 来源的规则仍可凭依据确认，但确认不能消除与本例的已知同源关系。
非建议断言引用的有效观察知识，与用例全部 `sources` 的来源身份有交集时，`case lint`、
`case approve`、`case readiness`、`case promote`（包括只读预览）及要求资产审核的批量/运行前置检查
均拒绝为“不独立”。知识 `source` 元数据变化即使未改变既有审核指纹，也会重新核对；
批量/要求审核的运行拒绝发生在新运行目录和设备创建前，不重写原用例、知识或历史运行。
审核状态同时保留明确的 `lineage_issues`：已知同源时 `required=true`、`valid=false`，
有既有审核记录则显示 `stale`，即使其内容/依赖指纹没有变化。普通单条诊断仍可执行，但标记为
`informal`，包括手工来源且已到 `regression` 的资产；不能据此晋级或绕过批量门禁。

身份只按以下明确形式提取，区分大小写，不做 Unicode 归一化或子串搜索：

- `source.run`：非空、可移植的单层目录名，保留显式历史 ID，不要求固定前缀。
- `source.ref`：完整 `run-YYYYMMDD-HHMMSS-<小写字母数字后缀>` 或 `session-<会话名>`；
  可选 `runs/` 前缀，也可追加 `/obs/obs-0001` 这样的规范观测目录定位。
  观测编号遵循现有分配器：正整数，至少四位且无多余前导零。
- 会话名与正常 CLI 的名称字符边界一致：允许中文、内部空格和点；不含路径分隔符、
  Windows 保留符号、C0/C1 控制字符或尾随点/空格。这里不读取目录、不验证证据是否存在。
- `run` 与可解析 `ref` 同时填写时，两者都参与；多个用例来源也全部参与。
  不能用另一个不同来源覆盖已知的同源身份。缺失、空、非字符串及非法目录名不产生身份。

例如 `{"type":"observation","ref":"runs/session-会话/obs/obs-0001"}` 与
用例的 `{"type":"session","run":"session-会话"}` 同源。
`session-x obs-0001` 则是一个完整会话名，不解析成 `session-x` 的观测；
观测定位请用 `/obs/`，叙述性说明可保留在其他来源字段或人工依据中。
URL、文件路径、多来源自由文本等不从中猜出身份；需要机器核对时另填明确 `run`。
无法解析或未发现交集仅表示没有发现已知同源，**不证明来源真实或独立**。
已有确认、适用范围、冲突和人工审核要求保持不变，非观察来源不套用此观察门禁。

## 单次运行的知识快照

`run` 在首次知识校验及资产审核前读取一次只读知识快照。该次运行的条件引用、断言判据、
最终结论、`case.deps` 和 `knowledge_snapshot` 均使用这份内容，不在运行中重读知识文件。
快照的读取结果可供调用方使用，但修改返回的容器不能改变快照；`add`、`confirm`、`resolve`
和底层保存均不能经快照写回。前置检查后仍绑定本次实测 `oracle_facts`，适用范围与场景证据
照常检查；冻结知识定义不等于冻结或补造现场事实。

运行中新增、确认或退役知识从下一次运行生效。已开始的未确认运行仍为 `pending`，不因
中途 `confirm` 追改为通过；随后须重新积累确定重放。历史结论与依赖记录不改写。
普通知识命令、后续运行及当前 `case readiness` / `case promote` 仍读取最新知识，因此当前
冲突、退役或无效引用会按既有门禁使旧证据失去资格，不能将旧判据结论记为新版本已验证。
纯确认/历史追加、无关主题、同预期及不重叠范围的依赖指纹语义保持不变。

此边界不提供退役后即时中止在途操作，也不冻结全部文件系统。模板字节与执行源码仍须
[按现有运行约定保持不变](workflows.md)；模板/代码热更新及 ABA 改动防护不属于知识快照。

## 探索路径 → 候选用例

`case from-exploration` / `case from-session` 只生成探索资产。人审操作与各级门槛统一见
[探索资产人工审核](workflows.md#探索资产人工审核30)及[晋级条件](workflows.md#晋级条件)；
资产审核不确认知识，不授予设备动作。以下只说明重放与审核的依赖边界。

修改步骤/断言/前置/清理会改变 `content_hash`，旧重放不再计数。
外部判据退役或状态无效会改变 `dependency_hash`，旧重放不再支撑晋级；
有效条目的 unconfirmed → confirmed 及历史追加不改变该指纹。显式判据的 kind/subject、实际冲突的直接条目（沿用既有范围比较），以及所引用的模板字节均绑定；无关主题、不重叠范围或同预期条目不影响重放。
动作模板的文件路径与策略类型只支持固定值或普通静态参数；不支持 secret、`test_data.template` 生成值（包括常量模板）或未知引用，无法绑定时在创建设备前明确拒绝。普通动作的动态输入文字不受影响。
模板绑定覆盖动作、内联目标、条件/断言及中断处置各自引用的模板文件；创建、删除或变更文件后须重新积累重放。资产人审既有的执行引擎及全模板上下文绑定保持原有更宽范围。历史记录不改写。
仅修改 `knowledge_used`
中的历史说明不改变既有重放内容/依赖指纹，但会改变资产审核指纹，探索资产须重审。

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
