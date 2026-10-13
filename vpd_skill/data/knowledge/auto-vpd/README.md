# auto-vpd 真机校准知识导入（2026-09-29）

> 来源：auto-vpd 技能（`C:\Users\h00899877\.dsh\skills\auto-vpd`，v1.2.0，华为乾崑 App
> HarmonyOS NEXT 真机自动化）2026-09-28/29 在共享测试机（阔折叠，hdc）上的全部探索与
> 回归沉淀。对应本 PR（阶段3c）`references/exploration-method.md` §5「乾崑真机校准清单」
> 的执行结果——清单第 1-4 步已由 auto-vpd 用自己的链路完成。

## 量级（本次导入）

| 类别 | 内容 | 数量 |
|---|---|---|
| 结构化知识条目 | `../items.json` 追加 K-AV-001~030（全部 unconfirmed，按本架构规则待评审确认；observation/reviewer 来源确认需 --basis） | 30 条 |
| 校准确认报告 | `device-knowledge.md`：对本 PR 乾崑切片 unverified/hypothesis 条目的逐条确认/修正/证伪 | 13 条（C1-C13） |
| 地图/车位域交互知识 | `map-interaction-knowledge.md`（页面导航图 + 9 条交互裁决 + 待探索清单） | 1 份 |
| 排障与环境知识 | `troubleshooting.md` §0-§21（登录/保险箱/键盘/形态/缺陷取证/环境事故） | 22 节 |
| 真机校准 UI 锚点 | `ui-catalog.json`（18 个页面域 ~80 锚点组，比例锚点跨三几何；已脱敏） | 1 份 |
| 原子能力清单 | `atom-registry.md`（14 个注册原子 = 已验证的原子能力目录） | 1 份 |
| 产品侧业务知识库 | `../sources/vpd_product_kb_260.json`（OneBox VPD 测试问题主表+对讲+wiki B 结构化挖掘） | 260 条 |

## 与本架构五类信息的对应

- **knowledge items**：K-AV-001~030 已按 items.json schema 入库（unconfirmed）。
  K-AV-001 与旧 K-QK-001 的入口冲突已由 2026-10-13 用户裁决（[#33](https://github.com/amazing-fish/ghost_VPD/issues/33)）：
  采用 K-AV-001，K-QK-001 保留为 deprecated 历史条目。K-AV-001 仍未确认，
  适用范围保持内部 debug 版 phone_fc78649，不将公网版「爱车页 ADS 按钮」与 debug「底栏 ADS tab」混为同一范围。
- **运行观察（证据）**：原文档中的 run 目录号/截图文件名保留为 ref，原始 runs 工件在
  auto-vpd 本机（本 PR 不含大二进制）。
- **执行经验**：troubleshooting §15/§16/§19（uiRecord 双通道、重启密码锁熔断、AAMS
  独占清理）= 可提炼为 locator_preference/中断规则的候选经验。
- **候选用例**：case2/case3/case4 系列已真机 PASS（auto-vpd 侧），其业务链路（登录→
  切车→VPD→搜索→算路上电；收藏链路被产品缺陷阻断）与 TC-QK-101/102 同构，可对照
  校准 flows/cases。
- **正式用例**：待本仓库晋级管线接手。

## 红线与安全

- 涉及车辆激活的操作（开始/重试/继续/确定-激活）auto-vpd 全程未执行（禁止激活铁律），
  K-AV-007/K-AV-008 记录的是裁决口径而非执行结果。
- 所有真机操作仅针对车主车 7885（K-AV-005）。
- 本目录是整理后的历史知识资产，未录入锁屏 PIN/账号密码等实际凭据；运行级原文取证已按 #32 移除脱敏门禁，不再以此历史资产格式描述执行器行为。
