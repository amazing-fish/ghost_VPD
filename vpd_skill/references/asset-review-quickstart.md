# 资产审核：你需要决定什么

**你不需要先学命令，也不需要批准所有用例。** Agent 应先挑少数值得复用、资料完整的用例，给你一份能判断“这个测试是否测对了”的材料。你对具体用例回复“同意按这份版本复用”“这里需要改”或“暂缓”，Agent 再记录。笼统的“继续推进”不等于批准尚未展示的资产。

## 先给你看什么

每条待审核用例的主区必须说明：

- **测试什么**：用例 ID、目的、适用条件，以及明确不覆盖什么。
- **怎么测、怎样算对**：关键步骤、预期结果和独立要求来源。不能把同一轮看到的现象直接当预期。
- **实际证据**：原始来源、关键截图和对应运行；截图用缩略图可放大。缺截图或没跑过要直说，不能用离线图冒充真机效果。
- **要你决定什么**：是否认可这份用例定义可复用；与上次相比改了什么、旧审核为什么失效。完整用例和所绑定配置应可查看，冗余日志可折叠。

资料不全、预期有争议时，Agent 先补资料或提出具体问题，不让你对整批 JSON 盲签。`case review` 只返回审核元信息，**不是完整用例阅读页**；完整定义在所选工作区的 `cases/<ID>.json`。

## 三件事不是一回事

| 动作 | 说明 |
| --- | --- |
| 资产审核 `case approve` | 记录你认可的用例定义与当前绑定版本。不会让用例变成“通过”。 |
| 晋级 `case promote` | 按已有条件从 draft/探索记录进入 candidate，再到 replay_verified、regression。不是一键直达正式回归。 |
| 产品要求确认 `knowledge confirm` | 独立确认“产品应该怎样”的依据；资产审核不替它做决定。 |

以上都不是本次设备动作授权。候选用例不是已验收功能；稳定复现的产品缺陷也可以成为回归资产，但必须保留失败结论和缺陷单。

## Agent 操作入口（用户可不看）

当前没有资产审批网页按钮或 HTTP API。Console 展示的是**当次运行记录里的**审核状态，不是当前资产审批页。真实入口是 CLI。

命令在 `vpd_skill` 目录执行，先按 README 完成安装。默认数据为该目录的 `data/`；若使用其他工作区，应在命令开头加 `--workspace <数据目录>`，不要误改旧工作树的数据。

```powershell
# 只读：确认具体资产的当前状态、缺口和审核绑定
.\vpdtest.ps1 case readiness <用例ID>
.\vpdtest.ps1 case lint <用例ID>
.\vpdtest.ps1 case review <用例ID>
```

`not_required` 表示该资产当前不要求这项审核，不必补签；`pending` 为待审核，`stale` 为原批准与当前定义不匹配，`revoked` 为已撤销，`approved` 为当前有效。

**仅在用户已经看过并明确同意具体版本之后**，Agent 记录实际审核人和可追溯依据，不自称用户、不自行批准自己的资产：

```powershell
.\vpdtest.ps1 case approve <用例ID> --reviewer "<实际审核人>" --basis "<用户确认的具体用例版本、证据及评审记录>"
.\vpdtest.ps1 case review <用例ID>
.\vpdtest.ps1 case readiness <用例ID>
```

审核依据保存为本地审计记录，不是账号认证。Agent 应对照 `current` 绑定核实用户看过的版本；若期间有相关变化，不能用旧同意覆盖新内容。

满足条件且任务范围允许时，再单独晋级：

```powershell
.\vpdtest.ps1 case promote <用例ID> --to candidate
# 之后按用例本身的前置条件及动作权限运行；记录至少两次独立、确定、一致的有效重放
# run <用例ID> ... --record-replay
.\vpdtest.ps1 case promote <用例ID> --to replay_verified
# 正式回归还要核对已确认且适用的判据、真机重放及终态评审人
.\vpdtest.ps1 case promote <用例ID> --to regression --reviewer "<实际评审人>"
```

不要为了晋级自动加 `--allow-offline`、删除阻塞历史或改预期。具体门槛和撤销流程见[工作流](workflows.md#探索资产人工审核30)；只读盘点口径见[资产就绪检查](asset-readiness.md)。
