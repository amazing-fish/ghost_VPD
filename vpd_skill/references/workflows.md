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
.\vpdtest.ps1 case from-session --session g1 --new-id TC-QK-201 --app qiankun_hm --goal "<目标原文>" --drop A4=与目标无关的试探
# 补齐 purpose / preconditions / assertions（独立 oracle_refs）/ cleanup / knowledge_used 后：
.\vpdtest.ps1 case lint TC-QK-201
# 单条 run 可先作非正式取证；晋级或放入任何批量入口前，按下方“探索资产人工审核”操作。
.\vpdtest.ps1 run TC-QK-201 --device $D --env account_state=logged_in --env favorite_spot_slot=available --env vehicle_in_vpd_map=yes --authorize user_data --record-replay
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

## 探索资产人工审核（#30）

`from-exploration` / `from-session` 只生成探索资产，不授予晋级或批量准入。补齐字段、lint 通过、
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
修改目的、来源、knowledge_used、步骤、断言、
目标定义、判据陈述或执行配置后，旧审核失效，须重新审查。正常晋级、追加重放与历史，以及有效知识
unconfirmed → confirmed 不使审核失效；产品知识退役仍使依赖失效。

执行引擎指纹保守覆盖全部 `vpdtest/**/*.py`；上下文含 APP 元数据、布局、系统弹窗和该 APP 全部模板。
因此即使无关引擎代码或模板修改，也可能要求重审。调用开始时检查版本；policy、布局和弹窗使用
本次检查的配置快照。源码及模板仅核对入口内容，运行期间应保持不变，未提供文件系统锁或代码热更新保障。
允许的经验 overlay 仍按既有验证规则调整定位/等待，不写入资产审批版本，也不能更改判据或 policy。

这是本地审计记录，reviewer 是声明身份，不能认证操作者或防止恶意改写文件。审核不提升成熟度、
不 confirm Oracle、不替代本次 `--authorize/--supervisor`，也不构成真机或产品通过证据。

### 晋级条件

每一级均须 `case lint` 无问题；有探索/会话来源时，还须上述当前有效的人工审核。
候选用例须补齐目的、前置、测试数据（或 `test_data_none: true`）、步骤（目标已登记、终点为已声明状态）、
至少一条非建议断言且每条有独立 `oracle_refs`、清理和来源；
假设目标未校准、流程已被证伪或判据来自本次探索观察时，不能靠人审绕过 lint。

| 晋级 | 额外条件 |
| --- | --- |
| exploration_record / draft → candidate | 补齐上述内容 |
| candidate → replay_verified | 当前内容与依赖下，至少两次不同运行给出确定、一致的重放结果；稳定失败须 `--known-issue` |
| replay_verified → regression | 重新核对当前重放资格；终态评审人、全部必需判据 confirmed 且适用，至少一次计入的真机重放；仅离线时须显式 `--allow-offline` 并说明依据 |

整例重放结论为不涉及、待确认、阻塞，或包含执行异常时，不计为确定重放，其后须重新积累至少两次；
失败保持原预期并登记缺陷。在可计入的整例重放中，单条断言明确不涉及不增加覆盖，也不清空它已积累的有效证据。
含条件的资产还须满足[逐断言分支覆盖](contracts.md#断言前置与操作选支56-第二切片)，未触发分支不能凭其他断言通过来补足。
内容或依赖变动后的旧记录不再支撑晋级，具体指纹边界见[知识与重放依赖](knowledge-rsi.md#探索路径--候选用例)。

## 把已有文字用例接进来

1. 放入 `data/knowledge/text_cases/`（Markdown/文本均可），记录来源（需求编号、文档链接）。
2. 由 Agent 读取并写成 `data/cases/<id>.json`：步骤映射到 `targets.json` 目标与 `states.json` 状态；
   预期结果写成知识条目（`knowledge add`，source.type=text_case）并在断言中引用。
3. `case lint` → 由用例负责人 `knowledge confirm` 判据 → 回归执行。
