# vpd_skill：VPD 手机 APP 智能测试 Skill v0.1

供 Agent 使用的工程化测试 Skill：Agent 读 [SKILL.md](SKILL.md) 做理解、计划、判断与沉淀；
确定性执行器 `vpdtest`（Python，零服务依赖）负责设备操作、观测、超时、证据、判定与安全边界。

- 首版范围：**HarmonyOS + 鸿蒙智行（`app.huawei.hmos.motor`）+ 底部导航 / 我的 / 设置 切片**，已在真机上运行。
- 阶段 3 增加乾崑（`com.yinwang.qiankunapp.hm`）样例：登录→切车→进入 VPD→算路（TC-QK-101）、地图收藏→按名搜索算路→读距离（TC-QK-102）、有人监护的激活→运动→暂停→后台→继续→完成（TC-QK-103）。**目前只在离线回放上验证，未在真机运行**。
- 登录、切车、进入 VPD、算路、改收藏需要运行时 `--authorize`；车辆运动另需 `--supervisor` 与用例 `safety`；自动探索不带运动授权。
- 选择理由：本机连接的是 Harmony 真机（SGT-AL10，OpenHarmony 7.0，已装 3.0.3.200），原工程 Harmony 选择器最完整（`MOTOR_HARMONY_CONFIG`），该切片覆盖页面切换、状态识别、弹窗、定位变化，且不需要车辆授权。
- 与原工程的关系：独立目录增量接入，不 import `app` 包、不改原有功能。复用边界见 [references/repo-reuse.md](references/repo-reuse.md)。

## 目录

```
vpd_skill/
  SKILL.md                 Agent 指令（精炼）
  references/              按需加载：契约、能力模型、工作流、知识与 RSI、宿主接入、复用边界
  vpdtest/                 执行器（CLI: python -m vpdtest）
    devices/harmony_hdc.py 真机适配（hdc / uitest / aa / bm）
    devices/replay.py      离线替身设备（回放 world.json）
  data/
    policy.json            运行边界（APP 白名单、禁止文本、副作用授权、预算）
    apps/motor_hm/         app.json / targets.json（定位策略）/ states.json（状态签名、中断、recipe）
    apps/qiankun_hm/       同上，另有 flows.json（参数化流程：登录、切车、进入 VPD、算路、收藏等）
    knowledge/             items.json（有来源的知识）、text_cases/（文字用例）
    cases/  suites/        结构化用例与用例集
    experience/<app>/      经验候选、已采纳 overlay、ledger（运行后自动生成）
  fixtures/worlds/         回放世界（合成数据）：motor_hm_*（base / drift / popup / locked / typo / crash / compact / fold / motion）、
                           qiankun_hm_base（未登录、有故障）/ qiankun_hm_ready（已登录、无故障）
  tests/                   pytest
  scripts/demo_offline.py  离线闭环演示
```

## 安装（Windows / PowerShell）

前提：Python 3.9+；真机需 `hdc`（DevEco Studio 或 OpenHarmony SDK 的 toolchains 目录）加入 PATH，手机开启开发者模式并授权 USB 调试。

```powershell
cd <仓库>\vpd_skill
# 首次若提示脚本被禁止执行：
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\install.ps1                  # 创建 .venv、安装固定版本依赖、生成 config.local.json、执行 doctor
.\install.ps1 -LinkSkill       # 另外把 Skill 挂到 %USERPROFILE%\.claude\skills\vpd-app-test（Claude Code）
.\install.ps1 -IndexUrl <公司 pip 镜像>
```

依赖只有 `lxml`、`opencv-python`、`numpy`（与原工程版本一致）和 `pytest`；不需要数据库、Redis、Flask 或网络服务。

## 配置

`config.local.json`（已 gitignore）：

```json
{"device": "hdc:<serial>", "app": "motor_hm"}
```

- `device`：`hdc:<serial>`（`hdc list targets` 查看）或 `replay:<world>.json`。命令行 `--device` 优先。
- 环境变量 `VPD_WORKSPACE` 或 `--workspace` 可指向另一份 `data/`（例如团队共享的知识与用例）。

## 运行

```powershell
.\vpdtest.ps1 doctor --device hdc:<serial>
.\vpdtest.ps1 run TC-MOTOR-HM-001 --device hdc:<serial>
.\vpdtest.ps1 suite motor_hm_smoke --device hdc:<serial> --fail-on failed blocked
.\vpdtest.ps1 explore --start car_tab --budget 30 --device hdc:<serial>
.\vpdtest.ps1 knowledge confirm K-MOT-001 --reviewer <你的名字>
# 乾崑（真机运行前请先阅读 references/exploration-method.md 的校准清单）：
$env:VPD_SECRET_QIANKUN_PHONE = '<账号>'; $env:VPD_SECRET_QIANKUN_PASSWORD = '<密码>'   # 只放在当前会话的环境变量里
.\vpdtest.ps1 run TC-QK-101 --device hdc:<serial> --env account_state=logged_out --authorize account_state vehicle_selection vpd_entry route_planning
.\vpdtest.ps1 run TC-QK-102 --device hdc:<serial> --env account_state=logged_in --env vehicle_in_vpd_map=yes --authorize vpd_entry user_data route_planning
.\vpdtest.ps1 run TC-QK-103 --device hdc:<serial> --env account_state=logged_in --authorize vpd_entry route_planning vehicle_motion --supervisor <监护人>
.\vpdtest.ps1 run TC-QK-103 --device replay:qiankun_hm_ready.json --env account_state=logged_in --authorize vpd_entry route_planning vehicle_motion --supervisor 离线演示
# 离线闭环演示（不需要真机）：
.venv\Scripts\python.exe scripts\demo_offline.py
# 测试：
.venv\Scripts\python.exe -m pytest -q tests
```

每次运行输出到 `runs/<run_id>/`：`report.md`、`run.json`、`events.jsonl`、`obs/<id>/{observation.json, tree.json, screen.png}`。
按 #32 裁决，UI 树、事件、运行汇总与截图保留采集原文，不脱敏，也不因系统覆盖物或窗口查询失败停止取证。截图仍受 APP 白名单约束，业务树仍要求属于本次目标 APP 且观测新鲜；空业务树另留整屏诊断证据。`runs/` 已 gitignore。用例中的账号/密码继续写成 `{"secret": "NAME"}`，由环境变量 `VPD_SECRET_NAME` 或 `config.local.json` 的 `secrets` 提供；生成资产保持外置参数引用。

探索/会话资产须经 `case review` 与人明确确认后的 `case approve --reviewer --basis` 才能晋级或批量使用；
单条未审核诊断仍标 informal。审核不 confirm 产品要求、不授予设备动作，也不自动提升成熟度。

回归、探索、单步决策、探索转用例、经验晋级的完整步骤见 [references/workflows.md](references/workflows.md)
与 [references/knowledge-rsi.md](references/knowledge-rsi.md)。

## 人工 Console（Windows / PowerShell）

```powershell
.\vpdtest.ps1 console --device hdc:<serial>
.\vpdtest.ps1 console --device hdc:<serial> --source snapshot
.\vpdtest.ps1 console --device hdc:<serial> --view-only
```

打开启动时输出的本机 URL。HTTP 服务开始监听后，画面还需要等待首帧；首帧未就绪、
画面不是 live 或帧龄超过 2 秒时，坐标和文字操作会明确拒绝，键盘返回／唤醒等恢复操作仍可使用。

`--source auto`（默认）和 `stream` 使用 uitest 帧流，需要可选依赖 `hmdriver2==1.4.3`；
缺少依赖、连续失败或持续静止时回退 `snapshot_display`。帧流只在画面变化时出帧：
出过首帧后约 5 秒无新帧标记为 stalled，再持续约 10 秒便自动回退；短暂静止后恢复出帧会重新计时。
计时依据是完整 JPEG 帧，协议文字和未完成的帧不会让旧画面保持可操作状态或延后回退。
`/api/status` 的 `fallback_reason` 和该 Console 运行目录的 `console_source` 事件记录回退原因。
回退后先等新的快照成为实时帧再操作；快照帧率依设备采集耗时而定。
回退后保持快照模式，重新启动 Console 才会再次尝试高帧率帧流。

Console 是人工控制入口，手动操作写入独立的 `console-*` 运行目录，不计为 Agent 自主用例或产品判据验证。

## 数据状态说明（重要）

- 2026-10-14 主干 `9e006ec` 快照（PR #41 已合入）：89 条知识（12 confirmed、76 unconfirmed、1 deprecated）；30 条资产（20 draft、10 exploration_record），正式回归 0。K-QK-001 已退役，采用的 K-AV-001 仍为 unconfirmed 且限定原 debug 范围。#34 的原始真机证据与独立复跑仍待验。
- 产品通过/失败要求每条 Oracle 明确 confirmed 且适用；入口选定、设备操作成功或框架 pytest 通过均不代替产品判据确认。人工 `knowledge confirm` 仍是独立治理动作。
- PR #36 保留为历史来源，PR #41 已在当前主干收敛19条候选定义；不能计为正式 VPD 覆盖。#32 原文取证与过时证据判定修复已随 PR #38 合入；策略误报 #35 已随 PR #39 合入。#30 资产人审已随 PR #40 合入。#34 原始证据与独立真机复跑仍待验收；首次真实算路前先处理 #42，扩大探索前处理 #43。详见 [方向与漂移审查](../docs/PROJECT_DIRECTION.md)。
- `car_tab` / `my_tab` 的状态签名已在真机（3.0.3.200）上校准：选中态 `selected=true` 标在 `TabBar` 下可点击的 `Column` 容器上，而非文字节点，见 `states.json` 的 `source`。`settings` 签名与 `update_dialog` 中断仍是 `review: pending`，需在真机上核对后改为 `reviewed`。

## 屏幕形态

- 形态分档在 `data/layout.json`：窗口宽度 vp = px ÷ 像素密度，默认 sm < 600 vp ≤ md < 840 vp ≤ lg。直板机竖屏都在 sm；
  折叠屏展开一般在 md 以上。阈值改这里即可，不改代码和用例。
- 回放世界：`motor_hm_base`（真机参数直板）、`motor_hm_compact`（小直板，压缩）、`motor_hm_fold`（折叠屏展开，左右分栏）。
  后两个是合成数据，只验证执行器，不代表鸿蒙智行的真实布局。

## 验证情况

下表包含历次采集与验证记录；旧 APP/build 的结果不表示本次已重新完成真机验证。取证行为以当前 #32 契约为准。

| 项 | 方式 | 结果 |
|---|---|---|
| hdc 输出解析（锁屏、错误序列号、包不存在时退出码为 0） | 真机采集的输出 → 单元测试 | 通过 |
| 目标不在前台时 `dumpLayout -b` 返回空占位树 → 识别为 `empty` 而不是"元素不存在" | 真机现象 → 单元测试 | 通过 |
| 前置检查：锁屏 / 解锁、亮屏、APP 版本 | 真机 `doctor` | 锁屏时 `screen_unlocked=false`；解锁后四项通过，版本 3.0.3.200 |
| APP 取证边界：只取目标业务树，截图采用 APP 白名单 | 离线回放 + pytest | 覆盖窗口/窗口未知照常取证；白名单 APP 切换保留截图、旧业务树 stale；未白名单或未知前台不留截图 |
| 满足预期判通过 / 违反预期判失败 / 锁屏与环境事实不足判阻塞 / 判据未确认或缺失判待确认 / 崩溃判失败 / 空树上的"不存在"断言不可判定 | 离线回放 + pytest | 通过 |
| 工具恢复不擦除首次异常；未评审弹窗不自动关闭；从不点击「更新」 | 离线回放 + pytest | 通过 |
| 定位漂移 → 经验候选 → 跨 3 用例 × 2 世界验证 → 采纳 → 回滚 | 离线回放 + pytest | 通过 |
| 探索（禁止文本跳过、覆盖声明）→ 探索记录 → 补齐并人工 approve → 两次新运行重放 → replay_verified；无真机重放拒绝晋级正式回归 | 离线回放 + pytest | 通过 |
| 无 UI 树页面走模板定位；模板多处匹配拒绝点击；无树时拒绝盲点坐标 | 离线回放 + pytest | 通过 |
| 真机执行完整用例 TC-MOTOR-HM-001（爱车 → 我的 → 上滑找到设置 → 返回） | 真机 `run` | 首次运行 S1 失败，定位为选中态签名写错（属性在容器上），校准后重跑 4 步全部执行成功，8 次动作约 39 s；结论为"待确认"，因为判据 K-MOT-* 未确认 |
| 真机探索 | — | 未运行：设置页含开关类控件，已加 `deny_node_types` 拒绝点击，但尚未在真机上验证 |
| 读取像素密度与折叠状态 | 真机 `doctor` | Mate 80 Pro Max：1320×2848 px，密度 3.375 → 391.1 vp（sm 窄屏），折叠状态 UNKNOWN、无 foldscreen 参数 → 直板 |
| 形态自适应后真机重跑 TC-MOTOR-HM-001 | 真机 `run` | 4 步全部执行成功；「设置」在可滚动 List 容器内上滑 1 次后找到，可见比例 100%；结论"待确认"（判据未确认）。重跑前一次运行因 hdc 通道重建（`E000004`）中断，据此修正为"观测失败·环境" |
| 新逻辑重放既有真机记录（11 次观测） | 本地真机树 → 新的状态识别与定位 | 页面状态与点击点和原记录完全一致；真机树不入库 |
| 乾崑 TC-QK-101：登录→切车→进入 VPD→默认车位算路，故障时「开始」置灰（opacity 0.4）→ A1/A2 观测成立 | 离线回放 + pytest | 通过（判据未确认 → 待确认）；按 #32 契约，输入后树和截图保留原文证据，参数汇总保持外置引用 |
| 乾崑 TC-QK-102：随机拖动地图、随机点选车位、收藏、重命名、设标签，按新名字搜索并算路，读到距离 356；清理后取消收藏 | 离线回放 + pytest | 通过（地图/收藏/标签控件都是假设，世界按假设搭建） |
| 乾崑 TC-QK-103：激活→运动中 TOR 提示→暂停→后台 10 s→拉回→继续→泊入完成；看门狗 armed/stopped ×2，无安全停止 | 离线回放 + pytest | 通过；故障世界在「开始」处判阻塞且不点击；车辆在后台自行恢复运动的变体由 moving_states 观测挂上看门狗 |
| 缺密钥 / 缺授权 / 缺监护人 | 离线回放 + pytest | 运行前判阻塞（环境），一步都不执行 |
| 同一用例跑三种形态：真机参数直板（391 vp）、小直板（360 vp，「设置」被底部导航遮住）、折叠屏展开（733 vp，左右分栏） | 离线回放 + pytest | 三种形态均通过；反例测试确认按 bounds 中心点击会点到底部导航、按整屏中线滑动滑不动左栏、整窗匹配会把设置页认成「我的」 |
| 现场选择器 `--select`：多匹配 / 被列表裁掉 / 只露一截都不下发并给出诊断；唯一匹配时证据记下节点、点击点、窗口与形态 | 离线合成世界 + pytest（`test_goal_loop.py`） | 通过 |
| 下发前复核：页面在观察与下发之间移动 → `target_stale`；前台被切走 → `foreground_changed`，都不下发 | 离线合成世界 + pytest | 通过 |
| 会话轨迹：成功、多匹配被拒、参数错误被拒、进程中断按顺序保留；`from-session` 排除动作必须写理由，中断的动作默认保留 | 离线合成世界 + pytest | 通过 |
| 判定修正：断言引用不存在的步骤 → 阻塞；步骤未执行的断言列为"尚未验证"；判据版本范围不符/未知 → 待确认；必需清理失败 → 阻塞；未确认安全停止优先于产品失败且保留产品失败 | 离线回放 + pytest | 通过 |
| 用例成熟度与产品状态：两次一致失败 + `--known-issue` → `known_failure`，预期不变，之后重跑报告"仍按同一特征复现"；通过与失败混合拒绝晋级 | 离线回放 + pytest | 通过 |
| `knowledge search`：K-AV-010（debug 版阔折叠观察）在 1.0.26.100 直板上适用性为"未知"，K-QK-USR-003 未确认不能作判据，来源是观察的"产品规则"标为现场观察 | pytest + 真实知识数据 | 通过 |

## 仍未验证的边界

- 真实弹窗文案、崩溃/无响应提示文案；TC-MOTOR-HM-002/003 未在真机上运行。
- 折叠屏、平板、横屏、分屏/悬浮窗：只有离线合成世界验证。鸿蒙智行在宽屏上的真实布局（导航是否移到侧边、是否分栏、分栏容器类型）、
  以及它在哪个 vp 宽度切换成宽屏布局（用户估计约 640 vp，ArkUI 默认 600/840 vp），都需要折叠屏真机确认后改 `data/layout.json` 和 `targets.json`。
- 折叠屏上 `hidumper -s DisplayManagerService` 的多屏与折叠状态字段、`const.window.foldscreen.type` 的取值，只按直板机输出推断，未见过折叠屏真机输出。
- 运行中折叠/展开、旋转：执行器会重新读取显示参数并记录 `layout_changed`，但没有真机或回放用例覆盖运行中切换。
- 真机上的模板定位（原工程模板图片 `app/assets/imgs/**` 不在仓库中，需要用 `template-crop` 从真机截图重新裁剪）。
- 长时间运行、多设备并发、弱网与后台切换等扰动；Android 适配器与其他 APP。
- 等待时间经验（`wait_timeout`）只在真机上才会产生，离线回放没有覆盖其自动生成路径。
- 乾崑：全部目标与状态签名只来自原工程代码和用户描述，未在真机上核对；地图是否为 XComponent、点地图是否弹车位卡片、收藏/重命名/标签的控件与文字、标签是固定选项还是自由输入、算路距离的文案格式，都是假设（`status=hypothesis`）。
- 乾崑：算路是否会唤醒车辆（原工程把算路结果记为「上电成功」）、「泊入车位」「靠边停车」本身是否会让车移动（本 Skill 从严按 vehicle_motion 处理）、后台返回后车辆是否自行恢复运动，都未确认。
- 真机上的中文 `uitest uiInput inputText`：非 ASCII/含符号的文本按单引号包裹下发，未在真机上验证转义是否正确。
- 车辆运动的真机运行：没有在真机上执行过任何激活/继续；安全停止、运动超时（含 `wait`/后置条件等待期间、超时后才观测到停止）、
  监视期间进程死亡检测都只在离线回放中验证，控车闭环在真机验证前不能宣称可用。
- 现场选择器只覆盖有 UI 树的页面：地图等自绘区域（XComponent）、无树页面、纯视觉定位不在覆盖范围内。
- 下发前复核每次多取一次 UI 树（真机约 1~3 s）。`act` 的动作预算按会话事件累计：进程在下发途中被杀、dispatch 事件没写下来的那次不计入。

#34候选资产的修订范围、18/19结构lint（218规则缺失）、来源/校准/XP状态及尚未核验的真实证据见[批次验收对账](../docs/BATCH_ASSET_ACCEPTANCE.md)。导入候选不自动人审，不计正式产品覆盖。
