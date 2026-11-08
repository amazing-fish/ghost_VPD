# 项目方向与执行安排

2026-11-04用户反馈落实见[执行安排](USER_FEEDBACK_20261104_EXECUTION.md)：搜索组合先准备三类收藏并独立读回，
组合语义保持待决；反馈分开用户提示、手机云端和车端DDI，新增动作绑定短提示留证。六条手工用例已收录，自动suite/实机验收仍待现场校准。

本页按对应代码基线维护工程能力、下一步验收和责任归属；具体运行须另记实际commit、APP/build、设备及来源资格。
工作机搜索的剩余验收见[搜索交接](WORKMACHINE_SEARCH_20261027.md)，手工用例以[十月来源及跨月差异](baselines/README.md)为准。
工程合入、动作发送、产品结论、资产成熟度和正式覆盖分别记账；以下交付不增加正式业务覆盖。
交付单位仍是可独立判定、重复执行、证据可追溯的VPD内功能用例；登录、解锁、系统弹窗和进入业务页属于前置能力。

2026-11-08 当前分工：工作机只提交 Issue 文字及结构摘要，原始文件、图片、日志留在工作机，不外发。
维护侧据获准摘要推进离线工程，不能声称核过原件；需要截图判断的事项明确缺图及其影响，不以索取源包替代现场记录。
本段限定当前轮次，历史已入仓的来源材料仍保留原版本。

## 已交付能力与使用边界

| 工程交付 | 当前可用范围 | 不能据此宣布完成的验收 |
| --- | --- | --- |
| [#112](https://github.com/amazing-fish/ghost_VPD/pull/112)、[#113](https://github.com/amazing-fish/ghost_VPD/pull/113) | 首页起点/目的地文案识别；坐标下发事实、最终坐标及受控协议框诊断 | 金桥实际到达路径、自然出现的起点分支、登录协议框checked后置；签名不证明车辆图内 |
| [#116](https://github.com/amazing-fish/ghost_VPD/pull/116) | 文本断言限定到唯一、完整可见的已登记当前树容器，拒绝跨行拼证 | 真实实体、列表完整性及字段产品契约 |
| [#120](https://github.com/amazing-fish/ghost_VPD/pull/120) | 两个搜索输入目标指向实际SearchField；空/非空查询共用结构状态依据，输入按navigation处理 | 清空中间值、实际新值及各现场后置；输入成功不证明搜索业务通过 |
| [#121](https://github.com/amazing-fish/ghost_VPD/pull/121) | 本次运行中更早、合格的独立read值可供后继输入和范围文本断言消费；保留源步骤/obs | 真实收藏集合/空态、实体字段、名称与编号维度及两账号/园区重放 |
| [#122](https://github.com/amazing-fish/ghost_VPD/pull/122) | 十月150条源快照及逐行跨月对应已入仓，九月151条保持历史 | 来源缺项、消失用例去向、列表新规则及逐例验收；63个非空自动化标记不是覆盖 |
| [#123](https://github.com/amazing-fish/ghost_VPD/pull/123) | 未验证名称结果行目标按当前查询精确选取唯一同行，计入符合结构的已知屏外同名行；配套断言组核状态、实际输入及独立读值 | 不提供真实收藏源，不覆盖编号查询，不自动启用230 v5或扩大资产人审范围 |
| [#125](https://github.com/amazing-fish/ghost_VPD/pull/125) | 当前乾崑前台、单栏首页及纯收藏夹接收行共同证明浏览tap，收窄宽泛收藏关键词误判 | 目标仍unverified；不证明完整收藏内容，不放行输入/清空或其他写入 |
| [#126](https://github.com/amazing-fish/ghost_VPD/pull/126) | 登记空查询下的受限电梯口目标，拒绝text/originalText冲突并在新观测撤权 | 目标仍unverified；不启用235，不证明标签选中、结果集合或组合规则 |

2026-11-08 的新增工程交付（均不增加正式业务覆盖）：

| 工程交付 | 已解决 | 仍保留的边界 |
| --- | --- | --- |
| [#161](https://github.com/amazing-fish/ghost_VPD/pull/161) | 240 支持显式 Text 叶节点 scope | 仍须独立定位/真值/可见性与人工审核，不自动校准或晋级 |
| [#158](https://github.com/amazing-fish/ghost_VPD/pull/158) | HDC 子进程不再额外继承宿主 PIN 环境键 | 不是安全的一次性 PIN 输入通道；现场仍需合规输入，不能把环境隔离算作完整解锁 |
| [#162](https://github.com/amazing-fish/ghost_VPD/pull/162) | 当前动作效果未知且需要接管时，停止顶层/flow/recipe 的依赖后继，并保留原失败来源 | 外层独立补偿与必要安全停止仍执行；离线证据不证明实机安全停止，旧资产审核可能需人工复核 |
| [#156](https://github.com/amazing-fish/ghost_VPD/pull/156)、[#163](https://github.com/amazing-fish/ghost_VPD/pull/163) | Console 区分最近读取与历史日志，切换运行不串结果；单条损坏证据隔离、修复后重读，详见[运行监控](../vpd_skill/references/console-monitor.md) | 刷新成功不证明进程存活；部分记录不代表完整进度；只读展示不增加真机验收或正式业务覆盖 |
| [#165](https://github.com/amazing-fish/ghost_VPD/pull/165) | 同 run_id 的冲突摘要不能覆盖既有证据；不确定重放不能掩盖更早的确定失败特征 | 保留原记录与人工审核门禁，不自动晋级；合成一致性验证不证明生产事故或实机结果 |
| [#171](https://github.com/amazing-fish/ghost_VPD/pull/171)、[#176](https://github.com/amazing-fish/ghost_VPD/pull/176) | Console可检索全部历史，明确手动快照；会话与监控按物理换行读证据，截断UTF-8整条留证、不把可解析前缀当完成事件 | 不证明进程存活，不自动修复或重签；既有截断续写阻挡保持，工程通过不增加业务覆盖 |
| [#179](https://github.com/amazing-fish/ghost_VPD/pull/179) | 同次运行的知识判据、摘要和依赖绑定开始时的同一只读知识快照 | 中途知识编辑从下次运行生效；当前晋级仍核最新知识，历史不追改，不声称冻结全部文件系统输入 |

本轮证据连续性补齐：[#172](https://github.com/amazing-fish/ghost_VPD/pull/172) 原子独占分配运行根目录，
只允许显式会话恢复，冲突在设备创建前失败；[#173](https://github.com/amazing-fish/ghost_VPD/pull/173)
限定会话为 runs 下合法单层目录并拒绝链接入口，不扩大为通用操作系统沙箱。
[#175](https://github.com/amazing-fish/ghost_VPD/issues/175) 观测编号按规范数值及既有事件引用续接，
缺号、无关条目及遗失目录不会覆盖或重绑定仍有记录的证据；窗口消费者同样按规范编号数值排序。
正常连续空占位仍跳过，损坏/截断日志不自动修复；会话身份、预算与新鲜窗口门禁不变。
离线/CI 工程验证不证明用户家庭机现场、实机业务行为或正式覆盖。

资产入口另见 [#151 母版与冻结实例](https://github.com/amazing-fish/ghost_VPD/pull/151)、
[#153 只读 scope 诊断](https://github.com/amazing-fish/ghost_VPD/pull/153)；重放证据一致性见 [#164](https://github.com/amazing-fish/ghost_VPD/issues/164) 与已合入的 #165。

具体语法、限制和合成示例见[执行契约](../vpd_skill/references/contracts.md)。以上能力沿既有
`Observer → Executor/Dispatcher → Verdict → Case`交付，不增设平行执行、规划或判定引擎。
[#124](https://github.com/amazing-fish/ghost_VPD/pull/124)已合，条件肯定证据与身份来源、同栏状态签名及条件实际引用的假设目标门禁已收紧；生产事实提供者仍沿#56验收。
收藏夹浏览与电梯口目标均保持unverified；入口工程不替代实机校准、独立收藏来源或标签业务验收。

## 下一阶段：完成真实来源与最小业务闭环

当前缺口已从“实现通用读值绑定”转为“取得独立、可校准的真实来源，并修订生产消费者”。
活跃230仍是历史v2；[来源存档](evidence/workmachine-search-20261027/README.md)中的230 v5、231 v2、234、235及两项知识未自动启用。
首页“收藏及常去”的混合卡不独立证明完整收藏集合、收藏类型、楼层或编号；不能把查询结果反过来当搜索期望。

| 单一验收归属 | 维护侧下一步 | 工作机提供的现场及完成边界 |
| --- | --- | --- |
| [#65 来源与总账](https://github.com/amazing-fish/ghost_VPD/issues/65) | 接收已有源件、配套定义/形态迁移，逐例核content/deps/engine/policy/context、人审和清理；不再等待十月快照重导 | 补232/233/236–239等尚缺原件与来源缺项。既有候选、知识、历史差异和完整业务验收继续记账，来源存档不等于覆盖 |
| [#111 数据驱动实现](https://github.com/amazing-fish/ghost_VPD/issues/111) | 校准真实收藏源及行目标，将独立读值、实际输入和同一行断言接入生产消费者 | 完整收藏/明确空态、实体身份、名称与编号独立来源及两账号/园区复验；通用能力或名称同行切片不完成整单 |
| [#80 搜索业务](https://github.com/amazing-fish/ghost_VPD/issues/80) | 消费#65来源及#111能力，保留车辆/园区/实体身份、楼层、标签、组合筛选和清理口径 | 当前查询及真实接收者、结果/空态、同名异实体和组合负例；原#83/#84/#88条款仍保留，不能用单个名称成功替代 |
| [#108 字段](https://github.com/amazing-fish/ghost_VPD/issues/108)、[#109 空白终态](https://github.com/amazing-fish/ghost_VPD/issues/109) | 分别接入独立字段契约、请求/加载终态及空态要求 | 原始行与字段归属、该次请求终态；材料矛盾或终态不足保持pending，不擅自确认编号可选或产品缺陷 |
| [#118 现场（承接#107）](https://github.com/amazing-fish/ghost_VPD/issues/118)、[#64 登录与输入](https://github.com/amazing-fish/ghost_VPD/issues/64) | 维护实际到达路径、账密主路径/已登录复用、输入/清空及协议框契约；#110剩余条款已由#64承接 | #118统一交接最小现场。自然出现的起点分支再验，不为造现场强制登出/切园区；清空未知不等于已清空 |
| [#56 生产事实](https://github.com/amazing-fish/ghost_VPD/issues/56) | 沿既有provider/guard补独立正反证及身份、时点、动作有效范围，不建新事实总线 | Windows实测；首页/回自车/历史记录或业务`--env`不证明当前车辆图内，事实不足不制造NA或通过 |
| [#54 触摸归属](https://github.com/amazing-fish/ghost_VPD/issues/54)、[#58 手势](https://github.com/amazing-fish/ghost_VPD/issues/58)、[#82 几何](https://github.com/amazing-fish/ghost_VPD/issues/82) | 分别处理表面接收者、真实手势和同帧窗口/裁剪；保留独立验收 | 实际父链/窗口、工具版本/帮助与px/s、真实表面后置；诊断、截图变化、basis或raw注入不代替证明 |
| [#45 治理与维护](https://github.com/amazing-fish/ghost_VPD/issues/45) | 按真实缺陷推进知识依赖/测试隔离、Legacy、Oracle资格与证据治理 | 判据确认、资产人审和当前重放独立办理，不以文档或Issue关闭扩大批准范围 |

同一缺口只由表中的owner维护验收条款，其他Issue引用它；保留未满足独立目标，不为减少数量关闭任务。
工作机只提交Issue/评论、原件及最小复验；维护侧修改代码、建PR、审完整diff和调用链，再按授权合入。
接管、证据和单一写入人约定见[协作规则](ISSUE_COLLABORATION.md#工作机-issue-交接与维护侧审查)。

## 判定和执行纪律

先用当前、有效、独立于被测结果的证据确定适用分支，再执行产品断言：明确不适用为not_applicable；
缺失、过期、矛盾为适用性未知，继续取证或pending；采集/执行异常保留error及归因；策略、预算或外部输入正常交接为blocked。
全部分支不适用不得聚合为passed。产品pass/fail还须判据confirmed且适用；判据未确认不阻止原件或draft交付，确认不追改历史pending。

离线CI/Replay属于E1，不能冒充当前设备验收。raw HDC/uitest不作为绕过Dispatcher的默认路径；
车辆动作、其他副作用、清理及接管按[工作流](../vpd_skill/references/workflows.md)执行。
`act swipe`已支持已知唯一滚动容器内的相对起止点及显式px/s；实际设备工具与表面效果仍沿#58核验。
正式资产不写像素，未知表面不静默改为普通滚动。折叠态可继续取证，其他形态另行补测。

## 历史与详细证据索引

- [十月当前来源、九月历史映射](baselines/README.md)：快照ID区分版本；不覆盖九月，不把旧映射改前缀当新验收。
- [工作机搜索源件存档](evidence/workmachine-search-20261027/README.md)与[session-s293历史现场](WORKMACHINE_SEARCH_EVIDENCE_20261027.md)：历史原件不随工程合入改写；后续材料及当前缺口沿#118和搜索交接登记。
- [K89逐例交付](K89_0228_CASES.md)、[main补交付索引](evidence/K89_MAIN_DELIVERY_20261027.json)、[批次验收](BATCH_ASSET_ACCEPTANCE.md)：保留各自固定基线和批准范围，不作为实时库存。
- [架构审计](ARCHITECTURE_REVIEW.md)：已有flow/state、错误归因、恢复、证据与成熟度工程的详细背景；新的改动仍落在现有链路。
