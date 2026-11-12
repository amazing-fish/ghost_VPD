# HM002 导航适用性候选（#245）

这是**尚未启用、尚未真机验证的 draft**，不是新的已批准资产。生产 `data/cases/TC-MOTOR-HM-002.json` 完整保留；不 approve、不 promote，不复制旧 run 作为新证据。

## 改动和边界

- 精确源提交、源文件规范化 SHA-256（UTF-8、换行统一 LF、保留末尾换行；跨平台一致，不是 checkout 原始字节 hash）、原批准绑定见 `baseline.json`；该绑定仅作溯源，不授权此候选。
- 原内容 hash `b4de659323c5`；候选内容 hash `13d48838551b`。
- 业务定义仅增加一个 page condition、S2/S3 相同 when、A1/A2 相同 guard。原 steps、expected、critical、timeout、retry、Oracle、cleanup 均保留。
- 去除 history/replays/asset_review/product_status，status 改 draft；保持同一 case ID 方便对照，因此**绝不可覆盖日常工作区文件**。
- 配套 `targets.patch.json` 只在独立工作区新增两个 TabBar 内树定位目标，专供条件观测；不改原 tap 定位。条件不支持 inline target，因此补丁是必要依赖。
- 有爱车且无购车：执行原往返及选中态校验；有购车且无爱车：业务段不执行、not_applicable、零验证覆盖；两者缺失或同时出现：pending。页面标题不作页签证据。
- 这不是登录状态检测，也不是跨版本、侧栏或折叠屏通用批准。购车/爱车语义由实际可见导航选择分支，产品成功仍独立检查原选中态。

## 工作机怎么验证

1. 拉取包含本候选的提交。先运行下面的本地测试，检查源 hash 与候选差异没有变化。
2. 创建**全新独立目录**，从 `baseline.json` 指定的固定提交导出 Git 已跟踪的 `vpd_skill/data`，作为该目录下的 `data`；不是复制工作机日常使用、可能已变动的工作区 data。不要带入日常工作区的 active 经验或其他本地状态，不复用现有 runs、session，不覆盖原工作区。记录原生产批准状态供对照。
3. 仅在复制出来的 `data/apps/motor_hm/targets.json`，把 `targets.patch.json` 的 `targets` 两项合并进 `targets` 字典，遇到同名项立即停止；保存前后差异。不要修改原 tap 策略或政策。
4. 候选用例以 `read_json` 读取此目录的 `TC-MOTOR-HM-002.draft.json`，通过现有 `run_case(Workspace(隔离data目录), case=候选, device_spec=当前设备, propose=False, require_asset_review=True)` 执行。不保存为生产 case、不运行批准命令。执行前调用现有 `cases.lint`，必须无问题。
5. 先核设备与现场，使用现成页面，不登录、不注销、不触发车辆激活。当前页有爱车时验证完整往返；当前页有购车时验证明确不适用。另一分支没有真实现场就标待验，**不要为了凑矩阵改变账号**。记录版本、布局、实际页签、S2/S3结果、A1/A2及 coverage；截图在工作机本地保留，仅提交 issue 结果，不外发文件图片。
6. 任何未知、冲突或真实切换错误都不得手改成不适用。报告真实失败、未执行与待验，不把合成 Replay 当实机。

普通 repo_code 来源 HM002 的 `required_review` 当前为 false；现有入口允许 draft 诊断执行，即使传 `require_asset_review=True`，结果仍明确 `asset_review.valid=false`。这个参数不是“强制批准所有用例”的开关，不能据此声称候选已获批准。批准资产复用/晋级是之后独立的实际证据与评审步骤。

## 离线验证

```powershell
$env:PYTHONPATH='vpd_skill'
python -m pytest vpd_skill/tests/test_hm002_candidate_migration.py vpd_skill/tests/test_published_regression_assets.py vpd_skill/tests/test_hm_navigation_applicability.py -q
```

测试加载本候选，使用生产 app/knowledge/policy 和隔离合成 Replay world；不更改生产期望、运行步骤或知识适用范围。覆盖基线规范化 hash 校验、最小内容差异、旧批准失配、原批准仍有效、爱车/购车/缺失/冲突、TabBar 外同名标题、真实切换故障及采集错误、无 TabBar 窄屏反例、隔离副本标准 loader 入口。所有离线结果仅证明设计和引擎语义，不是实机批准。
