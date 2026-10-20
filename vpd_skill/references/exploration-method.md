# 探索与沉淀方法论（以乾崑样例为例）

目标：把"在 APP 里摸索出来的东西"变成可复用、可验证、可回滚的资产，同时不把猜测当成规则。
核心是**先分级、再建模、离线证明、真机校准、按门禁晋级**，每一步都留下来源。

## 1. 本次沉淀的乾崑知识图（按来源分级）

| 来源等级 | 内容 | 落地位置 | 能否支撑判定 |
|---|---|---|---|
| 真机只读观测 | 包名 `com.yinwang.qiankunapp.hm`、`EntryAbility`、版本 1.0.26.100（`bm dump -n`，未启动 APP） | `app.json.sources` | 只作环境事实 |
| 原工程代码 | 目标选择器（`mhero_config.py` 原样 xpath，附行号）；登录、按车架号后 6 位切车、ADS 进入、默认车位算路、开始/确定激活、暂停/继续/退出的流程；「开始」可用 = Button opacity 1.0；不可用提示持续 10 s 才算失败；完成文案；算路可能唤醒车辆 | `targets.json`（status=unverified）、`flows.json`、`states.json`（review=pending）、K-QK-002…007；旧 K-QK-001 已按 #33 废弃 | 需负责人确认；入口选定 K-AV-001 但仍未确认、仅限原 debug 范围 |
| 用户描述 | 测试车 M5/末位 7885；当前有故障应无法激活；收藏→重命名→标签→按名搜索→算路→读距离；控车始终允许、探索受限 | K-QK-USR-001…004（source=reviewer） | unconfirmed，需负责人确认 |
| Skill 推测 | 地图是 XComponent、点地图弹车位卡片、收藏/重命名/标签按钮文字、标签是固定选项、常去地点列表结构 | 9 个 status=hypothesis 目标、2 个 hypothesis 流程 | 不能晋级，`case lint` 会拦 |

按层整理（与 [capability-model.md](capability-model.md) 一致）：

- **动作**：tap、input、swipe（含可复现随机滑动）、back/home、launch/resume/stop_app、wait、read、monitor。
- **原子能力**（53 个目标，按 `side_effect` 统计，以 `targets.json` 为准）：导航 13、账号 11、算路 8、改用户数据 8、
  车辆运动 5、只读 4、安全停止 2、切车 1、进入 VPD 1；其中 9 个为 hypothesis（地图、收藏/重命名/标签等）。
- **状态**：爱车/探索页、车辆列表、VPD 加载/首页、算路中/算路结果、运动中/已暂停/已完成；中断：安全码（阻塞）、版本更新（阻塞）、应用异常。
- **功能流程**：`login_password`、`switch_vehicle`、`enter_vpd`、`plan_default_spot`、`search_and_plan`、
  `favorite_random_spot`（假设）、`unfavorite`（假设）。
- **用例**：TC-QK-101（故障时不可激活）、TC-QK-102（收藏→搜索→算路→距离）、TC-QK-103（有人监护的激活→运动→暂停→后台→继续→完成），
  以及后续探索沉淀的 TC-QK-2xx（清单见 `data/cases/`）。

## 2. 探索 → 沉淀的闭环（目标优先）

默认从一句文字目标开始，现场观察优先于旧资产。原工程代码、回放世界、预写的流程和用例都是可选的线索，
不是前提：它们描述的是"当时脚本这样做"或"Skill 这样猜"，要在真机上核对后才算数。

1. **检索**：`knowledge search`，把命中分成产品要求 / 操作经验 / 现场观察，逐条判断是否适用于本次
   APP 版本与屏幕形态（不能比较就写"未知"）。没有独立判据时记 `source.type=test_goal` 的 unconfirmed 条目，不补编。
2. **现场观察与探索**：`observe` + `act --select/--target`。原始会话完整保留观察、请求、被拒、失败、超时、恢复、
   中断的顺序（`session <name>` 查看）；选择器的多匹配/不可见/部分可见/执行前失效都会给出诊断而不是盲点。
3. **固化**：`case from-session` 从会话提炼候选用例：步骤用选择器（证据里保留解析出的节点、点击点与形态），
   每一步关联到原始事件与观测；排除动作必须写理由。补齐目的、前置、数据、独立判据、清理、`knowledge_used`。
   晋级或批量使用前须[人工审核](workflows.md#探索资产人工审核30)，Agent 不得自行批准；单条诊断可先非正式取证。
4. **新运行中验证**：`run` 候选用例得到判定（产品结论与工具/环境/知识限制分开）；`--record-replay` 记入重放。
   稳定复现产品失败的用例用 `--known-issue` 固化，不改预期。
5. **沉淀可继承的知识**：真机上确认过的选择器可登记到 targets.json（附 run/obs）；某次看到的现象用
   `kind=observation` 记下（不是判据）；可复用的操作方法用 `kind=operation` 记下。
6. **可选：旧资产与离线证明**。需要复用原工程时再读它（选择器、等待时长、失败判据，写明 `source`）；
   需要证明执行器/判定逻辑时再搭合成回放世界（文案标「占位」）。这两步**都不证明产品行为**。
   `explore` 只在已声明状态上枚举可点元素，适合回归覆盖检查，不是目标驱动的探索。
7. **准入与维护**：知识 `knowledge confirm`；经验走 candidate → validate → adopt，可 `rollback`；
   用例按[晋级条件](workflows.md#晋级条件)逐级验证，不能用资产审核代替产品判据确认或动作授权。
   假设目标未校准前 `case lint` 阻止晋级；
   已被真机证伪的流程（如 `login_password`，C8）标 `falsified_on_device`，lint 会指出引用它的用例。

## 3. 与自进化 Skill 实践的对应

| 实践 | 做法 | 本 Skill 的取舍 |
|---|---|---|
| [Voyager](https://arxiv.org/html/2305.16291) | 自动课程、可执行技能库（按描述向量检索）、环境反馈 + 执行错误 + 自我验证的迭代 | 采用"技能库 + 环境反馈"：流程/目标就是技能库，执行器把执行错误与后置条件结果回写证据。不采用"模型自评即成功"：自我验证只能是建议，通过/失败只来自确定性证据 + 已确认判据 |
| [CoEvoSkills](https://arxiv.org/html/2604.01687) | 生成器与"代理验证器"分离，验证器看不到生成过程；通过后再用隐藏测试给不透明的成败信号 | 对应"候选经验不能自己验证自己"：`experience validate` 在来源用例之外的用例与世界上重放，与基线比较；覆盖不足保持 candidate |
| [MUSE-Autoskill](https://arxiv.org/html/2605.27366v2) | 创建 → 评估 → 注册的门禁；每个技能附记忆文件记录失败模式；合并、剪枝；目录式渐进加载 | 门禁对应 lint / asset approve / promote；失败模式记在目标/状态的 `note`、`review_note` 与 known_gaps；SKILL.md 精炼、references 按需读取 |
| [Dynamic Agent Skills 综述](https://arxiv.org/html/2607.10113v1)（[阅读清单](https://github.com/yubol-bobo/Awesome-Dynamic-Agent-Skills)） | 八阶段生命周期（取证、提议、验证准入、组织存储、检索组合、维护修复、蒸馏迁移、治理溯源）；准入边界与 tentative/promoted/rolled back；"只增长不等于学习"，需要负向算子 | 取证/提议/准入/治理直接对应第 2 节；溯源用 `source` + `ledger.jsonl` + overlay 版本；负向算子是 reject/rollback 与"假设不校准不晋级"。按 APP 分目录存放，避免扁平库膨胀 |

不采用的部分：权重级蒸馏（无训练环节）、无人值守的自动生成用例直接进回归、让模型改 policy。

## 4. 控车场景的探索边界

控车**始终允许**，但只在有人监护的回归/单步里执行：`--authorize vehicle_motion --supervisor <人>`，
用例声明 `safety`（安全停止序列、`max_motion_s`、`stopped_states`、`moving_states`）。
自动探索不带运动授权：无人保障时可能挡路、终点不对卡死，所以探索只把「开始」「继续」「泊入车位」等入口记为跳过，
由人把它们写成 draft 用例后再有人监护地运行。

需要观察的显示都用 `monitor` 时间线记录（状态序列、新出现的文字、`watch` 命中、首次出现时间），并用 advisory
`visual_review` 交人复核：TOR/接管提示、运动中车速与剩余距离、异常与故障提示、暂停后的页面、后台返回后的页面
（仍暂停 / 车已自行恢复运动 → 看门狗按 `moving_states` 观测挂上 / 冷启动）、完成页。

## 5. 真机校准清单（乾崑，按顺序）

1. 测试人员解锁手机，确认前台可以切到乾崑；设置 `VPD_SECRET_QIANKUN_PHONE`、`VPD_SECRET_QIANKUN_PASSWORD`（只放环境变量）。
2. `run TC-QK-101 --env account_state=logged_out --authorize account_state vehicle_selection`：
   核对探索/爱车页选中态、账号区、车辆列表、ADS、默认车位、算路结果与「开始」opacity。
3. 用 `observe` 在 VPD 首页看地图区域在 UI 树里是否有子节点、点一下是否弹车位卡片，再决定 `qk_map` 与收藏类目标的真实选择器。
4. 校准后跑 TC-QK-102；车辆无故障且有人在车旁时再跑 TC-QK-103（`--supervisor`）。
5. 核对过的目标把 `status` 改为 `calibrated_on_device`，状态把 `review` 改为 `calibrated_on_device`，都写上 run/obs；
   判据由负责人 `knowledge confirm`。
