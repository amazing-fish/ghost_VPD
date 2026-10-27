# 本机实际 vs 主线 targets/states 差异（2026-10-27 `session-s293`，主线 `dfcb332`）

只写**本轮实际观测到**的失配与其证据出处；没有观测的一律不写。主线定义取自 `data/apps/qiankun_hm/{targets,states}.json`。

## 1. `qk_tag_elevator`：主线**未定义**，工作机本地有定义

- 主线：`grep qk_tag_elevator vpd_skill/data/apps/qiankun_hm/targets.json` = 0 命中；只有存档 `TC-QK-235.json` 与文档引用它，所以 lint 才报「目标未登记」。
- 工作机本地工作树（`C:/temp/ghost_vpd_pr2`，本地 main `68f3d2c`，19 个未推送提交，已保全为 `local-main-19commits.bundle`）**有**该定义，原文见 [local-only-targets.json](local-only-targets.json)：`text=电梯口`，`side_effect=navigation`，`status=unverified`，来源是 10-24 启境1395 `session-s1700` 的 obs-0009/0013。
- 本机本轮**没有**该标签的观测：本园区搜索页的标签行只有 `充电桩`（`obs-0039` 文本 = `反馈 / 输入目的地或车位号 / 充电桩 / 历史记录为空`）。所以「金桥园区是否有『电梯口』标签」在本轮**无法验证**。
- 结论：对主线而言是**缺失定义**；对工作机而言是**未入仓的本地定义 + 无同园区观测**。搬运时不能只搬 `text=电梯口` 这一条策略就宣称覆盖。

同批本地独有、主线也没有的还有 `qk_floor_b1`、`qk_floor_l1`、`qk_support_area`（同样见 `local-only-targets.json`，均 unverified，来源 10-24 启境1395）。其中 `qk_floor_l1`/`qk_floor_b1` 用 `//*[@text="L1"|"B1"]` 全树匹配，在本机 `obs-0015` 的入口页里 `L1`/`B1` **不存在**（该页无楼层控件），因此不能从这里验证。

## 2. `qk_spot_search_input`：主线路径在本机**定位不到**

- 主线定义：`xpath=//root/RelativeContainer/Search/SearchField`（来源 2026-10-13 量产版 `session-wk-qk4` obs-0046，当时 `bounds=[308,164][986,304]`）。
- 本机 `act input --target qk_spot_search_input` → `status=target_unresolved`、`locate.status=not_found`、未含 dispatch 记录（[actions/seq13](actions/seq13-input-qk_spot_search_input-unresolved.json)，`obs-0040`）。
- 本机真实的输入框祖先链（`obs-0046`）：

```
SearchField#__SearchField__search_input [296,158][1037,293]
 < Search#search_input [175,158][1145,293]
 < RelativeContainer#SearchPoiPage [0,132][1320,1788]
 < Stack < __Common__ < Column < RelativeContainer
 < Stack#VPD_HOME_VIEW < Column < TabContent < Swiper < Tabs < NavBarContent < NavBar
 < Navigation < NavDestinationContent < NavDestination < NavigationContent < Navigation
 < root [0,132][1320,1788] < (0,0)[0,0][1320,2848]
```

即 `RelativeContainer` **不是 root 的直接子节点**（root 下还有一层裸节点 + 整条 Navigation 链），该绝对路径在本 build 上必然失配。这条与 #110 的「px 坐标是否消费」无关，是纯 targets 问题。

- 对照：`qk_k89_tag_search_input`（`xpath=//*[@id="SearchPoiPage"]//SearchField[@id="__SearchField__search_input"]`）在本机**能命中**（`SearchPoiPage` 与两个 id 都在树里，见 `obs-0039`/`obs-0046`）。但它登记的适用面是 K89/由度 A207 的标签链，本轮只是**借它做输入**（输入成功是工作机报告，5次输入动作原记录未随本PR提交；seq12为导航tap，不作为输入成功的原记录），不能据此把它升级成通用搜索输入框。
- 建议（不在本 PR 实施）：把搜索输入框改成 id 锚定（`SearchPoiPage` 或 `__SearchField__search_input`），而不是 `root` 起头的绝对路径。

## 3. `qk_search_entry` / `qk_spot_input`：入口页**两者都不命中**

- 入口页文本（`obs-0015`）：`金桥出口加工区 / 泊车代驾 / 共 742 个 / 搜索 / 我的位置 / 开智慧停车缴费 / 收藏及常去 / 沿途找车位 / 停泊位置 L1层-PR10 车位 …`，**没有**「搜索车位号设为起点/目的地」。
- 因此 `qk_vpd_home` 的 `require_any_text`（主线 `b9a80c8` 起）在本页**两条都不满足**，`page.state=unknown`、`page.signature=sig-1c45fe0a47`，`candidates=[]`。
- 可点容器 `Row[54,518][1020,707]`（`clickable=true`，内含 `Text 搜索`）是入口页搜索栏，点它进入搜索页（`text=搜索` 一次 passed，`actions/seq12`，动作终态为 `obs-0038`；后续独立观测 `obs-0039` 为 `page.state=qk_spot_search`）；但它**不是** `qk_search_entry`：声明 `vpd_entry` 会被 `task_semantics_unverified` 拒（`actions/seq08`/`seq09`）。
- 结论：本轮未取得经核验的 VPD 首页到达路径。两次声明 vpd_entry 的点按被拒是已知阻塞之一；现有证据不能证明园区卡可到首页，也不能把未知导航缺口归因于单一未登记语义。

## 4. states：本地与主线只差 `qk_vpd_home` 的识别条件

- 两边 `states` 都是 13 条、id 相同；唯一内容差异是 `qk_vpd_home`：主线用 `require_any_text: ["搜索车位号设为目的地","搜索车位号设为起点"]`（`b9a80c8`），本地工作树用 `require_text` + 兜底策略。
- 状态离线重算：入口页unknown；空查询搜索页obs-0039为qk_spot_search。obs-0046/0051/0056/0061的非空查询结果树均unknown：占位文案消失，现有require_text不再满足；不能称所有搜索结果页都已被状态模型识别。
- targets 内容差异：`qk_search_entry`、`qk_spot_input`、`qk_protocol_unchecked` 三项本地有改动（起点文案兜底 / 描述措辞 / #110 的匿名 Checkbox 策略）。主线 `b9a80c8`+`b8a544a` 之后的版本**已经取代**这些本地改动，**不得**把本地版本并入主线。

## 5. 影响（供后续接入）

1. 搜索域用例若沿用 `qk_spot_search_input`，在本 build 上第一步就 `target_unresolved`；改用 `qk_k89_tag_search_input` 只是借用，需要新的通用定义。
2. `qk_tag_elevator` 若要从存档转成活跃资产，必须先有**本园区实测的标签行 + 点击后结果**（本轮本园区只有「充电桩」一个标签）。
3. 先补首页到达路径及相应原始观测，确认真实页面作用后再决定目标与语义；在此之前只能把首页到达列作未满足前置，不预先登记 vpd_entry。本证据包不作该资产决策。

以上是基于导出AA重建的只读验证；输入目标与状态签名修订另交代码PR，未在本证据包登记生产目标或确认产品规则。
