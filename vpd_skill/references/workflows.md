# 工作流

以下命令都在 `vpd_skill` 目录执行，`$D` 为设备描述：真机 `hdc:<serial>`，离线 `replay:<world>.json`。

## 回归

1. `.\vpdtest.ps1 doctor --device $D`：确认 `preflight.ok`。锁屏/熄屏/离线/未安装直接汇报阻塞（环境）。
2. `.\vpdtest.ps1 case lint <case>`：正式回归用例应无问题；draft/candidate 仍可执行，报告会标注非正式。
3. `.\vpdtest.ps1 run <case> --device $D`。需要账号等无法自动核实的环境事实时，由测试人员用
   `--env account_state=logged_out` 声明；未声明而用例要求 → 阻塞（环境），不会误判。
4. 读 `report.md`：结论、归属、首次异常、每步定位方式与恢复、断言证据。
5. 若报告列出经验候选：`experience validate <XP> --suite motor_hm_smoke --device $D`，再决定是否 `adopt`。

回归中允许的变化只有：定位策略顺序（经验 overlay）与已记录的执行恢复；断言、关键步骤、判据不变。

新运行独占 `runs/<run_id>`：自动 ID 碰撞最多尝试 10 次，显式运行 ID 已占用则拒绝，
不会复用旧报告、事件或回放状态；分配失败时不打开设备。只有命名 `--session` 明确续写同一会话，
仍须通过原会话锁、日志完整性及身份检查；它不表示可以恢复已完成的 `run`。

## 任务范围与需要显式授权的运行

1. 先读用例的 `authorizations` 与 `preconditions`。已委派任务内进VPD/算路无需逐次询问；其他gated与supervised类别仍检查明确授予。一般入口/算路诊断自行取证现场，不将未知车辆条件填成已确认的场景事实。
2. 账号/密码只放环境变量：`$env:VPD_SECRET_QIANKUN_PHONE = '...'`（值由用户在自己的终端里设置，不经过你）。
3. gated：`.\vpdtest.ps1 run TC-QK-101 --device $D --env account_state=logged_out --authorize account_state vehicle_selection`。
4. 控车：`.\vpdtest.ps1 run TC-QK-103 --device $D --env account_state=logged_in --authorize vehicle_motion --supervisor <监护人>`。用例必须声明 `safety`；运行中出现首次失败、超时（`max_motion_s`）或用例结束仍在动，执行器自动执行安全停止并单独记录。
5. 读报告：监视时间线（TOR、车速、故障文字的首次出现时间）、看门狗 `motion[]`、`safety_stops[]`。有 `action_required` 时立即转告用户人工接管。
6. 单步控车同理（加 `--app qiankun_hm`）：`act tap --target qk_pause` 不需授权（safe_stop）；`act tap --target qk_continue --authorize vehicle_motion --supervisor <人>`。

## 探索

1. 选起点（已声明状态）和目标：`.\vpdtest.ps1 explore --start car_tab --goal "我的页导航" --budget 30 --device $D [--prefer 设置 帮助]`。
2. 执行器在每个已声明状态上枚举可点击元素：命中禁止文本/禁止副作用/无文本标识的跳过并记录；
   每次尝试前经 recipe 回到来源状态（可从已知状态重新开始）；到达未声明页面只记录签名与证据。
3. 退出原因：`frontier_exhausted`（已声明状态都已展开）、`budget_exhausted`、`reset_failed`（回不到已知状态，停止）、
   `preflight_blocked`。
4. 读探索报告：覆盖只相对于已声明状态与本次观察。对未声明页面，打开 `obs/<id>/screen.png` 判断语义，
   需要纳入时提议 `states.json` 签名（人工评审）。

## 目标驱动：文字目标 → 会话 → 候选用例 → 新运行判定

```powershell
.\vpdtest.ps1 knowledge search 收藏 重命名 搜索 --app qiankun_hm --device $D   # 产品要求 / 操作经验 / 现场观察
.\vpdtest.ps1 observe --app qiankun_hm --session g1 --device $D                  # clickable 带 label/type/id
.\vpdtest.ps1 act tap --select "text=收藏" --side-effect user_data --authorize user_data --session g1 --device $D
.\vpdtest.ps1 act input --select "xpath=//TextInput" --allow-input --text 新名字 --side-effect user_data --authorize user_data --session g1 --device $D
.\vpdtest.ps1 act tap --select "text=确定" --expect-text "新名字" --side-effect user_data --authorize user_data --session g1 --device $D
.\vpdtest.ps1 session g1                                                         # 完整轨迹：观察/请求/被拒/失败/中断
# <新用例ID> 须是尚不存在的 ID（例如 TC-QK-201 已存在，照抄会报 FileExistsError）
.\vpdtest.ps1 case from-session --session g1 --new-id <新用例ID> --app qiankun_hm --goal "<目标原文>" --drop A4=与目标无关的试探
# 补齐 purpose / preconditions / assertions（独立 oracle_refs）/ cleanup / knowledge_used 后：
.\vpdtest.ps1 case lint <新用例ID>
# 单条 run 可先作非正式取证；晋级或放入任何批量入口前，按下方“探索资产人工审核”操作。
.\vpdtest.ps1 run <新用例ID> --device $D --env account_state=logged_in --env favorite_spot_slot=available --env vehicle_in_vpd_map=yes --authorize user_data --record-replay
```

- `--select`：`text=` 精确、`text~=` 包含、`id=`、`xpath=`，`--within type=|id=|pane=` 限定范围。只覆盖有 UI 树的页面。
- 每次 `--select` 下发前会重新取一次 UI 树复核目标（`revalidate`）：节点变了/移动了 → `target_stale`，不下发。
  下发前还会确认前台仍是被测 APP，否则 `foreground_changed`；可经受控 launch/resume 主动拉起被测 APP，再重新观察，不能把触摸/输入下发给其他 APP。
- `act` 返回 `diagnosis`：多匹配的候选、被裁剪的数量、部分可见比例、复核前后的节点。
- `--allow-input` 的文字会原样记入会话（用于复测），**只用于非敏感测试数据**；账号密码只能走用例的 `{"secret": NAME}`。
- `knowledge_used` 每项：`{"id": "K-AV-010", "use": "操作经验：常去地点里找收藏", "applicability": "unknown",
  "rationale": "来自 debug 版阔折叠，本机是直板 1.0.26.100，需现场核对", "steps": ["S7"]}`。
  `applicability` 可省略；声明时仅用 `applies` / `unknown` / `out_of_scope`。`confirmed`、`unconfirmed`、
  `calibrated` 等状态不属于适用性，具体范围和依据写在 `rationale`。lint、人工审核和晋级会报告非法声明；
  既有资产需逐条核对，不自动映射状态或重新签字。该作者声明不替代运行时独立事实与 Oracle 资格检查。

## 单步决策（无 UI 树、未知页面、需要 Agent 选路时）

```powershell
.\vpdtest.ps1 observe --session s1 --device $D           # 返回 page、texts、clickable（含 policy 判断）、截图路径
.\vpdtest.ps1 act tap --target settings_entry --expect settings --session s1 --device $D
.\vpdtest.ps1 act tap --point 540,1210 --basis "截图中'设置'文字所在行" --session s1 --device $D
.\vpdtest.ps1 act back --expect my_tab --session s1 --device $D
```

`--point` 必须有 `--basis`；有 UI 树时点落在无文本控件或禁止文本上会被拒绝；无 UI 树时坐标点击默认被拒绝
（先用 `template-crop` 裁剪模板并登记为定位策略）。同一 `--session` 的观测与事件累积在 `runs/session-<name>/`。

## 探索结果 → 候选用例 → 回归

```powershell
.\vpdtest.ps1 case from-exploration --run explore-XXXX --transitions T3 T5 --new-id TC-EXP-001
.\vpdtest.ps1 case lint TC-EXP-001                         # 列出缺失的目的/判据/清理等
# 编辑 data/cases/TC-EXP-001.json 补齐，并按下方“探索资产人工审核”取得有效审核后：
.\vpdtest.ps1 case promote TC-EXP-001 --to candidate
.\vpdtest.ps1 run TC-EXP-001 --device $D --record-replay   # 不同新运行中取得至少两次确定、一致的结果
.\vpdtest.ps1 case promote TC-EXP-001 --to replay_verified
.\vpdtest.ps1 case promote TC-EXP-001 --to regression --reviewer <评审人>
```

## 显示母版与冻结实例

240/241/243 可复用业务规则与当前脚本/证据范围分开管理。先登记独立专用 scope 和独立预期，
再用 `case instantiate --input <json>` 只读预览；显式 `--write` 只新建未审核 draft。
不会冷启动或切旧车，也不确认 Oracle、审批或晋级；实际 Runner 的预检副作用仍须另行核对。
详细 schema、范围、证据要求与限制见[母版说明](case-templates.md)。
候选 scope 尚未校准时，先执行[离线 scope 诊断](scope-inspection.md)，不通过伪填 verified 绕过生成门禁。

## 探索资产人工审核（#30）

`from-exploration` / `from-session` 的 `--new-id` 与用例更新、盘点共用稳定 ID 规则：
`[A-Za-z0-9][A-Za-z0-9_.-]*`，例如 `TC-EXP-001` 或 `normal_1.2-ID`。ID 不是输出路径；
不支持中文、空格或路径分隔符。不合法 ID 在读取知识、会话/探索来源、加锁或创建目录/文件前
以参数错误拒绝（JSON `ValueError`、退出码 3），不改写原件；已有同名用例仍拒绝覆盖。
母版 `case instantiate` 保留独立、更严格的 `TC-[A-Z0-9]+(?:-[A-Z0-9]+)*` 规则。

`from-exploration` / `from-session` 只生成探索资产；`case instantiate` 只生成要求审核的母版实例。它们均不授予晋级或批量准入。补齐字段、lint 通过、
已有 `sources.type=reviewer` 或 `case promote --reviewer` 都不代替本次资产审核。Agent 必须向人提供
完整用例及来源证据，由人明确确认后才执行 `case approve`；不得自行填审核人来批准自己生成的资产。

```powershell
.\vpdtest.ps1 case lint TC-EXP-001
.\vpdtest.ps1 case review TC-EXP-001   # 只读：required / valid / state / origin_signals / current / record
# 人已核对完整用例、原始来源、排除理由、独立判据、清理与所绑定执行配置之后：
.\vpdtest.ps1 case approve TC-EXP-001 --reviewer "<实际审核人>" --basis "<确认依据或可追溯评审链接>"
```

| 路径 | 检查位置与结果 |
| --- | --- |
| 探索/会话固化 | 持久 `origin.type`；保持原 sources、步骤来源、历史，不自动审核 |
| `case promote` 任一级 | 写文件前要求探索来源资产具有当前有效审核，其他成熟度门槛继续检查 |
| `suite`（字符串/对象成员、多设备） | 创建设备及集合记录前预检全部成员；每条 run 再加载并复检 |
| `experience validate --suite/--cases` | baseline/trial 开始前预检全部成员；每个回调都复检，不改经验算法 |
| 单条 `run` / `--record-replay` | 可保留非正式诊断；未有效审核的探索资产即使写成 regression 也仍标 informal |
| 人工文字/repo_code/reviewer 来源 draft | 保留现有路径；发现探索 origin/sources/步骤/历史则仍要求审核 |

审核记录在 `asset_review`，另追加 history；字段与绑定定义见[审核契约](contracts.md#4-探索资产审核与运行准入30)。
需要撤回既有批准时，执行 `case revoke-review <case> --reviewer <实际撤销人> --basis <原因/证据>`。
撤销后晋级和批量入口拒绝，单条诊断保持 informal；原批准、成熟度、产品结论和重放历史保留。
重新启用须由人重新核对后执行现有 `case approve`，不能恢复旧批准记录来撤回撤销。
这不是设备紧急停止，也不自动撤回某次证据；完整边界和后续降级设计见[资产生命周期](../../docs/ASSET_LIFECYCLE_DECISIONS.md)。
修改目的、来源、knowledge_used、步骤、断言、
目标定义、判据陈述或执行配置后，旧审核失效，须重新审查。正常晋级、追加重放与历史，以及有效知识
unconfirmed → confirmed 不使审核失效；产品知识退役仍使依赖失效。

审核不绑定引擎源码：判定语义变化通过 `RESULT_CONTRACT` 进入依赖指纹并要求重审，纯重构不要求。
上下文含 APP 元数据、布局、系统弹窗和该 APP 全部模板，其中任何修改都要求重审。调用开始时检查版本；
policy、布局和弹窗使用本次检查的配置快照。模板仅核对入口内容，运行期间应保持不变，未提供文件系统锁保障。
`case review` / `case readiness` 的 `engine_changes` 仍列出审核后变化的引擎文件，供人判断是否需要复核。
允许的经验 overlay 仍按既有验证规则调整定位/等待，不写入资产审批版本，也不能更改判据或 policy。

这是本地审计记录，reviewer 是声明身份，不能认证操作者或防止恶意改写文件。审核不提升成熟度、
不 confirm Oracle、不替代本次 `--authorize/--supervisor`，也不构成真机或产品通过证据。

### 晋级条件

先用 `case readiness [预期用例ID ...]` 只读查看当前成熟度、人审绑定差异和既有晋级门槛。
不传ID仅枚举目录，不能发现未入仓源件；不提升、不审批、不确认知识。[完整口径与用法](asset-readiness.md)。

每一级均须 `case lint` 无问题；有探索/会话/母版来源时，还须上述当前有效的人工审核。
候选用例须补齐目的、前置、测试数据（或 `test_data_none: true`）、步骤（目标已登记、终点为已声明状态）、
至少一条非建议断言且每条有独立 `oracle_refs`、清理和来源；
假设目标未校准、流程已被证伪或判据来自本次探索观察时，不能靠人审绕过 lint。

| 晋级 | 额外条件 |
| --- | --- |
| exploration_record / draft → candidate | 补齐上述内容 |
| candidate → replay_verified | 当前内容与依赖下，至少两次不同运行给出确定、一致的重放结果；稳定失败须 `--known-issue` |
| replay_verified → regression | 重新核对当前重放资格；终态评审人、全部必需判据 confirmed 且适用，至少一次计入的真机重放；仅离线时须显式 `--allow-offline` 并说明依据 |

整例重放结论为不涉及、待确认、阻塞，或包含执行异常时，不计为确定重放，其后须重新积累至少两次；
当前内容与依赖下的全部确定结论仍须一致，失败特征也包括最近不确定运行之前的确定失败；缺失旧失败特征不能证明稳定失败。
新摘要原样保留运行的 `started` / `finished`，顶层 `ts` 仍只是录入时间，不是运行先后。
“其后”指新运行的 `started` **严格晚于全部不确定运行的完成上界**，不是数组后来的记录；
两次独立新运行不要求彼此串行，但它们都必须满足此边界。时间按含时区的实际时刻比较，相同毫秒、重叠区间不证明先后。
单条断言未知/异常/未执行的覆盖重置也用同一时序依据，并保留全部当前相关历史中的断言重置，
包括因与整例阻塞重叠而没计入整例恢复次数的运行；明确不涉及不重置该断言。

兼容旧摘要时不自动追写、重排、补时间或重签。没有时间敏感重置时，完全缺少 `started` / `finished` 的旧确定证据仍可用；
有重置时，旧确定证据不能用录入 `ts` 或数组位置证明自己是后续运行。
完全无运行时间的旧重置若有合法含时区 `ts`，只能把它作为**保守的完成上界**，不是实际 `finished`；
没有有效上界的重置不能被另一个已知时间的重置掩盖，须核对原始材料，不能仅靠再录入两条通过消除缺口。
已提供但缺项、无时区、非法或倒退的运行区间明确阻断，不退回录入时间。
墙钟并非跨机器的单调时钟；此规则依赖可信且同步的源时钟，不能发现所有回拨、跨机偏差或恶意改写。

同一 `run_id` 的摘要只允许完全相同（仅顶层录入 `ts` 可不同），重复录入不写文件、不增加次数、不移动首次位置。
唯一格式兼容例外是重新录入完全无运行时间的旧摘要：若其他证据完全相同，忽略新来源的运行时间并保留旧摘要原字节，
不借重试补全历史；旧摘要已有任一运行时间字段时，不允许差异或丢失。读取历史中同 ID 的不同时间/格式仍按冲突处理。
与当前资格相关的 ID 必须核对其全部历史摘要，包括旧内容/依赖版本；冲突时 readiness 与晋级预览均阻断，列出 ID 和差异字段，不回显证据值。
公共 `record_replay` 对冲突拒绝追加，保留原资产字节与运行材料；完全无关的旧 ID 冲突不影响当前资格。须核对原始证据，不应删除冲突记录来制造可晋级结果。
失败保持原预期并登记缺陷。在可计入的整例重放中，单条断言明确不涉及不增加覆盖，也不清空它已积累的有效证据。
含条件的资产还须满足[逐断言分支覆盖](contracts.md#断言前置与操作选支56-第二切片)，未触发分支不能凭其他断言通过来补足。
内容或依赖变动后的旧记录不再支撑晋级，具体指纹边界见[知识与重放依赖](knowledge-rsi.md#探索路径--候选用例)。

## 把已有文字用例接进来

1. 放入 `data/knowledge/text_cases/`（Markdown/文本均可），记录来源（需求编号、文档链接）。
2. 由 Agent 读取并写成 `data/cases/<id>.json`：步骤映射到 `targets.json` 目标与 `states.json` 状态；
   预期结果写成知识条目（`knowledge add`，source.type=text_case）并在断言中引用。
3. `case lint` → 由用例负责人 `knowledge confirm` 判据 → 回归执行。
