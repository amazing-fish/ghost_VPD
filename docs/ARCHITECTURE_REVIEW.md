# 架构、技术债与冗余评估（2026-11-09）

评估基线：[`main@281e420`](https://github.com/amazing-fish/ghost_VPD/commit/281e42064e89419f1b73fb3291f36e7ea3d65fe8)（PR #196 合入后）。
远端当时无 open PR，未合入分支均为 10 月旧证据/旧分支，不计入结论。
上一版审计（main@3273f27 / 10-25 对账，原发现均已标交付）见
[历史版本](https://github.com/amazing-fish/ghost_VPD/blob/281e42064e89419f1b73fb3291f36e7ea3d65fe8/docs/ARCHITECTURE_REVIEW.md)，本版只把它当线索。

证据口径：阅读核心调用链源码；AST 生成模块依赖图；在仓库外的临时数据副本上跑 `case readiness`
和一条离线晋级链；全量 pytest；`gh` 读取 PR/Issue 并抽样。没有连接设备，没有读取凭据值，
没有核实工作机现场与 Legacy 部署。本文是固定基线的快照，引用其中数字前先按当前 main 重新核实。

## 结论

1. 现有分层 Observer → Executor/Dispatcher → Verdict → Case 门禁，正是“Agent + 确定性执行器 →
   可重复、可独立判定、证据可追溯的用例”所需的最小结构；run/act/flow/recipe 共用同一个 Executor。不需要重写。
2. 这条链从未产出目标交付物：41 个用例全是 `draft` 或 `exploration_record`，没有一个到 `candidate`；
   有效人审 0，正式回归覆盖 0。在临时副本上把 `TC-MOTOR-HM-001` 离线走完
   draft → candidate → replay_verified → regression 只需几分钟：卡点不是机制，而是门禁设计与工作优先级。
3. 复杂度主要来自三类历史累积：业务例外进入通用引擎；同一规则多处表达；门禁与一致性加固先于真实数据。
4. 收敛优先合并与删除：一个校验入口、一个结果投影、业务豁免回到数据、规范只写现行行为。
   不新增框架、数据库或执行引擎，也不按体积拆文件。

## 架构现状

```
宿主 Agent ── SKILL.md + references/（约 3.1k 行）
   │ CLI JSON + 退出码
cli.py → commands.py（13 个顶层命令）
   │ run              │ act                    │ case lint/promote/readiness
   ▼                  ▼                        ▼
runner.run_case ──→ Executor ◀── flow/recipe（states.json / flows.json）
 前置·授权·清理       │ locate·conditions·readrefs·recover
 校验拼装 ③          ▼                        校验再拼一遍 ③
 │               Policy.check_tap ◀── policy.json + 硬编码乾崑豁免 ①
 │                   ▼
 │               Dispatcher（最终复核·预算·解锁）→ devices: harmony_hdc | replay
 │                                                   └ 回调 dispatch/unlock ④
 ▼
Observer/RunStore → runs/<id>/{events.jsonl, obs/, run.json, report.md}
 ▼
verdict（断言·适用性·结论）◀── knowledge.py（103 条，22 confirmed）
 ├→ report.py / commands._run_brief / console/progress.py  各自投影 ③
 └→ cases.py（hash·人审·重放资格·晋级）  人审绑定 engine_hash ②

旁路：experience（3 候选/0 采纳）、explore、case_templates/scope_inspection、Console 手动操作
独立：根目录 Legacy run.py → Flask 0.0.0.0:5000 + Huey → app/service/vpd（与 Skill 零 import）

① 业务例外进引擎  ② 门禁先于数据  ③ 同一事实多处表达  ④ 下层回调上层
```

- 依赖方向：顶层 import 无环，但 72 处函数内相对导入掩盖了一个 12 模块的强连通分量
  （cases、conditions、readrefs、verdict、dispatch、unlock、sysdialog、session、observe、devices.*）。
- 状态归属：一次运行的可变状态集中在 `RunContext`（`workspace.py:85`），由 runner、Executor、Dispatcher、
  MotionGuard 分别写入；持久状态全在 `data/` 与 append-only 的 `runs/` 文件里。这个设计是对的，不需要状态数据库。
- 规模变化（10-08 → 281e420）：`execute.py` 56KB → 126KB，`cases.py` 22KB → 70KB，`policy.py` 11KB → 37KB，
  `verdict.py` 19KB → 47KB，`references/contracts.md` 19KB → 86KB。
- 补丁模式：最近 200 条记录中已合入 PR 112 个，标题为 fix 的 66 个；典型 fix 是源码约 10 行、测试 190~340 行、
  文档只增不删（#162、#179、#191）。测试共 1.65MB，源码 0.89MB。+127k 行中含证据与基线 JSON，不全是代码。

## 优先问题

按收益/成本排序；“把握”指对问题是否存在的把握。

### P1 人审绑定全量引擎哈希，任何合入都让审核失效

- 位置：`review_snapshot.py:8 engine_files`、`cases.py::_review_binding`，`engine_hash` 参与审核记录的相等判断。
- 证据：现有 9 份人审全部 stale，`changed_binding_fields` 均为 `dependency_hash,engine_hash`；#135 实测
  240/241/243/249 报同一“审核失效”。后加的 `engine_snapshot` 只解释哪个文件变了，没有缩小绑定。
  上一版本文自己写过“不要用整个引擎哈希粗暴清空所有重放”，审核绑定却恰好这样做。
- 影响：2026-11-08 一天至少合入 25 个 PR，人审追不上；任何纯重构都会作废全部审核，反向抑制收敛。
- 建议：审核绑定改为 content/dependency/policy/context 四个指纹，加显式 `result_contract` 版本
  （`dependency_hash` 里已有 `result_contract: 7`）；引擎快照保留为诊断，不参与相等判断。
- 收益高，成本小（约 20 行 + 测试），风险中（放宽门禁后，未 bump 版本的语义改动会漏网），把握高。**需维护者裁决。**

### P2 乾崑业务豁免写在通用 Policy，同一规则 JSON 与 Python 各一份

- 位置：`policy.py` 的 `_favorites_browse_source`（:368）、`_search_filter_source`（:433）、
  `_search_filter_group`（:468），以及 `check_tap` 中对命中项的过滤。
- 证据：硬编码 bundle `com.yinwang.qiankunapp.hm`、`VPD_HOME_VIEW`、“收藏及常去/收藏夹/充电桩/电梯口”；
  `policy.py:382` 逐字复制 `policy.json` 的 user_data 正则，只改 JSON 会让豁免静默失效；
  `qiankun_hm/states.json:152` 的 `^充电桩$: navigation` 是同一规则的第二份。
- 影响：每加一个页面例外都要改安全引擎，按 P1 还会作废全部审核。
- 建议：两种结构证明（首页收藏夹入口、搜索筛选组）留在代码；标签、bundle、容器 id 在 `states.json` 声明，
  并按 `policy.json` 规则 id 引用，不再比较 pattern 文本。
- 收益中高，成本中，风险中（安全边界，现有正反例结果必须逐项不变）；问题把握高，方案把握中。

### P3 同一组校验与门槛在多个入口各自拼装

- 证据：
  - definitions/validate_definitions/validate_guard/validate_input_obstruction 在 `cases.lint`（:683）、
    `runner.run_case`（:81-97）、`Executor._run_step`（:248）三处手工组合，且不一致（Executor 不跑 `validate_guard`）。
  - “观测是否可用”：`runner.py:267` 与 `execute.py:108` 各有一个相同的 `load()` 闭包，另有 `verdict._stale`、
    `readrefs.py:157` 和 `execute.py` 的 837/1077/1304 行。
  - 授权检查两份：`runner.py:183-202` 与 `commands._act_step`；顶层步骤“停止后继”两份：
    `runner.py:207-233` 与 `execute._run_children`，#162 只好两边各补一次。
  - 常量：`PASSED` 在 `commands.py:387`、`conditions.py:290` 被重写成字面量；`max_monitor_s` 的回退值 600
    在 `execute.py:482/615`、`cases.py:619` 各写一份。
- 影响：新增一条规则要改 N 处，漏一处就是一类新 Issue。
- 建议：一个 `cases.validate` 服务 lint 和 run；`RunStore.load_obs()` + `observe.usable()`；一个 sequencer
  服务顶层与 flow/recipe；常量只从 policy/verdict 取。
- 收益高，成本低到中（纯重构，有约 4200 项测试兜底），风险低，把握高。

### P4 运行结果被三处各自投影，标签已漂移

- 位置：`report.py:6-10`、`verdict.LABEL`（:14）、`commands._run_brief`、`console/progress.py`、`console/page.html`。
- 证据：#73 当年就是 Console 漏传 informal/asset_review；SKILL.md 写“执行异常/流程阻塞”，代码与报告是“执行错误/阻塞”。
- 建议：一个 `run_summary(run)` 投影，标签只从 verdict 来。
- 收益中，成本低，风险低，把握中高。

### P5 门禁与一致性的加固先于真实数据

- 证据：
  - 重放资格（`cases.py:886-1076`，约 190 行，含旧摘要时间兼容）：仓库内 8 条重放记录全都没有 started/finished
    和 deps，按现行规则一条也不计入。
  - 经验/RSI：3 个候选、0 个采纳、overlay 为空，但 #186/#189/#193/#196 仍在加固其中断语义；
    另有真实缺陷：`experience.py:104` 按 change 全等去重，同一键 `TC-QK-214:S3` 留下 11.9 与 11.7 两条。
  - Console 是只读监控，10 月以来约 2.0k 行源码、3.3k 行测试；#181/#188 修的是“单个 HTTP 应答混读两代文件”
    这类竞态，却按 P1 排期。
- 影响：每个后续 PR 都背着这些分支，排期被假设场景占满，业务资产未推进。
- 建议：冻结这三块的新规则，直到第一个资产真正到 replay_verified；Console 一致性降到 P3；
  `propose_from_run` 是否默认关闭，由维护者结合 RSI 方向决定。
- 主要作用是止血，成本几乎为零。数据层面把握高，“该不该做”是评估判断。

### P6 规范文档写成了变更日志，且与代码有实际冲突

- 证据：`contracts.md` 86KB，引用 15 个不同 Issue 编号，多节以 Issue 号命名，§2 一节 250 行、单行最长 1041 字；
  `PROJECT_DIRECTION.md` 实际是 PR 台账；`vpd_skill/README.md:139` 的知识统计停在 10-14 快照（89/12），
  281e420 实测 103 条、22 confirmed。
- 已证实冲突：
  - `SKILL.md:59` 说恢复只允许三种，`execute._recover`（:1469）实际有 5 种动作（另有 dismiss_system_dialog、
    dismiss_input_obstruction），Dispatcher 内还有解锁；
  - `knowledge-rsi.md:103` 说经验“验证通过后可自动采纳”，实际只能 `experience adopt`；
  - `SKILL.md:137` 示例 `--new-id TC-QK-201` 撞已存在用例，照抄退出 3；
  - 退出码 130（`cli.py:271`）未写入 `host-integration.md`；
  - `qiankun_hm/app.json` 的 `vehicle_boundary` 仍写进入 VPD/算路须 `--authorize`，与 task_side_effects 冲突。
- 建议：先修这 5 处冲突；`contracts.md` 按概念重写为现行行为，历史交给 git 与 Issue；
  `PROJECT_DIRECTION` 压到一页“当前状态 + owner”；`docs/` 分规范与记录两部分。
- 收益中高，成本中（写作量），代码风险无，把握高。

### P7 隐藏的循环依赖

- 证据：12 模块强连通，靠 72 处函数内导入掩盖。根源是几个放错位置的小工具：
  `verdict.error_attribution`/`collection_error` 被 dispatch、unlock、sysdialog、execute 反向依赖；
  swipe 校验放在 `session.py`；`conditions.py:309` 调 verdict 私有函数 `_stale`；
  设备层 `clear_text`/`wake_unlock`（`harmony_hdc.py:591/602`、`replay.py:442/457`）回调 dispatch/unlock。
- 建议：抽一个小 `errors.py`，swipe 校验并入 P3 的 case 校验模块。目标是下层不 import 上层，不追求零延迟导入。
- 收益中，成本低，风险低（旧名先 re-export），把握高。

### P8 测试成了另一套框架，并耦合生产数据

- 证据（测试审计实测）：基线 4229 项（4201 通过、28 跳过），本机 734s；测试文件互相 import 108 处，
  `RunContext` 手工拼装 17 处，另有至少 5 套从零搭建的工作区；`ws` 夹具每项复制约 1.17MB，约占总耗时 1/4。
  变异实验：22 条 confirmed 改为 unconfirmed，17 项变红；删除 `data/cases`，358 项变红，其中声明为有意集成的仅约 3 项。
  CI 路径过滤不含 `docs/`，但有 3 个测试读 `docs/`（如 `test_k89_case_display_evidence.py:20`）。
- 建议：`tests/support/` 放共享构造器；用 marker 区分引擎契约测试与生产语料测试；夹具按需复制；
  CI 路径补上被读取的 docs 子目录。
- 收益中，成本中（可分批），风险低，把握高。

次要但真实：`reported_definition` 在 19 个用例中都与现值不一致且代码不读；47 条知识全仓无引用；
K-QK-TPL-240 与 K-QK-IDX-240 重复且确认状态不同；`policy.json` 的 `explore_allows` 无代码读取。

## 目标结构

| 处置 | 内容 | 为什么更简单 |
| --- | --- | --- |
| 保留 | 五层链路与共用 Executor；文件化状态；Replay 与真机同接口；`execute.py` 不按动作拆文件（tap/input 共用“定位 → 复核 → 策略 → 前台 → 下发”） | 这是目标本身需要的结构，拆开只会复制同一串检查 |
| 合并 | `cases.validate`、`load_obs` + `usable`、sequencer、授权函数、`run_summary`、`errors.py` | 每条规则只有一处出处 |
| 迁移 | 乾崑豁免参数 → `states.json`（按规则 id 引用）；历史裁决 → git/Issue；状态快照 → `docs/records/` | 业务变化不再改引擎 |
| 删除（先确认） | 重放旧摘要时间兼容分支（先确认工作机无旧格式重放）；不读的影子字段与 `explore_allows`；无引用知识先标 deprecated | 去掉只在假设里存在的分支 |
| 冻结 | Console、经验/RSI、重放资格不再加新规则 | 等真实资产流过再决定 |

## 分阶段计划

### 近期：高收益、低风险，每项一个小 PR

1. Walking skeleton（不改代码）：motor_hm 001~003（判据已 confirmed，不需要探索人审）在真机上走到 regression，
   逐个记录卡在哪道门。按当前分工在工作机执行，离线部分维护侧可先做。
   验收：至少 1 个资产 `status=regression` 并带真机重放，或得到逐门卡点清单。
2. 修 P6 的 5 处文档冲突。验收：示例命令可直接运行，描述与代码一致。
3. P3/P7 纯重构：观测读取、常量、`errors.py`、`cases.validate`。兼容：旧名 re-export，`run.json` 字段不变。
   验收：全量测试结果不变；新增 AST 守卫，禁止 devices/dispatch/verdict 反向 import cases/session。
   时机：现有 9 份人审已全部失效，重构不会额外损失审核；但宜排在 P1 裁决之后，免得新审核再被作废。
4. CI 路径补上被测试读取的 docs 子目录。

### 随后：结构调整，需要先裁决

5. P1 审核绑定改为显式契约版本。验收：改注释或纯重构不使审核失效；改结果语义必须 bump `result_contract` 并因此失效。
6. P2 业务豁免迁到数据。验收：`policy.py` 不含乾崑字面量（grep 守卫）；现有收藏夹/筛选正反例结果逐项不变。
7. P3 剩余部分：sequencer、授权、`run_summary` 合并。验收：#162 类“停止后继”修复只需改一处；
   CLI、报告、Console 的标签同源。
8. 文档重组与测试支撑层（P6、P8）。验收：`contracts.md` 不再以 Issue 号作标题；删除 `data/cases` 只让语料测试变红。

### 暂缓或需确认

9. Legacy：仓库内零消费者，CI 不覆盖，`app/` 自 90545b9 后未改；仓库外是否部署无法从仓库证实
   （线索：`WORK_DIR` 依赖 vpd-service 目录名，Smart-Test 平台按 executor_ip 下发任务）。
   先确认工作机 :5000 与 Huey 进程是否仍在运行。不论删不删，`config.json` 与 `app/constant.py` 中的凭据
   都应轮换（已进入 git 历史）；若仍部署，`/api/vpd/upgrade/task/execute/` 无鉴权且监听 0.0.0.0，属于风险。
10. 维护者 2026-10-07 曾裁决新增“完成·待批”终态与 run 录屏，供事后批量确认；281e420 的代码和文档里都没有，
    `SKILL.md`、`PROJECT_DIRECTION.md` 仍写“判据 confirmed 才能 pass”。若该裁决仍有效，它比继续加固门禁更优先，
    因为它直接针对 80 条 unconfirmed 判据这个最大卡点。需确认是否已撤回。
11. 方向判断：经验自动提议是否暂停、Console 一致性是否降级、旧重放兼容分支是否删除。

## 最该减少的复杂度与第一步

最该减少的三种复杂度：

1. 同一规则多处表达：JSON、Python 常量、多个入口、多份文档各写一份。
2. 业务例外进入通用引擎：叠加 `engine_hash` 全量绑定，形成“业务变化 → 改引擎 → 人审全部失效”的链条。
3. 门禁先于数据的加固：重放时间兼容、经验中断语义、Console 跨文件一致性在真实资产流过之前持续加厚；
   文档膨胀主要是它的副产品。

第一步：用 motor_hm smoke 跑通一条真实的 walking skeleton，同时裁决 P1 与第 10 项。它不改代码，
却能回答哪些门禁真在起作用，也给后续每次重构一个真实的回归基线。

看似技术债、其实值得保留：

- 三层结果分开记录（`sent` / `executor_ok` / `post.met`），执行结论与产品结论分开，
  pending/blocked/error/not_applicable 互不替代。这正是“可独立判定”本身。
- 下发前最终复核（前台、锁屏、遮挡、重定位），以及 MotionGuard、safe_stop、action_required：安全边界，不是冗余。
- Replay 世界与 states/targets 的文案各写一份：离线 CI 的代价，可接受，顶多加一致性 lint。
- `execute.py`、`cases.py` 体积大：合并重复比按大小拆文件更有效。
- content/dependency 双指纹 + 人审 + 两次重放的门禁思路：要改的是 `engine_hash` 的范围，不是这套概念。
- `test_dispatch_regression` 的 AST 架构守卫。

关联：治理与后续审计沿 [#45](https://github.com/amazing-fish/ghost_VPD/issues/45)；P1 相关
[#135](https://github.com/amazing-fish/ghost_VPD/issues/135)、[#198](https://github.com/amazing-fish/ghost_VPD/issues/198)；
P5 相关 #181、#182、#186、#188、#192、#193。
