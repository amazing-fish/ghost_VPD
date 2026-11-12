# 定位经验不随索引漂移（#241）

定位策略索引不是稳定身份。#236 删除裸文本首选后，原索引 1 的 TabBar 变成窄屏区域后备；仓内 XP-20261013-101449-def5 是 candidate、未激活，但旧 trial 路径会错误解释该索引。

新定位结果和新候选保存整个目标定义的 locator_fingerprint（包括策略顺序、范围和 when 条件）。运行仅在当前目标完全匹配且索引合法时应用；缺绑定或定义变化的已存经验保留，但不应用，其原因写入 overlay.locator_ignored。validate/adopt 均核当前定义；验证还要求试验回执含相同指纹及实际应用的 overlay_pref，避免其他变化被误计收益。

不自动给旧经验补签指纹、不重写历史、不删除候选、不把旧验证搬到新目标。需要使用偏好时，从当前定位重新产生候选并验证。完整目标指纹采用保守策略：即使仅来源说明变化也会要求重新验证，以免推断旧索引含义。本次不改资产、定位定义和判据，不声称新增真机通过。

验证：在 vpd_skill 目录执行 `python -m pytest tests/test_locator_experience_binding.py tests/test_experience.py tests/test_layout.py tests/test_experience_validation_interruption.py -q`。


## 内联目标的当前来源（#243）

#242 的指纹门禁原来只查 targets.json，导致真实 Runner 新生成的 inline:<label> 候选也无法验证。现在从来源用例的当前文件读取内联目标，复用既有步骤/flow/recipe/cleanup 展开器，而不是信候选自报的定义或路径。候选对应的来源必须仍存在、属于当前 APP 且指纹一致；参数化后无法与静态定义核对的目标保持不采纳，不保存/信任候选声称的解析值。

验证仍允许包含不触碰该目标、或同 label 不同定义的负对照用例：运行时不应用不匹配偏好，也不计入该经验的独立覆盖与收益。采纳前重新核对最新成功验证的实际 exercised_cases，要求包含来源之外的独立用例，且这些用例的当前定义仍匹配；缺失、删除或变更都要求重新验证。原始经验和验证历史不被重写。

真实 Runner 合成回放已覆盖直接 inline 生成→跨用例验证→采纳→运行；嵌套来源测试只覆盖静态解析，不代表实现了所有嵌套 flow/recipe 的经验生成/收益统计。没有新增实机通过声明。
