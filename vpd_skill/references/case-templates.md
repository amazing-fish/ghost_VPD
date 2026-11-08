# 显示规则母版 → 冻结实例（240 / 241 / 243）

本切片让业务规则复用，不把旧车、旧园区和旧 build 变成永久业务前提。它**没有**自动确认通用 Oracle、审批资产或产生新的真机覆盖。旧 TC-QK-240/241/243、旧 K-QK-IDX-* 与历史证据保持原样。

## 三层分开看

| 层 | 本次实现 | 仍须补齐 |
| --- | --- | --- |
| 通用规则母版 | 名称精确比对、数量正整数显示、楼层标签集合比对 | 新 K-QK-TPL-* 均为 unconfirmed；核实具体语义与依据 |
| 冻结实例 | 字面预期、独立来源、已登记 scope 与校准指纹写入新 draft | 人核对来源与本次绑定后，才能按现有审核流程批准 |
| 本次环境与证据 | 实例不硬编码车/VIN/园区/build；定位校准有证据级别 | 当前车辆、园区、build、折叠形态仍须记录和核实；源声明不认证外部事实 |

用户已说明大部分业务规则不受车辆、园区和版本限制。这不等于每个页面结构已跨形态校准，也不把某个历史环境的实测范围自动扩大。

## 母版的准确范围

- **240 名称**：当前 VPD 首页的专用名称容器中，完整名称须等于独立测试数据中的名称。不是从本次读出的名称生成预期，不证明车辆已在该园区。
- **241 数量**：专用数量容器显示 `共 N 个`，N 为不含前导零的正整数。不证明 N 等于后台地图总量；零车位、其他文案或统计口径不在当前提案内。
- **243 楼层**：专用、完整暴露的楼层容器，其 text 标签须与独立非空预期集合一致，各出现一次、无额外非空标签，不要求顺序。**不证明隐藏/虚拟化项、后台地图全集、切楼层行为或车辆实际所在楼层。**

上述断言检查暴露 UI 树中的真实 text 与节点完整几何可见性，不验证字形栅格是否被省略号/字体裁切，也不证明任意视觉遮挡。对应截图仍是实机验收的必要补充。

只有三种模板，没有任意 XPath/断言程序输入，也没有运行时变量替换。名称及楼层在生成时转义并冻结。修改输入生成另一个实例，不能借运行时参数改变已审核断言。

## 前提：独立校准专用容器

输入只能引用 `apps/qiankun_hm/targets.json` 中已登记的目标，不能自行把本次输入标成生产校准。目标必须：

1. `side_effect: read_only`、`forbid_tap: true`，无滚动、输入或 secret。
2. 仅一个静态 id 或受限结构 XPath 定位，不能用预期名称/楼层定位容器，不能回退其他容器。结构 XPath 只允许元素路径和正整数位置谓词。
3. `template_scope.role` 与模板一致：`parking_name`、`parking_count` 或 `floor_labels`；`shape: text_container`。
4. `template_scope.calibration` 含 `status: verified`、`source`、`ref`、`validation_level`。真机要求目标 `status: calibrated_on_device`、source 为 `device_observation`、level 为 `real_device`；合成夹具只能用 synthetic 状态、synthetic_fixture 来源、synthetic 级别。
5. 容器独立于业务预期，是专用且完整暴露的 text 容器，不是整页或混合卡片。名称/数量各有一个非空 text 后代，楼层按完整暴露集合定义。

本 PR **没有登记任何生产 scope**。真实页面如果没有这样的容器，生成前需补校准，或另做结构适配，不能把页面根节点临时当作已验证 scope。登记与 ref 都是可追溯声明，仍须人审其真实来源；不是密码学证明。

## 使用方式：默认无副作用预览

准备 UTF-8 JSON，示意如下。目标名和证据地址必须替换为已核实的登记和独立来源，示例不是可直接运行的生产校准：

```json
{
  "template_id": "TC-QK-240",
  "new_case_id": "TC-QK-240-SITE-A",
  "scope_target": "registered_name_container",
  "scope_source": {"source": "device_observation", "ref": "evidence/name-scope.json"},
  "expected_source": {"source": "test_data", "ref": "fixtures/site-a.json#name"},
  "expected_name": "独立测试数据中的完整园区名"
}
```

241 不接受 expected_name/expected_floors；243 改用 `"expected_floors": ["B1", "L1"]`，接受 1–32 个无重复标签，按集合规范排序冻结。独立依据可以来自 requirement/test_data/reviewer；不得引用本次定位观测、runs/session/obs 引用或运行时/秘密参数。字段采用严格白名单。

```powershell
# 只读：返回冻结用例、内容/依赖指纹、lint 与审核 pending 状态
.\vpdtest.ps1 case instantiate --input .\name-instance.json
# 仅在 lint 无问题时新建 draft；已有 ID 拒绝覆盖，不运行设备
.\vpdtest.ps1 case instantiate --input .\name-instance.json --write
.\vpdtest.ps1 case review TC-QK-240-SITE-A
.\vpdtest.ps1 case readiness TC-QK-240-SITE-A
```

ID 仅接受 `TC-` 开头的大写字母/数字/连字符新 ID，不能覆盖三个来源用例。预览不创建目录、case 锁、运行或设备连接。`--write` 使用 exclusive create；并发同 ID 只允许一个创建成功，不覆盖任何已有内容。

## 没有冷启动/切车，不等于 Runner 绝无动作

生成的业务步骤仅在当前页面 raw read，零次重试、零超时；同帧验证 qk_vpd_home。清理只有零时长 wait，没有 ensure_state、launch、切车、输入或车辆控制，authorizations 为空。

**实际 run 的既有预检和打断处理仍可能解锁、处置系统弹窗或关闭 APP 已登记的良性弹窗。** 本功能不修改这些机制、不授予权限，不能将“母版业务步骤只读”解释为任何真机 run 都零动作。人工先准备当前车辆/园区及 VPD 首页、解除锁屏与弹窗；再按运行政策和授权执行。本 PR 的测试是合成证据，不是手机验收。

## 审核与晋级不能继承

新实例有 `origin.type=template` 和持久 template 来源，始终要求现有人工审核；只改 origin 为 manual 不会清除 source 的审核要求。生成不复制旧批准、成熟度、replays 或 product_status，不确认知识。

- 独立预期、scope、来源或步骤变化，需生成/审核当前实例；内容与依赖指纹据此重算。
- 目标定义、Oracle 语义/适用范围、执行引擎与审核上下文变化，可使旧审核失效。引擎指纹覆盖全部 vpdtest Python 文件，**本次代码升级也可能使既有审核 stale**，不是产品失败。
- 来源登记、模板/输入摘要都纳入用例审核材料；不可将同一业务规则跨园区复用误读成跨环境证据自动继承。

逐级门槛仍是现有机制：

| 下一步 | 必要条件 |
| --- | --- |
| draft → candidate | lint 无问题、当前有效人工审核；该级本身不要求两次重放 |
| candidate → replay_verified | 当前内容/依赖下至少两次不同运行给出确定且一致结果；稳定失败要登记 known issue |
| replay_verified → regression | 重验重放资格、终态评审人、必需 Oracle confirmed 且适用、至少一次计入真机重放；仅离线须既有显式例外 |

新 Oracle 未确认时，合成/真机运行即使结构断言满足，也不自动成为确定产品通过。人审、Oracle 确认、运行授权、资产成熟度、产品结论是不同动作。

## 后续实机验收的最小证据包

1. 当前 app/build、车辆/园区、宽窄形态与时间；必要时写“未知”，不可从旧记录补成当前。
2. 不依赖预期值的专用 scope 定位/完整暴露校准，及对应截图和 UI 树来源。
3. 独立预期：240 完整名称；243 应显示的标签集合；241 数量显示口径（本模板只断正整数格式）。说明独立来源，不读取 UI 后反填预期。
4. 冻结实例内容与依赖指纹、审核绑定、run/report 原始证据，以及错误名称、零/错误格式、漏/多/重复楼层等反例结果。
5. 明确建议：可先审 candidate，或因 scope/Oracle/重放欠缺继续补证。**不自动执行 approve、confirm、promote。**

关联：[#65](https://github.com/amazing-fish/ghost_VPD/issues/65)、[#135](https://github.com/amazing-fish/ghost_VPD/issues/135)；[审核与晋级工作流](workflows.md#探索资产人工审核30)、[依赖与重放](knowledge-rsi.md)、[资产 readiness](asset-readiness.md)。
