## 关联 Issue / 目标

- Issue: `Refs #...`（仅当所有验收项完成时使用 `Closes #...`）
- 预期结果：
- Base 分支及起始 SHA：
- Head 分支及提交 SHA：
- 是否为 stacked PR：否 / 是（依赖 PR #...）

## 实际改动与边界

- 改动模块、用户可见行为：
- 明确未改动的范围：
- 向后兼容 / 迁移说明：
- 本次变更是否修改 policy、知识 confirmed 状态或设备安全门禁：否 / 是（附人工批准记录）

## 验证证据

| 验证 | 环境与版本 | 结果 | 证据 |
| --- | --- | --- | --- |
| 原基线 | SHA / Python | passed / failed / skipped | 日志或报告路径 |
| 变更后单元测试 | SHA / Python | passed / failed / skipped | 日志或报告路径 |
| 真机试运行（若适用） | APP build / device_form | 行为结果 + 产品 verdict | run_id / obs_id |
| Cleanup（若适用） | 前置状态 | verified / unverified / blocked | post-observation |

**请区分**：执行器 `sent`、设备操作结果、产品 Oracle 判定；离线回放不等同真机验收。

## 动作边界与原文证据

- [ ] 用例凭据保持外置引用，原始 runs 与版本化资产分别保存；没有为隐藏异常改写现场证据
- [ ] 无越权设备操作；如含车控或运动，附单次审批与在场监护依据
- [ ] 首次失败、效果未知、未确认安全停止及尚未验证的断言未被掩盖
- [ ] 与该 PR 无关的 worktree、PR 和原始证据未被破坏

## 遗留问题与交接

- 未完成 / pending / blocked：
- 人工评审项与负责人：
- 如何在新 Agent 会话中复现：命令、工作区、关键证据（run_id / obs_id / 版本）：
- 自检：是否真的满足原 Issue 所有验收条件？
