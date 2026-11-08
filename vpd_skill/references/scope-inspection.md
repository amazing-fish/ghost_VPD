# 母版 scope 的离线诊断

`case inspect-scope` 检查**一份已有历史证据**里的候选范围是否符合 240/241/243 当前母版的结构约束。它解决“未校准时无法先检查”的缺口，不需要先把目标标成 verified。

它不连接设备，不修改证据/资产，不登记生产 target，不生成业务预期或实例，不确认 Oracle、审批或晋级。CLI 这一路径不加载 `config.example.json` / `config.local.json`；其他命令仍保持原配置加载方式。

## 用法

```powershell
.\vpdtest.ps1 case inspect-scope --input .\scope-candidate.json --evidence-dir .\runs\session-example --obs-id obs-0001
```

输入 JSON 仅含以下两项。下面是**合成示意**，不是已验证生产定位：

```json
{
  "template_id": "TC-QK-240",
  "candidate_target": {
    "side_effect": "read_only",
    "forbid_tap": true,
    "strategies": [{"kind": "id", "value": "synthetic-name-container"}]
  }
}
```

候选目标可以尚未登记；只允许一个静态 id 或当前母版允许的结构 XPath。结构 XPath 不能用预期名称/楼层作文本锚点、运行时变量或任意函数。允许可读 label/source 说明，不接受 status、calibration、template_scope、expected_name、expected_floors、滚动、输入、secret 或后备策略。

## 结果如何读

| 字段 | 表达什么 | 不表达什么 |
| --- | --- | --- |
| `localization.status` | 本帧唯一、缺失、多匹配、隐藏、裁剪或不可用 | 当前手机仍在同一状态 |
| `shape.compatible` | 与当前母版的专用容器结构相容/不相容/证据不足 | 业务显示正确、已校准或可执行 |
| `shape.evidence_usable` | 本次诊断是否具备足够且一致的检查材料 | 原始资料真实性或当前执行资格 |
| `semantic_role` | 始终 `requires_human_review` | 自动认定此容器就是园区名称/数量/楼层 |
| `current_applicability` | 始终 `not_checked` | 跨版本/车辆/园区已实测覆盖 |
| `production_calibration` | 始终 `not_granted` | 生产校准已获批准 |

`diagnostic_status=inspected` 仅表示材料已完成诊断，不表示 shape 相容，更不是产品 passed。
`incomplete` 表示缺材料，`conflict` 表示非法/损坏/矛盾证据。详细原因在 issues；inspection_errors 是工具错误。
`shape.compatible=true` 可能与 incomplete/conflict 并存：结构相容不代表材料完整一致。还须核 evidence_usable、issues 与 inspection_errors，不能只看一个布尔值。
退出码 0 只表示诊断命令完成，包括缺材料或结构不相容；参数/工具错误及 inspection_errors 非空返回 3。不能用退出码替代字段检查。

240/241 检查专用容器自身无非空 text、且恰有一个非空 text 后代。243 列出观测到的 text 集合及重复情况，检查当前母版结构，不把观测集合变成 expected_floors。隐藏祖先、description 借名、子节点裁剪或越出 scope 都不能提供完整显示证据。

混合信息行、整页根节点、带自身 text 的叶节点可能不符合现母版；诊断须如实报告，不能为了生成实例伪登记。真实 UI 若没有合适的专用容器，后续另做适配，不改变产品要求来凑通过。

## 证据边界

- 只检查指定目录中的固定 `obs/<obs-id>/observation.json`、`tree.json`、`screen.png`，以及 run/session 身份。JSON 内路径只能核对，不用于追随任意文件；跨 obs、路径越界和身份冲突不能作为一致证据。
- 核对 obs_id、APP bundle、device/source/foreground 等历史身份；未记录的信息保持缺失，不从其他观测拼成同一帧。
- `fresh=true` 只描述采集当时。历史资料不授予现在的设备动作许可，也不证明现在仍适用。
- 现算 SHA-256 绑定**本次读到的字节**。历史树原记录没有原始内容哈希，不能据此认证原始同帧、真实设备来源或防恶意伪造。
- 缺截图可继续树结构诊断，但必须保留视觉核验缺口。文件存在或哈希匹配不证明实际像素、字体省略/裁切、任意遮挡、隐藏或虚拟化集合完整。
- 输出可能含被观察文字和历史设备标识；应在原证据所在机器审阅，不自动外发原件或诊断输出。

## 从诊断到可审阅实例

1. 在本机已有证据上检查候选 scope，核具体结构及缺口。
2. 独立核对容器的业务角色、实际截图和适用环境，再登记真实校准来源。此命令不会替你登记。
3. 从独立要求/测试数据取得名称或楼层预期，不能从被测 UI 反填。
4. 使用 [case instantiate](case-templates.md) 预览完整冻结实例，核 lint、来源、内容/依赖与当前审核绑定。只有明确写入才新建 draft；没有旧审核、重放或产品结论可继承。

本次引擎代码变更仍按既有 engine_hash 规则影响审核绑定；不会为了新增只读命令豁免旧审核失效。后续实际执行/晋级前，须按当前绑定检查，不自动重绑。

241 的 ASCII 空格/正整数格式源于旧脚本的限制，不是已确认的普适产品规范；243 的无序精确集合仍是未确认提案。该诊断不改变任何 Oracle 的确认状态，也不要求现在追加业务裁定。

后续现场的最小步骤由现场负责人协调：保持用户已准备的车辆和 VPD 首页，用现有 observe 入口采一份同次观测的树/截图及版本/形态元数据（顺序采集，不承诺像素级原子同帧）；原件留在本机。不要为了校准冷启动、切回旧车或触发激活。页面变化/弹窗/锁屏则先报告状态，不把离线诊断当动作授权。

Refs [#65](https://github.com/amazing-fish/ghost_VPD/issues/65)、[#135](https://github.com/amazing-fish/ghost_VPD/issues/135)。
