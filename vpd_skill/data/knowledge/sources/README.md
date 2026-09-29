# 产品侧业务知识库（260 条，导入源材料）

- 来源：`kb_mining/merge_kb.py` 结构化挖掘（2026-09-12），OneBox「VPD 测试问题主表」
  （主表 111 条）+ 车外对讲（135 条）+ wiki B（14 条）。
- 文件：`vpd_product_kb_260.json`，字段：title / category / endpoint / desc / symptom /
  cause / keywords / errorFeature / logFeature / version / origin{source, seq, dtsTicket,
  testDate, tracker} / dupHint。
- 统计：category = 业务逻辑 134 / 运行时 87 / API 集成 22 / 兼容性 12 / Native 5；
  endpoint = sdk 139 / host 52 / vehicle 26 / cloud 13 / unclear 30；dupHint 非空 84 条
  （同根因簇提示）；含 DTS 单号 76 条。
- 定位：**源材料（source corpus），不是 knowledge items**。性质≈运行观察与问题史，
  部分条目可提炼为 product_rule/domain_note 判据（提炼时按本架构 knowledge add 规则
  走 unconfirmed，source.type=observation 或 text_case，ref 指回本文件 origin.seq）。
  dupHint 同簇条目入库前应合并为「一类根因一条知识」。
- 注意：这是**问题史挖掘**（含已修复/已知问题），条目陈述 ≠ 当前版本仍然成立的产品
  规则——提炼判据时必须逐条核对当前版本。

## 与乾崑 APP 测试的关联
- endpoint=sdk/host 条目（191 条）覆盖 VPD 手机端交互的上下游（上电接口、token 失效、
  环境不匹配等），是 VPD APP 层异常中断（interrupts）判定的背景知识。
- 「特定场景无法选中目标车位」（origin.seq 2530，DTS2601050081649）与本 PR 探索目标
  同域——与 auto-vpd 实测的收藏 hit-test 缺陷（K-AV-009）属不同缺陷，注意区分。
