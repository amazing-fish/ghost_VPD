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

## 需要授权的运行（登录、切车、算路、控车）

1. 先读用例的 `authorizations` 与 `preconditions`，把需要的授权、账号前提、是否会让车移动告诉用户，由用户决定是否授予。
2. 账号/密码只放环境变量：`$env:VPD_SECRET_QIANKUN_PHONE = '...'`（值由用户在自己的终端里设置，不经过你）。
3. gated：`.\vpdtest.ps1 run TC-QK-101 --device $D --env account_state=logged_out --authorize account_state vehicle_selection vpd_entry route_planning`。
4. 控车：`.\vpdtest.ps1 run TC-QK-103 --device $D --env account_state=logged_in --authorize vpd_entry route_planning vehicle_motion --supervisor <监护人>`。用例必须声明 `safety`；运行中出现首次失败、超时（`max_motion_s`）或用例结束仍在动，执行器自动执行安全停止并单独记录。
5. 读报告：监视时间线（TOR、车速、故障文字的首次出现时间）、看门狗 `motion[]`、`safety_stops[]`。有 `action_required` 时立即转告用户人工接管。
6. 单步控车同理（加 `--app qiankun_hm`）：`act tap --target qk_pause` 不需授权（safe_stop）；`act tap --target qk_continue --authorize vehicle_motion --supervisor <人>`。
7. 监护人可以另开 `.\vpdtest.ps1 console --device $D --view-only` 实时看画面和步骤进度（`runs/run-*` 的第 n/N 步、看门狗状态）；
   需要人工接管时在手机上或控制台里操作，控制台会话日志记录每次手动操作的时间，便于和运行事件对照。

## 探索

1. 选起点（已声明状态）和目标：`.\vpdtest.ps1 explore --start car_tab --goal "我的页导航" --budget 30 --device $D [--prefer 设置 帮助]`。
2. 执行器在每个已声明状态上枚举可点击元素：命中禁止文本/禁止副作用/无文本标识的跳过并记录；
   每次尝试前经 recipe 回到来源状态（可从已知状态重新开始）；到达未声明页面只记录签名与证据。
3. 退出原因：`frontier_exhausted`（已声明状态都已展开）、`budget_exhausted`、`reset_failed`（回不到已知状态，停止）、
   `preflight_blocked`。
4. 读探索报告：覆盖只相对于已声明状态与本次观察。对未声明页面，打开 `obs/<id>/screen.png` 判断语义，
   需要纳入时提议 `states.json` 签名（人工评审）。

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
# 编辑 data/cases/TC-EXP-001.json 补齐后：
.\vpdtest.ps1 case promote TC-EXP-001 --to candidate
.\vpdtest.ps1 run TC-EXP-001 --device $D --record-replay   # 新运行中重放；至少两次通过
.\vpdtest.ps1 case promote TC-EXP-001 --to replay_verified
.\vpdtest.ps1 case promote TC-EXP-001 --to regression --reviewer <评审人>
```

## 把已有文字用例接进来

1. 放入 `data/knowledge/text_cases/`（Markdown/文本均可），记录来源（需求编号、文档链接）。
2. 由 Agent 读取并写成 `data/cases/<id>.json`：步骤映射到 `targets.json` 目标与 `states.json` 状态；
   预期结果写成知识条目（`knowledge add`，source.type=text_case）并在断言中引用。
3. `case lint` → 由用例负责人 `knowledge confirm` 判据 → 回归执行。
