# `session-s293` AA 结构与动作记录导出（2026-10-27，工作机执行）

本目录是[工作机搜索源件存档](../README.md)的配套原始材料，来自同一台工作机的**同一次会话** `session-s293`（执行器 `vpdtest`，main `293a496`，设备 `hdc:5MT0225B05000904` / SGT-AL10 / OpenHarmony-7.0.0.111 / 直板 1320×2848 / 乾崑 `com.yinwang.qiankunapp.hm` 1.0.26.100 / 园区「金桥出口加工区」/ 已登录未强制登出）。

导出方式：直接读该会话 `runs/session-s293/obs/<obs>/tree.json` 与 `observation.json`，提供节点属性与父子路径的结构化导出；不是源 `tree.json` 的逐字节副本，区域文件为子集，行字段/LCA为派生索引。原始 run 目录仍留在执行机，本目录带可审的结构化树导出与 `steps.jsonl` 动作原记录，不等于完整CLI返回体。

## 文件

| 文件 | 内容 |
| --- | --- |
| `obs-0015-entry-region.json` | ADS tab **入口页**（园区卡 + `搜索` 栏 + `我的位置`）纵向 300–800 区域的全部节点及其到根祖先链 |
| `obs-0028-entry-region.json` | 被拒动作发生瞬间的同一区域（用于证明 locate 命中的容器就是入口页搜索栏） |
| `obs-0039-search-page.json` | 由入口页搜索栏进入的**搜索页**完整树（`page.state=qk_spot_search`） |
| `obs-0035-favorites.json` | 收藏及常去面板完整树 + 各收藏项的行容器与父链 |
| `obs-0046-name-search.json` | 查询「1号车位」（名称维度）结果首行的行容器与父链 |
| `obs-0051-id-search.json` | 查询「I1」（编号维度）同一实体行 |
| `obs-0056-spot-name-search.json` | 查询「1号停车点」（路点）结果首行 |
| `obs-0061-default-spot-search.json` | 查询「333」（默认车位）结果首行 |
| `local-only-targets.json` | 工作机本地工作树存在、主线未定义的 4 个 target 原文（含 `qk_tag_elevator`） |
| `targets-states-diff.md` | 本机实际树 vs 主线 targets/states 的失配清单与观测出处 |
| `actions/seq*.json` | 关键动作的 `steps.jsonl` 原始 `step_record`（不是完整CLI外层返回）（入口页重按、两次点按被拒、成功进搜索页、注册输入框 locate 失败） |
| `manifest.json` | 上述文件与本机原件的 sha256、obs/step 对应关系 |

## 字段口径

- `nodes[].attributes` 为工作机导出的节点属性键值（源树原件未随附，未独立核其逐字节一致性）：`type` / `id` / `text` / `originalText` / `description` / `bounds` / `origBounds` / `clickable` / `longClickable` / `visible` / `enabled` / `checked` / `checkable` / `selected` / `focused` / `scrollable` / `opacity` / `accessibilityId` / `key` / `zIndex` / `clip` / `displayId` / `hitTestBehavior` / `hostWindowId`。
- **导出键集已核24种**，包含 `visible`，不含 `editable`。原始源树“33键”和191节点可见性统计仍是工作机报告；原始全树未随PR携带，维护侧无法独立核其完整键集。导出的空字符串也不自动证明输入来源有效；可输入性继续沿类型、当前输入来源与策略校验，不能从未导出字段推定权限。
- 入口区域文件只保留纵向区间内的节点与它们到根的祖先链（`in_region=false` 的节点是为保留父子关系补入的祖先）；完整树用 `raw.tree` 指回执行机路径与 sha256。
- `rows[].lca_path` 是**该行若干字段节点的最近公共祖先**，由路径前缀计算，不是按截图文案猜的；同时给出 `chain_to_root`，可直接看出行容器（如结果首行的 `Row[165,818][1266,1045]`，其中 `路线` 按钮与名称/楼层同属这一行）。
- 同一行内字段存在并列候选时（例如搜索框里残留同名查询词），按「字段节点并集包围盒最小」选择，规则写在导出脚本注释里；`field_paths` 保留最终选中路径，可人工复核。

## 这份材料能证明/不能证明什么

**能证明**：入口页真实控件与文本、`text=搜索` 的可点容器是 `Row[54,518][1020,707]`、该容器声明 `vpd_entry` 被策略拒绝的 step_record、搜索页的真实输入框结构（`Search[@id="search_input"] > SearchField[@id="__SearchField__search_input"]`）、四次搜索各自的首行字段与行容器、收藏面板的真实项。

**不能证明**：产品结论（判据仍 unconfirmed）、请求/加载终态、VPD 首页的两种文案（本轮没进到首页）、以及这些字段是否「本应如此」——本目录只提供现场事实。

## 维护侧核验与边界修订

- `manifest.files` 可重算本库导出文件的 bytes/SHA-256；`raw_tree_sha256`、截图hash指向未提交的执行机原件，只作为来源声明，不能在本库独立认证其字节。
- `nodes:null` 的入口文件实际节点位于 `region.nodes`，`node_count`是源节点数报告；`manifest.obs_map.exported_node_count`记真实导出数量。
- 动作记录来自 `steps.jsonl`，不是完整CLI返回。seq13为`target_unresolved`且未含dispatch记录，不能引作字面`dispatched=false`；seq12动作终态是obs-0038，obs-0039是后续独立观测。
- 五次成功输入的完整动作记录未随本PR提交，相关passed只保留为工作机报告。维护侧可依据补交树重建并验证选择器，不把重建当新一次真机执行。
- 主线状态模型重算：obs-0039为qk_spot_search，四份非空查询结果树均unknown；占位文案消失后旧状态签名失配，作为后续states修订输入，不改写现场原判定。
