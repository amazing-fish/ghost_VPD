# auto-vpd 实测踩坑手册（references/troubleshooting.md）

## 当前工程补充：tap 定点结果与匿名协议控件（2026-10-27，#110；离线验证）

`act tap --point 77,2528` 使用的是整数px，执行链并不按density换成vp，也不会静默忽略point。
缺 `--basis` 或同会话先前 `observe` 会明确报参数错；有依据仍须通过policy。
匿名Checkbox默认命中设置变更拒绝，JSON中的 `policy_denied` / `dispatched=false` 不能解释成点击成功，退出码0仅表示命令已处理。
核对 `action_status`、`result.outcome`、`diagnosis.policy`；实际APP目标下发点在 `attempts[].dispatch_coordinates`（`system=px`）。

对于同时显示“欢迎登录”及“本机号码一键登录”、无Dialog且唯一匿名未选Checkbox的已知乾崑登录页，
可在账户操作已获授权后用 `act tap --target qk_protocol_unchecked --authorize account_state`，再 `observe` 读回checked属性。
多复选框、已勾选、其他页面或未知布局会拒发；不增加raw注入旁路，不代表密码登录flow或完整一键登录已验收。
本条依据#110现场节点报告及合成回归；真实点击后置仍须工作机校准，下文历史真机结论保持原取证时间。

全部为 2026-09-27/28 NEXT 三折叠真机（6HQ0226505000341, 2584×1920, 状态栏高 107）
实测结论，按出现频率排序。

## 0. E2E 调通过程中踩的坑（2026-09-28 首个全绿 run：case1_20260928_184150）
- **隐私协议要点的是'同意'按钮**，不是弹窗标题文本（wait_any 返回的 marker 元素是标题，
  直接点标题无效，弹窗不消失）。
- **用户协议确认框在点'登录'之后才弹**（勾选框点击不一定生效）——必须在登录结果
  轮询循环里检测并点同意（同意 Button y~1509-1619）。
- **Windows 文件名消毒**：dump 文件名带 mask_account 的 `*` 会炸 OSError 22
  （ui_finder._safe_tag 已消毒 `[\\/:*?"<>|]`）。
- **介绍页'跳过' clickable=false**（点击由父容器处理）——消弹层匹配不能要求
  clickable=true；注入中心坐标点击照样有效。
- **定位权限框挡 VPD**（首次进 ADS tab 弹"允许访问你的位置"）：不处理的话 60s 内
  VPD 主页不出现且 App 可能退后台。dismiss_popups 已加分支：仅使用期间允许。
- **锁屏判定不能看'时间悬浮框'文本**——那是状态栏悬浮窗徽标、任何屏幕都在（实测
  在桌面误报"锁屏"，把自动化引去乱滑打开了搜索抽屉）。只认 `ScreenLockRootComponent`
  /`BouncerView_Screen_Lock` 根组件 id。

## 1. 密码登录被"密码保险箱"劫持（最核心坑）
- **现象**：手动 `uitest uiInput inputText` 输入密码后，密码框内容被保险箱自动覆盖，
  或提交后字段被清空重填。
- **根因**：密码框获焦即触发系统 AutoFill；手动输入的凭证提交时必被劫持。
- **唯一可行路径**：走保险箱链路——密码框聚焦 → 面板(使用"xxx"账号 / **使用其他账号**
  / 手动输入) → 使用其他账号 → 人脸框(**无取消键，Back 无效**，选"使用密码") →
  PIN 框(id 含 `pinSix`) → 输锁屏密码 → 账号选择器(标题"推荐填入"xx的账号密码) →
  **点目标账号文本所在行** → 双字段自动填充。
- **填充后必须强校验**：手机号=账号原文、密码框=密码长度个星，不符立即中止。
  （实测 11 位密码被误点成 12 星，若不校验直接提交必错号。）
- 保险箱来源的凭证提交不会被劫持（实测多次）。

## 2. 安全键盘吞点击污染密码框
- **现象**：选择器填充后密码框仍获焦 → 系统安全键盘（canvas 渲染，覆盖屏幕 y>995）
  自动弹出；后续对协议勾选框(y~1287)的点击落在键盘上，误触按键给密码框追加字符。
- **修复**：填充校验通过后**立即 dump 找 `hideButton` 点掉键盘**，再点协议区。
- 密码框无法程序化清空（无剪切、退格无效、PasteButton 拒绝 uitest）——污染只能重来。

## 3. 锁屏与解锁
- **现象**：跑用例途中手机自动锁屏 → dump 只剩状态栏/锁屏时钟；`aa start` 报
  `10106102: device screen is locked, unlock screen failed`（开发者模式禁止自动解锁）。
- **修复（程序化解锁，实测有效）**：
  1. `power-shell wakeup` + `power-shell timeout -o 1800000`（保活 30 分钟）；
  2. 从**通知卡片区**(y≈1390) 向上滑到 y≈500——注意：从空白区上滑可能只是滑动卡片
     不唤键盘；上滑后走人脸识别（失败无妨），失败态下 BouncerView PIN 键盘出现；
  3. 键盘数字是 SymbolGlyph **不落 dump 文本**——按九宫格几何布局点：
     容器 id=`Digital_PSD_Input_Tip`，3 列 4 行，
     `[[1,2,3],[4,5,6],[7,8,9],[·,0,·]]`，格内点击取 (列中心x, 行高2/3处y)；
  4. 输完自动校验解锁；连错 5 次锁机——PIN 与锁屏/保险箱同源，先在别处验证再自动化。
- 已固化为 `flows/case1.unlock_device()`，precheck 自动调用。

## 4. 空的 -b 过滤 dump ≠ 元素不存在
- `uitest dumpLayout -b <bundle>` 在系统弹层（保险箱/键盘/锁屏）覆盖时可能返回空——
  此时状态是 **UNKNOWN** 不是"目标元素不存在"。一律用全量 dump（`Ui.dump` 默认）。

## 5. 双窗口层叠 dump 混淆
- dump 可能同时含多个窗口层（如 社区页 + 全屏登录页 共存）。判断前台页面要看
  **动作可达性/弹窗**，不能只看"元素存在"。验证导航要看目标页的独有标识。

## 6. 同名文本多匹配
- VPD 地图上有 "ADS 10m" 文本与底栏 "ADS" tab 同名——**底栏 tab 选择器必须加
  `center_y_range:[1650,1850]`**。任何 selector 命中多元素时 find 只回第一个（文档序），
  必须用 bounds/y-range 消歧。

## 7. 隐形弹层吃点击（"页面冻住"）
- 点什么都无响应时，先 dump 找屏幕底部弹窗按钮（如 车辆新版本升级框：下次再说/立即下载）。
- 本 skill 的消弹窗白名单：`以后再说/暂不/下次再说/跳过`；白名单外一律不盲点。

## 8. VPD 业务侧事实
- VPD = 乾崑 **ADS tab**（登录后）；首启有介绍视频（跳过，右上角）。
- 召唤跟随**账号默认车辆**；"切换车辆"= 我的→我的车辆→目标车"设为默认显示"→确定弹窗
  （pre_vpd 阶段确定合法；VPD 内确定=激活确认，禁点）。
- 召唤入口=主页"上车点"常去地点卡片（点卡片本体，即"上车点"标签左侧 ~180px）。
- 算路上电证据链：路线规划中 → 系统功能启动中(N%) → 进度文案消失+**开始**按钮出现。
- 路线可能被车辆侧清除（路线已清除，车距远时）——重试点击上车点卡片（实测第二次成功），
  flow 内建 3 次重试。
- 车辆侧警告（胎压异常/泊车代驾辅助暂不可用/车辆位置据您较远）**非致命**，记 warnings。
- 爱车 tab 的车（F03）不可用户切换，与 VPD 无关。

## 9. 设备/环境杂项
- `aa force-stop` 后 `aa start` 幂等恢复比 Back 键导航可靠（Back 连按会退出应用）。
- `bm clean -n <bundle> -d` 清数据=未登录首启态，保险箱条目是系统级的**不受影响**；
  这是可重复"未登录"前置的唯一可靠手段（App 内无退出登录入口）。
- 一键登录页的手机号是 SIM 卡号(193****6576)≠测试账号，验证码路径不可行。
- 测试机为共享设备：保险箱含同事账号（3 个，手机号已脱敏），
  **不登录不删除**，选择器必须精确匹配自己的账号文本。
- 冷启动 ~12-14s、弹层切换 ~3-5s、召唤算路+上电到就绪 ~90-150s（240s 兜底超时）。
- `inputText` 是**追加**不是替换；checkbox 勾选状态不可从 dump 读取——点完勾选直接
  提交，若弹"用户协议"确认框点同意即可。

## 10. 阔折叠双形态自适应（2026-09-28 用户裁定：两形态都要测）
- 形态1 **dual 两页** 2584×1920 / 形态2 **direct 直屏拉伸** 1828×2584；`detect_form`
  按 dump 最大右边界判（阈值 2000），`sel()` 解析 `forms` 变体。
- 页面类 UI 顶部对齐（两形态同 y 只横向拉伸）→ 文本锚点通用；**居中弹框**（隐私/协议/
  人脸框/底栏 tab）纵向随屏高变 → 带 y-range 的弹框锚点必须配 forms 变体（实测 direct
  值已入 catalog：隐私同意 y2250-2450、人脸使用密码 y1650-1800、底栏 y2300-2560）。
- **direct 形态解锁**：滑动手势可能集体失效（7 连败）——Power keyEvent 灭屏 →
  `power-shell wakeup` → swipe(914,2300→914,400, v600) 即活；解锁后可能弹
  "USB 连接方式"对话框（确定 y~1566 direct），需消。
- App 闲置约 2h 曾发生自动登出（原因未明）——case2 状态路由已含重登录兜底。

## 11. 用例2：VPD 地图/车位域事实（2026-09-28/29 direct 实测）
- **地图是 XComponent**：车位号/图标/收藏星标态**不落 dump**——读图用 vision 桥
  （`scripts/vision.py`，DeepSeek-V4.1-Flash，OpenAI 协议；>4MB 自动 PIL 压缩）。
  **vision 坐标误差 ~40%，只可读态/留证，不可用于点击定位**。
- **搜索是确定性入口**（远优于地图车位号点击）：搜索栏"搜索车位号设为目的地" →
  输入车位号 → 实时多楼层结果（如 498 → B2-498/B2-3498/B1-498×3/B1-2498）；
  **结果行点击=开车位详情面板；行内"路线"按钮=直接算路**。搜索页另有快捷 chip
  我的位置/电梯口/楼梯口（与面板 chip 同名，靠页面上下文区分，无共存）。
- 车位详情面板（bottom sheet，固定高度不可上滑展开）：标题"B1层-498 车位"、添加标签
  （电梯口/楼梯口/充电桩）、停车位姿（靠左/居中/靠右）、收藏、查看路线。
- **常去地点行点击=直接算路**（不开详情面板）；行上横滑也被当成点击；"更多"=展开
  列表（10 项服务端数据），**无 管理/编辑/重命名 入口**。
- 地图复位按钮随状态改名：**回自车 ↔ 全览**（找到哪个点哪个）。
- 路由页底部主按钮随车辆状态在 **开始 ↔ 重试** 间切换——**两态都禁点**（重试也会
  重触发上电）；顶部目的地卡片（我的爱车→目的地）；泊入车位/靠边停车/步行路线 tabs。
- `aa force-stop` 重启后：落地社区 tab；定位权限可能重弹（仅使用期间允许为会话级）；
  "请下载车辆资源包"底部面板会挡底栏（下次再说消，已在白名单）。

## 12. 收藏/标签/重命名链路被产品缺陷阻断（DTS 候选，2026-09-28 实锤）
- **收藏按钮点击被地图 XComponent hit-test 吞掉**：按钮渲染正常（vision 确认非置灰），
  dump 声明 clickable=true，但实际点击落在 xComponent10000（uiRecord 实锤，多次坐标
  77/105/140/200×2250-2360 全被吞）；查看路线（同行右侧）命中正常 Text。
- 标签 chips（电梯口/楼梯口/充电桩）hit-test 命中 Text 正常，但点击后无选中态/无
  Toast（疑似需先收藏生效，与收藏缺陷同根因）。
- 重命名入口：常去列表展开无管理/编辑/重命名；面板无重命名 affordance。
- 取证链已固化于 `flows/case2.py hit_test_click`（uiRecord 录制→点击→读回→存证→
  kill -9）。**缺陷修复后 case2 自动转 PASS 判定**（已收藏文本断言）。

## 13. uitest uiRecord = hit-test X 射线（使用规范）
- `uitest uiRecord record -c true` 后台起录（记录注入事件实际命中的控件：W1_Type/
  W1_ID/W1_Text）；`uitest uiRecord read` 读 JSON 行；**录制中 dumpLayout 会超时**
  （broadcast 被占）——用完必须 `kill -9 <pid>`（普通 kill 实测 Permission denied）。
- **安全铁律：探测点击会真实送达不可见僵尸层**——实测 (914,2273) 命中路由残层的
  "开始"按钮（幸而胎压异常使泊车代驾辅助暂不可用，未被激活）。**严禁网格盲扫**；
  一律 dump 定位 → guard 拦截 → 点击；僵尸层内 开始/重试/确定 区域绝不可探测。
- dump 声明的 bounds/clickable **不是** hit-test 真相（收藏按钮即反例）——判定控件
  可交互性以 uiRecord 为准。

## 14. vision 桥杂项
- 模型是 reasoning 型：响应在 `reasoning_content`（vision.py 已兼容 content or
  reasoning_content）；输出很长且含推理过程——**prompt 要求结构化短答**，长输出
  Out-File 落盘再读，勿走控制台（截断）。
- 图片 >4MB 报错：vision.py 自动 PIL 压缩（quality 85→45 ladder，长边 ≤2048）。

## 15. uiRecord 双通道与捕获间歇性失败（2026-09-28/29 深夜实测）
- **两个事件通道**：(A) 控制台 stdout 日志（`-c true`，`finger1:click: at Widget(...)` 行，~1s 出现）
  ——主通道；(B) `uitest uiRecord read`（富 JSON：X_POSI/Y_POSI/W1_*）——**不可靠**：
  提交延迟抖动 1s~60s+，且全局事件存储跨会话残留（会返回前几个会话的陈旧事件，kill -9 也不清）。
  读回数据必须与注入中心坐标匹配校验，否则弃用。
- **record.csv 永远是 0 字节**：只有优雅退出（ctrl+c/SIGINT）才 flush，但实测 `kill -INT` 也
  Permission denied（唯一可用信号是 -9）→ csv 通道废弃，取证靠控制台日志+read 双通道。
- **捕获间歇性失败（未完全归因，已固化工作协议）**：同脚本同状态，22:53/23:52 两会话捕获成功，
  23:12 之后大量会话只有 "No operation detected"（订阅成功但收不到事件）。观察到
  `Can not connect to AAMS, RET_ERR_CONNECTION_EXIST`（疑似 kill -9 残留的 accessibility 悬挂
  连接阻塞新订阅，但与时间窗不严格相关，重启设备亦未获复测机会）。**缓解**：hit_test_click
  已双通道降级——捕获不到时 hit=None + 状态验证照常出 BLOCKED 证据（缺陷结论不依赖单次捕获）；
  需要铁证时先跑一次安全点（回自车）验证通道健康，通道死则不浪费目标取证会话。
- 录制器存活期间 dumpLayout 会被阻塞——取证后必须 kill -9（ps -ef | grep uiRecord 确认清零）。
- **点击注入与录制器解耦**：`uitest uiInput click` 在锁屏安全层键盘上照样送达（实测
  "密码错误，请重新输入" 出现=输入已注册），屏幕内容无变化≠点击无效——安全层不进 screenCap。

## 16. 重启密码锁（reboot lock）（2026-09-29 凌晨实测 + 上午用户裁定修正）
- **⚠ 结论修正（2026-09-29 09:14 用户实测裁定）：重启锁密码就是锁屏 PIN（值不入库，见本机 config）**。
  凌晨"密码不同源、PIN 无效"的结论**错误**——自动尝试失败的根因是 **dual 横屏形态下
  bouncer 键盘键位推断错误**（用户原话："大概率触发横屏键盘后，你没找对位置"）：横屏键盘
  疑似网格转置（行列互换），"unlock 重试脚本 typed 234567"的错位症状与此吻合。
  **待办**：下次遇到横屏 bouncer 先 dump 留证（numKeyBoard 节点几何），验证转置假设后再修
  `_keypad_positions`（增加 orientation 检测：root width>height 时按转置网格映射）。
  在此之前 case1.unlock_device 的熔断保留（横屏映射不可靠时自动尝试仍会输错密码耗尝试次数）。
- **密码键盘在安全层**：screenCap 截图为黑（仅状态栏），vision 永远读不到键盘；但 dump 树里
  BouncerView + numKeyBoard 节点齐全——键位按节点几何推算（`_keypad_positions`：行列聚类，
  节点偶发丢失也能补全，实测 9/10 节点场景完美还原）。
- 黑底截图 ≠ 灭屏：screenCap 渲染窗口树但排除安全层；判断真实内容要结合 dump 树，勿只信截图。
- `power-shell wakeup`（WakeupDevice）+ `timeout -o <ms>`：控制息屏超时；precheck 每轮重设
  30min，但两轮之间的手工实验仍可能撞上灭屏——先 wakeup 再操作。
- 上滑手势会收起已弹出的 bouncer（黑屏态）→ 重新上滑唤出；碎片输入 ~30s 自动清空。
- **bouncer 自动折叠**（00:45 补充实测）：输错后的"密码错误，请重新输入"提示 ~20min 超时后
  bouncer 自动折叠回普通滑动锁屏——但锁屏头部 sl_lock_head 区域仍有 **"重启后需要输入密码"
  文本标记**（LockIcon_Text_lock 节点）：这是**不开 bouncer 即可识别重启锁状态**的 dump 标记
  （numKeyBoard=0 ≠ 已解锁！必须同时确认无 ScreenLockRootComponent）。
- 设备 shell 工具链极简：`tr`/`awk` 不存在（inaccessible），用 `cut`/`sed`；`$!` 在脚本文件内
  正常、内联命令里会取错 pid；pid 提取 `ps -ef | grep xxx | grep -v grep` 后 `cut -d' ' -f2`
  会因多空格取空——稳妥用 `awk` 不可用时在脚本文件内 `$!`。
- **pwsh 引号铁律**：`$(...)` 在 pwsh 双引号字符串里被本地 PowerShell 求值（'grep' is not
  recognized 报错来自本机！）——复杂设备端逻辑一律写脚本文件推到 /data/local/tmp 执行，
  勿内联。

## 17. 乾崑 App 启动隐私锁（设备重启后首次启动触发）（2026-09-29 上午实测）

**现象**：app.restart 后状态路由报"既非 VPD/tab 也非登录页"——实际是人脸验证屏
（'未识别成功'/'点击人脸重试或使用其他方式验证' + '使用密码'/'使用指纹'，**无取消按钮**）。
该锁在设备重启后的首次 app 启动出现（09:15/09:19 两轮复现），持续 ≥40s 不会自行消失。

**确定性消解链路（已实测，零密码风险）**：`使用密码 →（IMF 安全键盘密码框）→ 取消`
→ 整个验证被跳过、app 正常进入 tab 页。已机械化：`case1.app_lock_present()` +
`case1.dismiss_app_lock()`，挂在 app.restart 路由循环里。

**密码安全键盘知识**（若必须走输密码路径）：
- 密码框文本"请输入锁屏密码"——用的就是锁屏 PIN（用户裁定与锁屏 PIN 同值，值不入库）；
- 键盘是 IMF 安全层 `CanvasKeyboard` (0,1088,2584,1828)：无键位子节点、
  screenCap 截图该区域 **100% 纯黑**（vision 原生 dominant_colors 权威裁定，
  6MB jpeg 体积是噪点不是内容）；
- 但**注入点击照样有效**，且 `pinSix`（密码圆点框）的 text='*'*n 反映已输位数
  ——安全键盘的**位数反馈通道**（dump 可观测）；
- canvas 键盘为**全宽布局**（远左 x=431 点击也注册按键，排除居中窄排假设）；
  3×4 网格推算键位：列 431/1292/2153，行 1180/1365/1550/1735——**数字指派未验证**
  （对齐 §16 教训：几何推算键位在未验证前不可用于真实输密码）。

**关联 §16**：系统 bouncer 键盘 numKeyBoard 节点几何（3x4 网格、节点中心）实测
输出了错误序列（用户密码正确却被输错）——节点中心 ≠ 键心或指派非标准，
根因未定；下次遇到先 dump 留证。pinSix 位数反馈通道同样适用系统 bouncer
（可做零风险按键注册探针，但数字身份仍不可观测）。

## 18. AppGallery 静默自动升级公网版事故 + 卸载重装内部版恢复链（2026-09-29 上午实锤）

**事故**：09:52 AppGallery 把测试机乾崑 App 静默升级为公网正式版（bm dump:
appProvisionType 从 debug→release、appDistributionType=app_gallery、updateTime=09:52）。
公网版整个导航改版：tab 栏 商城/探索/展厅/ADS/社区（我的/爱车消失），ADS tab 变成
高德充电地图（定位权限未开时默认北京），VPD/泊车代驾入口完全不存在（4 tab+车卡+
头像+图层按钮+ability 清单穷尽验证：仅 Entry/Vroom_sdk/CardForm/EntryBackup）。
探索页车卡点开是"被授权的车辆"（公网版只显当前默认车）。**VPD 只在内部 debug 版存在。**

**恢复链**（全部实测）：
1. `hdc install phone_fc78649.hap`（Desktop\outputs 最新内部版）报 "install version
   downgrade"——公网版 versionCode 比内部版新，必须先 `hdc uninstall` 再 install。
2. 卸载重装副作用（app 数据+系统级授权记忆全清）：首启隐私协议 → 同意后**系统通知
   权限框**（允许"华为乾崑"向你发送通知？）会盖住登录页 30s+ → dismiss_popups 新增
   通知分支 + cold_start 改"边等边消"；定位权限/白名单/资源包等首启弹窗全回归。
3. 密码保险箱是系统级的，卸载不丢（vault PIN 链路照常）。
4. **车辆资源包丢失**：重装后进 VPD 弹"请下载车辆资源包"；点"下次再说"拒绝后进
   VPD 页 → **页面加载完成但 app 必死退桌面**（冷启/热启均复现；不拒绝则活着）。
   修复=点"立即下载"补装（静默秒装），之后 VPD 页 40s+ 稳定。诊断特征：dump 从满节点
   骤减到只剩时钟文本=app 已死退 launcher。
5. 服务端登录态/默认车绑定不丢：重装后 relogin 目标车 VIN7885 仍是默认显示。

**预防**：测试机应关 AppGallery 自动更新（或装内部版后断开升级源），否则夜间/日间
随时可能被再次覆盖。日检 precheck 可加 appProvisionType/debug 断言。

## 19. uiRecord AAMS 连接失败根因：残留 uitest daemon 独占（2026-09-29 实锤）

**现象**：`uitest uiRecord record -c true` 报 `Can not connect to AAMS,
RET_ERR_CONNECTION_EXIST`，规律复现非间歇（与 §15 的提交延迟是两回事）。
**根因**：残留的 `uitest start-daemon`（dumpLayout/screenCap 常驻服务，含
screen_cast 扩展）独占 AAMS 连接。**互斥关系双向**：daemon 占用→record 连不上；
recorder 运行→dumpLayout 被阻塞（§15 已知）。
**解法**：按 pid `kill -9` 全部 start-daemon（pkill 会 Permission denied，只能
kill），record 立即可起；被杀的 screen_cast daemon 下次 screenCap 自动重生。
已固化进 case2._start_recorder（失败先清 daemon 重试一次再报错）。
**证据**：杀 daemon 前连试 3 次全失败；杀后秒起（ForAbility=qiankun EntryAbility）。

## 20. 阔折叠三种屏幕几何 + 比例锚点体系（2026-09-29 折叠外屏实测确立）

**三几何**（detect_form 按根节点宽度阈值只分 dual/direct 两类）：
- dual 两页展开横屏：2584x1828
- direct 半展开竖屏（历史标定用的"直屏拉伸"）：1828x2584
- direct 完全折叠外屏（2026-09-29 新发现）：**1264x1848**

**事故**：direct 锚点全部按 1828x2584 标定（tab y[2300,2560]、滑动区 x≤1550），
用户折叠手机跑 direct 回归 → app.restart 状态路由 8 轮不识别（dump 里明明有
ADS tab，在 y≈1757）→ "热启动后状态无法识别" FAIL。根因：折叠外屏 y1757 落不进
[2300,2560]，滑动区 x>1264 半程出屏。

**修复（比例锚点体系）**：ui_finder 新增 `center_y_frac_range:[f1,f2]`（按当次
dump 根节点屏高换算绝对区间）；catalog 五个形态变体锚点（tabs×3/隐私同意/
保险箱使用密码/协议同意）全部改比例版，一套通吃三几何（离线单测 3 几何×5 锚点
+ 反例全过：C:\Temp\test_frac_anchors.py）。MAP_SWIPE_REGION 改分数制
（dual x(0.27,0.85) y(0.16,0.80)；direct 类 x(0.14,0.85) y(0.16,0.48)，由
swipe_region_px 按实际屏尺寸换算）。折叠外屏 direct 回归套件 E2E 验证通过
（case2 BLOCKED 有效 + case3 PASS，ADS tab@350,1743 命中、滑动 4 次正常）。

**注意**：页面类顶部对齐锚点（登录输入框 y500-900 等）在三几何中 dual/折叠外屏
屏高近似（1828/1848）沿用有效；**半展开 2584 高下页面类锚点未回归验证**——
半展开形态跑登录链前先 dump 复核。锁屏 PIN 键盘 _keypad_positions 为几何自适应
（节点聚类），折叠外屏竖屏标准布局已实证（2026-09-29 11:42 解锁成功）；
unlock_device 上滑坐标也已改形态感知（从锁屏 dump 根节点取宽高中轴起滑）。

**楼梯标签折叠外屏行为差异**：dual 形态下楼梯口标签点击后选中（vision=是）；
折叠外屏同操作命中正常（Text 楼梯口）但无状态变化（vision=否）——疑似与收藏
按钮同根因（收藏失败→标签链路不生效），缺陷修复后两形态都要回归。

## 21. 控车权限：非车主无法使用 VPD（2026-09-29 用户实锤）

鸿蒙智行车辆在乾崑 App 里**只有车主能控车，授权用户无法使用 VPD**。测试账号
测试账号（151****3183）名下仅车架号尾位 7885 是车主车；0133/384 等列表内其他
VIN 均为授权车。实测：把 0133 切为默认显示后进 VPD，ADS tab 点击正常但 VPD 主页
60s 超时打不开（runs/case4_car_route_poweron_20260929_122541）——不是 app 缺陷，
是权限约束。

**测试设计约束**：多车探索一律用 7885；其他 VIN 不可作为 VPD 探索对象。
car.select 原子保留（切车链路本身已验证可用），但组合用例的目标车必须车主车。
