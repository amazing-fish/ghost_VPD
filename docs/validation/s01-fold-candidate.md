# S01 折叠形态候选（未验证）

仅新增 draft；不修改 S01/HM002，不继承 review、replays、product_status 或 observed。不得据此晋级。

## 归因边界
layout_requirements 缺省/null 不排除宽屏；observed.layout_key 是来源描述，不是 locate.resolve 的执行门禁。实际策略门禁是 strategy.when。旧 S3 未限定容器与现场未滚动有关，但缺原始派发/容器摘要，不能断言执行器缺陷。旧 S4 未找到设置不能归因 sm/portrait 标记。

## 候选与代价
复用独立已确认 K-MOT-001/002 和注册 tab_my。只针对实际 device_form=foldable、motor 3.0.3.200、已登录；像素宽度不自动等于 wide。显式 List 内上滑两次，随后唯一可见“设置”定位，断言退出登录入口但不点击，再 back。固定两次是已报道手工路径的待验提案，不保证其他折叠界面通用。零重试避免失败时累积滑动；复用已登记 settings_entry，不新造 inline observed。若显式两次滑动后未定位，该目标既有 scroll(max=2) 仍可能触发无 within 的补滚；本候选未改变该既有行为，现场需确认 S4 直接命中或记录补滚状态，不将推定视为通过。

## 工作机只需在 Issue 补的最小摘要
1. 执行前真实 layout.device_form/width_class/wide，不能用分辨率推定。
2. my_tab 当前可见 List 个数；两次滑动的 sent/状态、选中容器类型和是否发生滚动（布尔），无需原树/图片/日志。
3. 滚后可见“设置”是否唯一且无遮挡；S4/S5 状态与 A1/A2 结果。
4. 原件只留工作机；由该机核完整证据、独立审核及录入，不以 Issue 文字冒充本机原件。

未取得新实机证据前保持 draft。这里不涉及登录退出、购车、车辆动作或最终激活。


## 待完成门禁
静态 lint 可通过，仍是未审核 draft：必须由工作机保留原件并实际执行候选，再独立审查与记录真实重放。不得直接继承旧批准或重放。observed 不作布局门禁，但参与 locator_fingerprint，不能把删除来源说成零语义变化。

## 相对手势与离线集成
候选使用 start=[0.5,0.75]/end=[0.5,0.25]/speed=2000/within=List 的既有相对手势，而不是 direction swipe。前者要求唯一容器，多 List 或缺 List 均不发送；direction 路径会选择最大滚动容器，不具有该拒歧义保证。合成世界显式模拟两次滚动才出现设置，非工作机原件；覆盖整例 A1/A2、旧用例对照、缺 List/多 List/无设置反例。

## 工作机验收版本
必须使用发布后的相关 PR 最新完整 HEAD（是否合入均如此），在 Issue 记录 PR URL 和完整 SHA；本候选尚未发布，禁止使用 main 冒称已验本候选。
