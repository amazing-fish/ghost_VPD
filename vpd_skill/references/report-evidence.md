# 报告中的步骤与证据归属

`report.md` 是 `run.json` 的只读展示，不重新计算产品 verdict、操作结果、审核资格或清理状态。

## 步骤树

- 执行阶段与 cleanup 分别按原始记录顺序展示，父步骤在子步骤前。
- flow、ensure_state recipe 和其它已有 `sub_steps` 记录逐层展开。每行使用该记录的 `step_id`，并标注执行/清理阶段及直接父步骤。
- 父级关系来自记录树，不从步骤名称或点号前缀推断。无子步骤的历史记录仍按叶步骤展示。
- `outcome`、最终观测、定位和恢复信息沿用该行原记录。父流程通过不能把子步骤的 error、blocked、skipped 或 pending 改成通过。

## 尝试次数不是动作总数

执行器的父 flow/recipe 记录已有子步骤 attempts 汇总。报告标成“汇总 N”，不能再与子步骤次数相加；运行级“动作数”仍直接展示 `run.actions`。定位和恢复栏也仅投影该行记录，不提供额外动作计数。

## 监视时间线

每份 monitor 只在实际拥有它的步骤下展示一次，并标注阶段及直接父级。嵌套流程的 monitor 不再借用最外层步骤 ID；cleanup 内的 monitor 同样展示。状态、命中、采样时间和观测 ID 保留原值。

报告展示证据所在步骤，不证明证据充分或产品符合预期。离线合成测试不是实机验收，未确认判据、未知状态和未完成清理仍按原记录保留。

## 验证

`python -m pytest tests/test_report_step_identity.py -q`（在 `vpd_skill` 下执行）覆盖直接父级、执行/清理、多层 flow、真实 ReplayRunner recipe、monitor 归属、非通过结果、汇总计数、历史叶记录及输入零修改。全部使用合成记录或离线替身，不连接设备、不确认生产知识、不批准资产。
