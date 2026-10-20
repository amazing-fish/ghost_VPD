# 架构与技术债审计（2026-10-21）

审计基线：[`main@3273f27`](https://github.com/amazing-fish/ghost_VPD/commit/3273f27ee5bc7d129ceff8357b15c720a42648e2)，#66/#67 已合入。证据为实际代码阅读与隔离工作区的合成 Replay；没有操作真机、确认生产知识或批准资产。

后续对账：#68～#73 已合入 `main@8657630`：复合判定、ensure_state 终态、共用结构检查、Python 3.12 安装/两平台 CI、依赖绑定及 Console 证据层级均已补齐。本次文档修订只收拢重复审核/晋级说明，不改变运行时规则。完整语义见[执行契约](../vpd_skill/references/contracts.md)，当前操作说明见[工作流](../vpd_skill/references/workflows.md)。下文保留原审计时点的证据与工作包范围。

## 整体判断

现有 Skill 分层可以继续使用。当前最值得优化的是层间契约的一致性：同一检查不能因为包进 flow 就换结论；人工审核、重放资格和展示也应引用同一份适用证据。文件长、功能多、存在延迟导入，本身不构成重写理由。

交付单位仍是能独立判定、重复执行且证据可追溯的 VPD 功能用例。登录、解锁、导航是到达业务起点的工具；动作成功、产品通过、用例成熟度和正式覆盖分别记录。

| 边界 | 当前实际职责 | 应保留的设计 |
|---|---|---|
| 宿主 / CLI | `cli.py`、`commands.py` 解释请求、管理会话；宿主处理未知现场与人工交接 | 已知用例交给程序运行；不让 Agent 为每个固定步骤重新规划 |
| 观察 / 执行 | `observe.py` 留存观测，`execute.py` 定位、后置检查和有限恢复，`dispatch.py` 约束实际下发 | flow/recipe 复用同一执行链；不引入第二套登录或动作框架 |
| 独立判定 | `conditions.py` 判适用性，`verdict.py` 消费当前证据及适用 Oracle | 未知、未触发、工具错误、正常交接与产品失败不可互换 |
| 资产 / 证据 | `cases.py` 管内容、依赖、审核和重放资格；`runner.py` 生成原始 run；报告和 Console 消费结果 | 来源、人审、产品知识确认、成熟度各有职责；不新建状态数据库 |
| 设备适配 | Harmony 和 Replay 实现同一设备接口 | Replay 只证明合成环境逻辑；不冒充真机或业务覆盖 |
| Legacy / 人工 Console | `run.py → Huey → app/service/vpd/base.py` 是旧服务链；Console 手动输入另记会话 | Skill 不 import Legacy；不把人工输入或旧服务当受控自动化的替代入口 |

Legacy 仍在仓库，根 README 仅有旧标题；是否还在部署未知。当前只能确认入口及隔离事实，不能据此宣布旧服务可删除。其配置凭据治理已有 #45 记录，本轮未读取凭据值、未迁移或重写历史。

## 按实际影响排列的技术债

| 优先级 / 类型 | 已核证据与影响 | 处理边界 |
|---|---|---|
| P1，判定缺陷 | `execute.py::_run_children` 允许非关键失败后继续；`verdict.py::case_verdict` 却主要检查顶层，只有 error/pending 有专门递归。真实 `run_case → Replay → Executor` 反例：相同子步骤的 confirmed 失败、unconfirmed 候选或 blocked，包进成功父流程后整例可 passed；关键子步骤还可能使用父层其他 Oracle 误判。 | **本轮唯一运行时优化包**：在现有 Verdict 中统一投影实际子步骤，保留父子路径、正确 Oracle 和原始观测。`critical` 继续控制是否停止执行，不承担隐藏结果的职责。 |
| P1，执行前后契约 | `cases.py::iter_case_steps/lint` 已识别递归 flow/recipe，但执行入口仅选择性复用检查；重复步骤 ID 可被 `by_id` 覆盖。通用 `ensure_state` 也没有统一复核 recipe 完成后的目标状态；当前登录配置用末尾显式检查规避。 | 后续复用现有遍历，在副作用前拒绝无效结构，并补目标终态核验；不把全部资产治理 lint 强制加到 informal 诊断上。 |
| P1，依赖绑定缺口 | `dependency_hash` 只绑定显式 Oracle；`knowledge.py` 的冲突裁决还依赖同 subject 的其他条目。隔离反例中新增相反条目后 Oracle 已 conflicted，但 deps 和审核仍有效，旧两次 Replay 仍能晋级 `replay_verified`。`regression` 仍被当前 Oracle 检查拦住。模板内容也仅绑定审核，未绑定重放指纹。 | 独立包候选：把实际影响裁决的条目、被引用模板字节接入既有依赖指纹；不要用整个引擎哈希粗暴清空所有重放。 |
| P1，安装与平台验证 | `install.ps1` / README 声称 Python 3.9+，固定的 NumPy 2.5.2 实际要求 3.12+；已有 `.venv` 未单独检查版本。`.github` 无测试 workflow，两项 PowerShell 测试在 Linux 跳过。 | 独立包明确支持基线、验证现有虚拟环境，并复用现有 pytest 做 Linux/Windows CI；不把 Linux 测试数当 Windows 通过。依赖依据：[NumPy 2.5.2](https://pypi.org/project/numpy/2.5.2/)。 |
| P2，结果展示 | `console/progress.py` 未透传 `validation_level/informal/asset_review`，主界面会显示“通过”“已验证”；Markdown 报告已有这些边界。 | 扩展现有 Console 展示已有字段即可，不建覆盖平台。 |
| P2，维护与证据准入 | 条件判断调用 Verdict 私有检查函数；部分测试仍依赖生产知识；重放晋级信任摘要而不自动核验原始运行包；经验偏好按策略 index 绑定。 | 分别在相应修改中收敛。完整证据包目前仍按 `BATCH_ASSET_ACCEPTANCE.md` 人工验收；不声称已有自动完整性校验。 |

## 本轮最小优化与验收

复用 #56 / #45，保持一个独立 PR：修复复合结果的判定与证据投影，更新对应结果依赖版本及相关消费者测试。原始历史 run/verdict 不改写；旧语义的重放不用于新语义晋级。执行器的动作顺序、策略、重试和 `critical` 继续语义保持原有边界。

必要正反例：同一检查平铺与嵌套结论一致；confirmed/unconfirmed/conflicted Oracle 正确区分；父层 Oracle 不替代子层；多层路径可追溯且复制的父失败不重复计数；已恢复的历史错误不重新变成当前错误；必需/可选 Cleanup 分别处理；CLI、报告、Console 和重放消费者保持可读。

不并入 Legacy 迁移、安装/CI、证据指纹、结构校验、Console 展示或新业务资产。#52/#54/#58 和 215/226/228 的原边界继续保留。

既有真机 prompt 的操作顺序可以沿用，但其中固定的 #66/#67 HEAD 不含本轮修复，版本区块须更新到本次候选或其合入版本。执行前核对实际源码与结果契约，按新语义重新积累运行；登录菜单、二级页安全归位、更新弹窗/黑屏及独立业务判据仍需 Windows/真机证据。
