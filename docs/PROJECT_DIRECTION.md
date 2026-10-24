# 项目方向与执行安排（2026-10-24）

核对基线：`main @ 244a71ec0205f15dd8adc8ad962372f87fed8e8a`（#81 已合入）。本批#82/#84的工程变更与待现场验收项分列；具体合入状态以对应PR为准，未合入候选不计入上述基线。PR #36 仅保留历史来源，不再整包合入。历史实施范围从 Git 历史与对应 PR 追溯。

## 交付目标与当前缺口

交付单位是可独立判定、重复执行、证据可追溯的 **VPD 内功能用例**。沿用 `Observer → Executor/Dispatcher → Verdict → Case`；操作能力落在现有 `flow/state/targets/dialogs`，不新建另一套执行、规划或判定引擎。登录、解锁、系统弹窗及进入业务页属于前置能力，不能计作 VPD 功能覆盖。

工作机报告启境1395在乾崑1.0.26.100折叠态的8项资产、16次整例执行，其中5项为VPD功能方向、3项为基础检查；产品结论均为 pending。该报告不等于远端资产已交付，也不等于确定重放或正式覆盖。

在上述244a基线，仓库有31份case（21 draft、10 exploration_record）及90条knowledge；工作机的230新fixture、231–234与形态迁移仍未交付远端。主干TC-QK-230 v2保留历史园区fixture，不代表1395。本批工具补丁不增加业务资产；应先把已有工作变成可检查的draft交付，再按当前内容与依赖补齐验收。判据未确认不阻止单条诊断或draft入仓，之后的确认也不把旧pending追溯改成确定重放。

## 当前执行顺序

| 优先级 | 工作包 | 交付与完成边界 |
| --- | --- | --- |
| P1 | [#65](https://github.com/amazing-fish/ghost_VPD/issues/65)、[#80](https://github.com/amazing-fish/ghost_VPD/issues/80) | 交付工作机已有1395资产，逐条核对名称、编号、标签、楼层及结果身份。名称查询不替代编号查询；保留适用规则、当前fixture、原始run与cleanup，pending不计正式覆盖。历史143行与NEXT空列先核原表，不自动回填平台 |
| P2，当前修复 | [#84](https://github.com/amazing-fish/ghost_VPD/issues/84) | 已有StateModel/Policy中限定搜索筛选结构的导航语义候选；不全局删除“充电”限制。仍需当前1395父链、输入后页面身份及点击后结果/空态校准 |
| P2，当前修复 | [#82](https://github.com/amazing-fish/ghost_VPD/issues/82) | 共享裁剪证据、拒绝未知坐标接收者并投影最终策略；不放宽Swiper或窗口计算。947/1264矛盾仍须同帧独立窗口、祖先链与真实按钮后置，不能把诊断交付称为几何适配完成 |
| P1，现场分支验收 | [#64](https://github.com/amazing-fish/ghost_VPD/issues/64)、[#31](https://github.com/amazing-fish/ghost_VPD/issues/31) | 以量产账密登录/已登录复用为主路径；账号管理的短信验证是旁路，不是普通VPD执行的必经门禁。未知二级页、更新弹窗和黑屏按实际观察取证，不盲目Back或杀进程 |
| P1，生产事实接线 | [#56](https://github.com/amazing-fish/ghost_VPD/issues/56)、[#34](https://github.com/amazing-fish/ghost_VPD/issues/34) | 判定引擎已交付；补适用的独立现场provider、规则、人审与当前指纹下的真机复跑。一般探索不以固定故障前置为唯一入口；215/226/228业务裁决继续暂缓 |
| P2，独立场景 | [#83](https://github.com/amazing-fish/ghost_VPD/issues/83)、[#54](https://github.com/amazing-fish/ghost_VPD/issues/54)、[#58](https://github.com/amazing-fish/ghost_VPD/issues/58)、[#52](https://github.com/amazing-fish/ghost_VPD/issues/52) | 分别处理身份绑定/产品规则、影像表面触摸归属、真实手势及知识沉淀；不把它们混成搜索策略豁免或通用几何放行 |
| 持续治理 | #9 / #45 / #29 | 随实际缺陷收敛重复检查、生产知识解耦与证据准入，不预先增设框架层 |

## 工程交付与架构收敛

以下已交付工程不应重新列为待实施：

- #59/#60/#68：执行错误、适用条件及复合结果独立投影；#69/#70：ensure_state终态与共用结构检查。
- #61/#62/#63：211/212经验归入flow/state，现有消费者使用ensure_state；211/212仍为operation_reference_only，不计业务执行口径。来源确认及生产校准沿#31。
- #71/#72/#73：Python3.12与双平台CI、实际判据/模板依赖绑定、Console证据层级。
- #66/#76：已知选择菜单及延迟遮挡的受控恢复；#77/#79：PIN发送未知的防重入与按设备显式启用的空白键盘识别；#78：USB单选弹窗的一次受控关闭。它们不证明所有现场分支均已验收。
- #51/#67：任务范围入口/算路许可及预算可观测性；#49/#55已关闭。入口与算路不逐次请求授权；PIN解锁沿既有授权，其他副作用仍按当前策略。
- #81：TC-QK-230 draft及返回首页导航修复；产品判据、1395 fixture、结果身份、按资产来源核对必要的人审及确定重放仍待验收。

本批渐进重构收敛同一事实的来源和消费者：UiTree提供几何，StateModel提供当前页面动作依据，Policy判动作语义，Dispatcher下发前复核，必要时重定位并重算，后置观察证实声明效果。#82的CLI保留初检审计、展示最终拒发；#84复用已有来源通道，不新增另一套配置或执行路径。具体契约见[执行契约](../vpd_skill/references/contracts.md)，职责与剩余技术债见[架构审计](ARCHITECTURE_REVIEW.md)。

## 前置、选支与产品结果

先根据当前、有效、独立于被测结果的证据选分支，再应用该分支的产品断言：

| 前置证据 | 处理 | 结论与覆盖 |
| --- | --- | --- |
| 明确满足 | 执行适用分支 | 适用且已确认判据及有效结果证据才得出pass/fail |
| 明确不满足 | not_applicable | 不计失败、通过或覆盖 |
| 缺失、过期、矛盾 | 适用性未知 | 继续取证或pending，不能跳过制造通过 |
| 采集/执行异常 | error，保留阶段与归因 | 与产品结论分开，不计覆盖 |
| 策略、预算或外部输入正常交接 | blocked | 不机械改为error |

全部分支不适用不得聚合为passed。`conditions`、`step.when`、`assertion.guard`及规则的`condition_requirements`共用现有契约；来源关联obs、时间及APP/build/车辆/任务/账户范围，不跨可能下发的动作缓存。业务`--env`仅作调度声明，不能代替现场产品事实。K-QK-USR-002的故障条件不意味着已有健康/故障读取器；合成216不代表真机业务验收，历史raw run/verdict不改写。

## 执行与证据纪律

动作发送、声明后置、产品结论、资产成熟度和正式覆盖分别记录。离线CI/Replay属于E1，不能冒充当前设备验收。产品确认、人审、重放资格及覆盖口径完整门槛见[工作流](../vpd_skill/references/workflows.md)与[协作约定](ISSUE_COLLABORATION.md)。

`act swipe`仍为容器滚动，不静默接受未实现的定点/时长参数；完整手势沿#58。设备`uitest uiInput swipe`末参数是速度px/s，不是毫秒；#52保留原始命令与观察后再校正经验。raw HDC/uitest不作为绕过Dispatcher的默认自动化路径；截图变化不独立证明视角身份或遍历完整。

折叠态可以继续取证，直板形态另行补测。新现场关键场景进入对应Issue；已知问题先复用现有Issue，避免重复建账。每个修复都以可核验行为与用例交付推进，不以代码行数衡量完成。
