# 原子能力注册表（自动生成，勿手改；来源 scripts/atoms.py）

## `device.precheck`  [阻断]

预检：hdc 在线、亮屏唤醒、息屏超时延长、锁屏则解锁（PIN）、阔折叠形态探测
（dual 两页 2584x1920 / direct 直屏 1828x2584，写入 ctx.form）。
expect_form 指定时校验实测形态：不符→BLOCKED（如 direct 回归前手机未折叠）。

- params: device_pin, expect_form

## `app.restart`  [阻断]

热启动（不 bm clean，保留登录/数据）：app_stop → app_start → 弹窗消解 →
App 启动隐私锁消解（设备重启后首启触发：使用密码→取消跳过，2026-09-29 实测）→
状态路由（vpd_ready / on_tabs / logged_out，写入 ctx.state）。

## `app.relogin_if_needed`  [阻断]

重登录兜底（仅 ctx.state=logged_out 时执行）：保险箱链路密码登录（复用
case1 全链路）→ 切默认车（VIN contains 匹配）→ 回 tab 页。已登录则跳过。
无论是否执行，凭据都存 ctx.login_creds 供 vpd.enter 自愈重登录使用
（隐私锁跳过/重启丢会话时，登录页可能晚至点 ADS tab 才浮现）。

## `car.select`  [阻断]

切换默认显示车辆：我的tab → 我的车辆 → VIN 唯一匹配行（滑动翻页找车）
→ 设为默认显示 → 确认弹窗（pre_vpd 阶段护栏放行'确定'——切车确认非激活）
→ 校验（目标行首行+默认显示标签）→ key_back 回 tab 页。已是默认则跳过。
复用 case1._select_car 全链路。前置：ctx.state=on_tabs（app.restart 之后）。

- params: vin_contains

## `vpd.enter`  [阻断]

进入 VPD 主页：on_tabs 则点 ADS tab；消弹窗（定位权限/介绍视频跳过/资源包）
轮询至"华为练秋湖研发中心"出现；置 guard in_vpd 阶段（开始/确定/重试 全禁触）。
自愈（2026-09-29）：轮询中发现登录页（隐私锁取消跳过/重启丢会话导致，登录态
晚至点 ADS tab 才浮现）→ 用 ctx.login_creds 重登录+切默认车 → 再点 ADS tab
继续等 VPD 主页（最多自愈一次，防登录死循环）。

## `summon.route_poweron`  [阻断]

召唤算路上电：点上车点常去地点卡片 → 路线规划中 → 上电进度（系统功能
启动中%）→ 就绪'开始'按钮出现（绝不点击，禁止激活护栏 in_vpd 阶段生效）。
【终态保持上电】返回时车辆已上电、召唤面板停在就绪页——本原子后不要再接
app.back_out/cleanup 类原子，否则破坏"保持上电"终态。复用 case1._summon
（3 次重试/路线被车辆侧清除检测/KPI 计时：规划触发延迟/就绪延迟）。

## `map.pan_random`  [非阻断(BLOCKED 只记录继续)]

随机滑动地图（形态感知安全区，避开楼层选择器/右缘按钮/底部 sheet）；
可选 vision 读图留证（可见楼层+车位号）；随后点 回自车/全览 复位视角。

## `spot.open_panel_via_search`  [阻断]

搜索定位车位并打开车位详情面板（确定性入口，优于地图车位号点击——车位号是
XComponent 渲染不落 dump）：搜索栏 → 输入车位号 → 精确文本行（如 'B1层-498 车位'，
无命中放宽 contains）→ 行点击 → 等待面板（查看路线 出现）。

## `panel.favorite_hittest`  [非阻断(BLOCKED 只记录继续)]

收藏车位（取证式）：uiRecord hit-test 录制实际命中控件 → 点击 → 读回 → 存证 →
杀录制 → dump 验证"已收藏"。已知产品缺陷（2026-09-28 uiRecord 实锤，dual/direct
跨形态复现）：收藏按钮点击被地图 XComponent 吞掉（W1=XComponent），hit-test 正常
命中且出现已收藏才 PASS；否则 BLOCKED（DTS 证据 evidence_favorite_hittest.json）。

## `panel.tag_hittest`  [非阻断(BLOCKED 只记录继续)]

给车位打标签 chip（取证式）：hit-test 点击 tag chip → vision 复核选中态。
已知行为：chip 命中 Text 正常但无状态变化（疑似需先收藏生效，与收藏缺陷同根因）。

## `panel.rename_probe`  [非阻断(BLOCKED 只记录继续)]

重命名车位（依赖收藏成功）：先 Back 关闭车位面板回主页（更多 只在主页可见，
面板会盖住常去地点区）；收藏被阻断时展开常去地点列表取证"无管理/编辑/重命名入口"；
收藏成功则探测重命名入口（当前 build 未实测路径，发现即留 warning）。

## `search.route_via_search`  [阻断]

名称搜索算路（确定性入口）：退回主页 → 搜索车位号 → 目标行内"路线"按钮点击
→ 等待路线规划页（我的爱车目的地卡片 + 路线规划中/启动中/重试/开始 任一）。
注意：底部主按钮 开始↔重试 随车辆状态切换，均禁点（护栏已机械化）。
返回主页逻辑幂等：仅当搜索栏不可见时 Back（最多 2 次）。

## `assert.not_activated`  [阻断]

终态断言（禁止激活）：dump 全树确认无运行态标记（暂停/正在前往召唤点/正在
泊入车位）；护栏审计确认全程无 开始/重试 点击、in_vpd 无 确定 点击。

## `app.back_out`  [非阻断(BLOCKED 只记录继续)]

清理：Back 退出当前页留安全态（注意 Back 连按会退出应用，默认 1 次）。
