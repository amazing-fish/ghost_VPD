# 只读资产就绪度与晋级预检

现有成熟度保持 `exploration_record / draft → candidate → replay_verified → regression`。
成熟度、人审状态、产品结论、记录的验证级别与正式覆盖各自展示，不合成为新的分级或评分。

```powershell
.\vpdtest.ps1 case readiness
.\vpdtest.ps1 case readiness TC-QK-215 TC-QK-232 TC-QK-240
.\vpdtest.ps1 case readiness TC-EXAMPLE --to regression --reviewer <评审人>
```

不传ID时仅枚举当前workspace的 `cases/*.json`。**目录枚举不能发现从未入仓的源件**；
只有显式传入预期ID后，缺文件才列为 `source=missing`，不替它推定成熟度或审核状态。
显式ID保留顺序、去重；不接受路径。不触发设备连接、写文件、approve、confirm、重绑或晋级。

| 字段 | 使用既有机制 | 结论边界 |
| --- | --- | --- |
| `status` | 源文件成熟度（缺省沿引擎为draft） | 不自动提升；缺源件为null |
| `review` | `cases.review_status` | pending、stale、approved、not_required保持区分 |
| `issues` | `cases.lint` | 当前定义/本workspace引用检查，不是实机验收 |
| `replay` | `cases.replay_basis` | 既有内容/依赖版本与重放记录资格，不核验原始包字节 |
| `recorded_product_status` | 资产已有product_status | 历史声明，不重新产出产品passed/failed |
| `recorded_validation_levels` | 资产replays中的validation_level计数 | 历史记录，不推断E2/E3，不证明原件已取得 |
| `oracle_references` | 现有展开引用与知识存储状态 | 未采集当前事实，不把confirmed直接当作当前适用 |
| `promotion` | 原 `cases.promote(..., dry_run=True)` 全部门槛 | 只预检，不写history/product_status/status |

`review.changed_binding_fields` 仅指出旧binding与当前binding不同的维度。
没有可比较binding时 `binding_available=false`；有额外字段则另记数量，仍遵守原相等检查。
旧规则下的审核带 `engine_hash` 字段，与当前绑定不相等，计为 stale，需重审一次。
只读差异解释不补签或复用旧reviewer批准。

## 审核后的引擎文件差异

审核绑定不含引擎源码。判定语义变化时 `cases.RESULT_CONTRACT` 加 1，经依赖指纹使审核失效；
纯重构、注释和与判定无关的改动不使审核失效。

`case approve` 产生的审核记录附 `asset_review.engine_snapshot`：schema=1、相对引擎路径和各文件SHA-256。
`case review` 与 `case readiness` 的 `engine_changes` 据此列出 `added / removed / modified` 相对路径及
`unchanged_count`，供人判断是否需要复核；它只是诊断，不改变 valid/state。完整清单保留在资产文件，
不在每份run/suite简报的 `record` 中重复。

没有快照时返回 `available=false, reason=snapshot_missing`；快照损坏、schema不支持、清单路径/摘要格式无效、
缺 binding，或旧记录的快照与其 `engine_hash` 不符时，各自明确unavailable及原因，差异列表为null，
不能把缺证据解释成“无变化”。清单允许采集器实际产生的中文、空格及隐藏目录相对路径；绝对路径、
驱动/反斜杠、点段、空路径段与控制字符仍拒绝。路径只作数据比较，不用于读取旧清单指定的文件。

自洽快照仅帮助定位引擎文件变化，不证明原始源码包、审核者身份、设备证据包或运行适用性，
也不自动产生人审、判据确认、设备授权、成熟度晋级或正式覆盖。快照并非整个工作区的原子锁定；
文件变化后仍须重新核对，不能把诊断当长期准入凭据。

预检默认使用 `cases.NEXT` 的下一阶段，也可用 `--to` 指定。`--reviewer`、`--allow-offline`、
`--known-issue` 只作为同一套晋级检查的参数；不产生授权、人工审核或实测证据。
`eligible` 仅表示这些选项下当前晋级检查通过，不表示可操作设备、已正式覆盖或以后持续有效。
candidate按既有规则可含active但unconfirmed的判据；正式regression仍按原门槛核对重放中的判据上下文、
评审人、缺陷登记和真机记录/显式离线例外。现有 `case promote` 的默认写入行为保持，实际晋级仍重新检查。

`scope.runtime_applicability=not_checked` 表示不采集当前实机事实；正式回归预检对已有重放事实的检查仍执行。
`evidence_packages=not_checked` 表示不校验原始截图/树/run字节完整性；lint仍可发现本workspace缺少引用目录。
`coverage=not_assessed`，不发布正式覆盖率。需要业务决策时仍应提供逐条背景与实际截图；缺图记录待补，
在下次同项实测前提醒补采，不凭本报表要求批量批准。

检查前先核对已知集合形状（步骤、断言、来源、历史、重放及其断言/事实等），
已知源件数据错误逐行列为 `source_error`，顶层 `incomplete=true`，后续资产继续检查；缺源件也标incomplete。
未预期的检查异常逐行列为 `inspection_error` 并保留trace，后续资产仍检查；汇总的 `inspection_errors>0` 时
CLI退出3，不能把实现故障藏在成功退出码后。CLI的成功退出码表示生成报告，须读取JSON中的
`incomplete`、`error`和`promotion.status`。本命令不为整个workspace获取文件锁，文件变化后应重算；
正式执行和晋级仍各自核对当时的绑定，不能把预检当作长期准入凭据。
