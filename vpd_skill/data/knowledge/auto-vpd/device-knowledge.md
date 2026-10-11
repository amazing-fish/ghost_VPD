# 真机校准确认报告：本 PR 乾崑假设 vs auto-vpd 实测（C1-C13）

> 方法：逐条对照本 PR 的 unverified/hypothesis 条目（targets/states/flows/knowledge），
> 给出 auto-vpd 真机证据与结论：**确认 / 修正 / 证伪 / 新增**。全部证据 run 在共享测试机
> （阔折叠）内部 debug 版（phone_fc78649）上取得；本 PR 的 repo_code 来源条目基于公网
> release 版模型——**build 差异本身是需要裁决的信息**。

## C1 修正｜VPD 入口（K-QK-001 / qk_ads target）
现行 debug 版「ADS」是**底部一级 tab**（折叠外屏 y≈1743），点击直达 VPD，~13s 就绪；
非「爱车页 ADS 按钮」。→ 已入库 K-AV-001（与 K-QK-001 构成冲突，待 resolve）。

## C2 部分确认｜开始按钮可用规则（K-QK-002）
就绪观测成立（「开始」出现=可激活态）；opacity=1.0 判定规则未验证（禁止激活铁律下
永不点开始）。保持 unconfirmed。

## C3 确认｜故障/不可用文案（K-QK-003）
7885 实测命中：「泊车代驾辅助暂不可用」「车辆位置据您较远…异常退出风险」「胎压异常，
请前往检修」。验收口径=计入 warnings 不判 FAIL（K-AV-008）。

## C4 永不验证｜激活顺序（K-QK-005）
禁止激活安全边界内不执行；保留为未来 supervised 用例判据。

## C5 确认并增强｜算路唤醒车辆（K-QK-006）
实锤：点上车点卡片→「路线规划中」(1.0s)→「系统功能启动中2%」→就绪 37.8s。
→ K-AV-006；「系统功能启动中N%」可作 powering-on 状态签名（states.json 现缺此状态）。

## C6 确认+重大新增｜测试车（K-QK-USR-001）
7885=LM8F7E890PA007885 车主车全链路 PASS；**0133/384 为授权车：设为默认后进 VPD
必 60s 超时（owner-only 控车权限）** → K-AV-005。影响 switch_vehicle 与多车用例设计：
VPD 探索只可对车主车。

## C7 部分确认+修正｜收藏/重命名/标签/按名搜索（K-QK-USR-003 + 9 个 hypothesis targets）
- 搜索定位车位 ✓（确定性入口=搜索栏；行点击→面板、行内「路线」→直接算路，K-AV-012）
- 名称搜索算路 ✓（auto-vpd case3 PASS）
- **收藏按钮 hit-test 被地图 XComponent 吞**（dual/direct 跨形态复现，uiRecord 原始
  pointer 事件流实锤）= 产品缺陷 → K-AV-009，favorite_random_spot 流程会 BLOCKED 在
  qk_spot_fav 步（DTS 证据链已备）
- 常去地点「更多」仅展开 10 项，**无 管理/编辑/重命名 入口** → K-AV-010（重命名入口
  缺失疑为级联缺陷）
- 「楼梯口」等为**固定 chip 选项** → K-AV-011（PR 假设确认）
- 标签 chip 命中正常但无状态变化（疑似需先收藏才生效）

## C8 证伪+替换｜login_password 流程（flows.json，repo 来源 12 步）
真机结构性不同：**密码保险箱劫持手动输入**（唯一稳定自动化路径=保险箱选择器填充，
K-AV-002）；填充后安全键盘弹出必须先 hideButton 收起（K-AV-003）；首启隐私协议→
系统通知权限弹框（K-AV-004）。PR 的 12 步流程在真机会失败；auto-vpd vault.* 链路
（ui-catalog.json）为校准后替代，dual+折叠外屏 E2E PASS。
「不写坐标收键盘」的取舍与实测一致（键盘节点 dump 可见）。

## C9 确认+增强｜车辆列表（states.qk_vehicle_list，pending）
实测「问界 F3等 16 辆车」；切车确认后**列表重排序（默认车跳首行）但滚动位置停留**
→ K-AV-022；屏底行「设为默认显示」常需上滑重找 → K-AV-023。PR 的 read-and-record
方案可行，但需加滚动复位校验。

## C10 修正分级｜qk_retry 副作用（targets → route_planning）
「重试」点击会**重新触发上电**（改变车辆电源状态）→ K-AV-007，建议升级为
vehicle_motion 级。

## C11 确认｜policy 禁 app_update
auto-vpd 实锤事故：AppGallery 静默升级公网版致 VPD 入口整体消失；恢复=卸载重装
内部 debug hap（K-AV-020）。佐证本 PR 拒绝原工程「点更新」行为（repo-reuse #8）。

## C12 方向确认｜layout vp 宽度分档
三几何实测：dual 2584×1828 / 半展开 1828×2584 / **折叠外屏 1264×1848**（PR 未覆盖
形态）→ K-AV-024。绝对坐标锚点跨形态失效事故（折叠外屏上 dual/direct 校准锚点全错）
正是「无坐标+vp 分档」要防的故障类；三组数据可校准 layout.json 阈值。auto-vpd 已用
比例（frac）锚点过渡。

## C13 确认｜qk_route_result + monitor 上限
召唤链路实测：算路→系统功能启动中→「开始」出现=就绪（37.8s，K-AV-026）；PR 的
180s monitor 上限余量充足。

---

## auto-vpd 领先资产（本 PR 缺失，建议吸收）

1. **vision 桥**：视觉模型读 XComponent 地图截图（车位卡片/星标态取证）——PR 只有
   opencv 模板匹配且无 OCR；可作为 locate 新策略类型接入。
2. **uiRecord hit-test 取证**：dump bounds/clickable ≠ hit-test 真相（K-AV-009 反例：
   坐标命中 dump 目标但事件落 XComponent）。PR 的「可见矩形中心」计算在该场景会给出
   错误信心 → 建议新增 `hittest_verified` 证据类型 / 定位状态（K-AV-015）。
3. **保持上电终态语义**：算路上电用例不 back_out/stop_app，车辆保持上电供人工接管
   （K-AV-027）——PR cleanup 默认 stop_app，需要「终态保持」用例类型。
4. **owner-only 控车权限**（K-AV-005）：PR 完全缺失的领域规则。
5. **三几何 + frac 锚点数据**（K-AV-024）。
6. **环境知识**：资源包必须立即下载（K-AV-019）、重启密码锁熔断（K-AV-021）、
   AAMS 独占连接清理（K-AV-029）。
7. **runs/ 每步全量 dump 快照**（NN_*.json）——真实录制原料，可转 fixtures/worlds
   回放世界（PR 目前用手写 fixtures）。
8. **召唤算路 KPI**：触发 1.0s / 就绪 37.8s（K-AV-026）。

## 结论

本 PR 乾崑切片的架构（三契约/分级 policy/判据分离/RSI）无需改动即可承载以上全部知识；
需要 owner 动作：① `knowledge resolve` 裁决 K-AV-001 vs K-QK-001（build 差异）；
② 评审确认 K-AV-002~030（observation 来源需 --basis，ref 已附 run 证据）；③ 决定
states/targets 的 calibrated_on_device 翻转（本文档 C1-C13 即依据）；④ 考虑吸收
「领先资产」1-3（hittest_verified 证据类型 / vision 策略 / 终态保持用例类型）。
