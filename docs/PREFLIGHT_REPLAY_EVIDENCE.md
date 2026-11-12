# 前置阻塞与重放证据（#235）

资产晋级需要同一内容/依赖版本的至少两次独立、确定且一致的产品重放。环境准备不满足而未进入用例的尝试，不是新的产品反证。

Runner 在前置检查拒绝时写入 `preflight_evidence`。仅在显式证明没有进入用例、全部 dispatcher 事件计数为零、没有产品步骤观测，并且完整运行记录的动作、清理、安全停止、步骤尝试及恢复均为空时，摘要保存该证据。前置诊断截图仍保留，不当作产品断言覆盖。

这样的 `blocked` 记录继续保存在历史中，不计通过次数，也不重置已有合格重放；断言级覆盖采用同一规则。`ignored.reason=preflight_only_no_product_evidence` 解释排除原因。

以下仍沿用原规则：真实执行后的 blocked/pending、执行 error、产品失败、不明确或有动作的前置尝试、缺证据的历史摘要。产品失败仍参与一致性判断，不能被前置记录掩盖。历史摘要不自动升级；重复录入同一历史运行保持原字节和原判断。

验证：`python -m pytest tests/test_preflight_replay_no_reset.py tests/test_replay_chronology.py tests/test_replay_evidence_consistency.py tests/test_condition_lifecycle.py tests/test_condition_runner.py -q`（在 vpd_skill 目录执行）。这些为独立合成环境与真实 Runner/CLI 测试，不声称实机通过。
