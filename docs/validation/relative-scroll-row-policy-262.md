# #262：完整被动布局链的滚动语义

## 最新证据：不能用页底通过替代页顶通过

工作机六 PR 集成 tree `79176a7e62a19a8a45ff09ce1a70c549f9c5972f` 精确对齐旧
#264 HEAD `0342d17d2161e664414133123eedb047b6f0a5f6` 后：页顶
`run-20261117-154103-949d` 在 ListItemGroup 命中“收藏”拒绝；页底
`run-20261117-154500-9283` passed。此前 Row 修复不完整，不是部署漂移。
文字反馈提供的层级为 Column/List/ListItemGroup/ListItem/Column/Row/Button/Column。
本地未取得工作机截图、树或日志原件，合成测试不能声称是原帧回放。

## 结构证明，不是无条件类型豁免

只有节点到当前同一树中已验证 Scroll/List/Grid 范围的**完整被动布局链**才能收窄文字来源：

- 节点和范围必须是当前树中的同一对象，链必须实际到达该范围。
- 每个中间节点须属于原生布局 Row/Column/ListItem/ListItemGroup/GridItem/Flex，
  不可点击、不可独立滚动、无 longClickable 或未知的长按属性值。
- 范围外存在可点击/长按祖先也不能收窄；原始 clickable/scrollable 属性异常时不能用归一化默认值证明被动性。
  仅排除 idx=0、无父级、type=orgRoot 且仅含 type/bounds 的元数据包装；带交互属性的根不排除。
  范围是链上唯一允许的滚动节点。
- 链上所有节点自身 text/description 都参与策略，不能漏掉中间祖先的危险语义。
- 叶子、实际按钮接收者、未知/交互类型及任一不确定链仍走完整点击审计；执行器仍逐区间
  检查路径归属、危险类型和未知表面。普通 tap 不传滚动范围，语义不变。

这不是所有原生布局无自定义手势的证明，不改 policy.json 或授权，不采用左边缘坐标绕行，
不修改 S01 候选的 within/start/end/断言。类型名只是结构判断的必要条件，不单独授予放行。

## 工作机验收

1. 继续前补采页顶首败、页底通过、修复后页顶的本地对照截图；原件只留工作机，当前缺图未关闭。
2. 使用本 PR **最新 HEAD**及固定 #255/#258/#259/#260/#261 版本，记录各 SHA 与新的集成 tree。
   旧 tree `79176a7e…` 仅用于反例，不得再当新修复验收基线。
3. 显式复位页顶、确认设置不可见，原样整例运行，记录 S3/S3b/S4/A1/A2/S5。
   另从页底、设置可见状态运行同一候选作对照；保留每个起始状态与首个失败。
4. 仅在 Issue 回报文字摘要、运行 ID、SHA/tree、起始状态与结果；不外发文件、图片或账号资料。
5. 不额外授予 vehicle_control、不点击退出登录。S01 保持候选，单次 passed 不自动晋级或合并。

## 审查修正：缺省不等于显式空值

`longClickable` / `long-clickable` 缺省是可选属性不存在；显式空字符串、空白、未知值
都不能证明没有长按行为，须保留完整审计。仅显式 false/0 可视为禁用。
回放生成器仅将“没有声明长按行为的合成节点”默认设为 false；显式 attrs 覆盖（包括空值）
和真实失败占位树的空值保持不变。此改动不改写真机 dump，也不把空字段强行补成 false。

## 输入契约：空属性聚合根不是未知控件的 false 默认值

官方 OpenHarmony 7.0 的固定源码 `85f4f79861c42e91d7eb8f83b707743f7b968bbf` 说明：

- [命令入口](https://github.com/openharmony/testfwk_arkxtest/blob/85f4f79861c42e91d7eb8f83b707743f7b968bbf/uitest/server/server_main.cpp#L273-L286)：只有 `-i` 启用窗口列表，本仓 `dumpLayout -b ... -p ...` 不含 `-i/-a`。
- [聚合序列化](https://github.com/openharmony/testfwk_arkxtest/blob/85f4f79861c42e91d7eb8f83b707743f7b968bbf/uitest/core/ui_driver.cpp#L125-L190)：此模式主动创建非 widget 文档根；26 属性为空，仅 bounds 为从零开始的窗口并集，children 为匹配 bundle 的窗口树。
- [属性集合](https://github.com/openharmony/testfwk_arkxtest/blob/85f4f79861c42e91d7eb8f83b707743f7b968bbf/uitest/core/ui_model.h#L90-L117) 与 [widget 序列化](https://github.com/openharmony/testfwk_arkxtest/blob/85f4f79861c42e91d7eb8f83b707743f7b968bbf/uitest/core/ui_model.cpp#L155-L168)：真实窗口 widget 额外具有 hierarchy/visible/hashcode/hint，窗口根另补 abilityName/bundleName/pagePath。

适配器根据其自身受控 bundle 命令结果记录**候选 profile 诊断**；不改原 JSON，
不接受 JSON 内自称的来源标记。Observer 记录完整 raw 的语义 SHA256、schema 匹配和拒绝原因。
它们分别证明采集方式、结构与输入完整性，**不证明未知现场二进制的根没有输入所有权**。

现场仅给出 OS 版本，尚未证明厂商 uitest/ArkXTest 二进制与上述 commit 的实现相同。
因此即使结构完全匹配，仍返回 `status=unverified`、`reason=producer_build_unverified`、
`may_exclude_root=false`。本次不改变 policy/UiTree 的权限行为，不排除新形态根，页顶仍应
保持 blocked；不把 clickable/longClickable 空值判成 false，不新加布局类型豁免。

下一步工作机只以 Issue 文字补充（不必重复整例动作）：
1. 设备侧 `uitest` 版本查询结果（不支持也如实记录）、二进制路径/SHA256，以及可核验的
   厂商构建来源/对应实现或“该根不参与输入接收”的明确生产者契约。哈希本身不是语义证明。
2. 实际调用模式是否为 `dumpLayout -b <被测bundle> -p <临时路径>`、无 `-i/-a`；不回传账号信息。
3. 根26键是否精确匹配、其余25值是否严格空；直接窗口数、窗口bundle是否与请求一致，
   widget的hierarchy/visible/hashcode/hint及窗口字段是否齐全。只回报布尔结论/计数/缺失键名。
4. 新观测中的 `obs.tree.document_root` 诊断（status/reason/profile/schema_status，必要时语义指纹）。

收到可验证生产者语义后再独立安全评审，未取得前不启用旁路。原树与截图始终留工作机，
已有页顶首败和页底通过要保留；结构形似与一次通过均不能替代此证明。

## 集成口径更正

旧期望 tree `33738a26...` 误含67项 pytest/pycache 产物，不能作交付基线。
按六个固定PR的 path/blob/mode 重建后，419项源码全部一致、无覆盖或遗漏，干净 tree 为
`a5d605913cc0ed7d633e8b02a8eb8ff1d7569e52`，与工作机一致；干净树502项回归通过。
后续只按精确 manifest 暂存文件，不广泛 `git add -f .`。本修复在 harmony_hdc.py 与 #260
同文件不同区段，须三方合并两份增量并核新 manifest，不能整文件覆盖版本识别修复。
