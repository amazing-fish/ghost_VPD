# `session-s293` 原始 AA 树导出（2026-10-27，工作机执行）

本目录是[工作机搜索源件存档](../README.md)的配套原始材料，来自同一台工作机的**同一次会话** `session-s293`（执行器 `vpdtest`，main `293a496`，设备 `hdc:5MT0225B05000904` / SGT-AL10 / OpenHarmony-7.0.0.111 / 直板 1320×2848 / 乾崑 `com.yinwang.qiankunapp.hm` 1.0.26.100 / 园区「金桥出口加工区」/ 已登录未强制登出）。

导出方式：直接读该会话 `runs/session-s293/obs/<obs>/tree.json` 与 `observation.json`，逐节点保留 AA 属性与父子关系；**没有手写摘要、没有把文字列表冒充树**。原始 run 目录仍留在执行机，本目录只带可审的树与动作返回。

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
| `actions/seq*.json` | 关键动作的执行器**原始返回**（入口页重按、两次点按被拒、成功进搜索页、注册输入框 locate 失败） |
| `manifest.json` | 上述文件与本机原件的 sha256、obs/step 对应关系 |

## 字段口径

- `nodes[].attributes` 为 AA 树原样键值：`type` / `id` / `text` / `originalText` / `description` / `bounds` / `origBounds` / `clickable` / `longClickable` / `visible` / `enabled` / `checked` / `checkable` / `selected` / `focused` / `scrollable` / `opacity` / `accessibilityId` / `key` / `zIndex` / `clip` / `displayId` / `hitTestBehavior` / `hostWindowId`。
- **`visible` 有，`editable` 没有**：本机树共 33 个键，`visible` 几乎每个节点都有（入口页 191 个节点里 190 个带 `visible`，全 `true`；搜索结果页 149 个节点里 4 个 `false`，分别是不可见的历史/隐藏节点）。**`editable` 这个键在本机 AA 树里完全不存在**，所以「可输入」只能由节点类型（`SearchField`/`TextInput`）+ `focused`/`clickable` 或 `allow_input` 判断，不能用 `editable` 写断言。
- 入口区域文件只保留纵向区间内的节点与它们到根的祖先链（`in_region=false` 的节点是为保留父子关系补入的祖先）；完整树用 `raw.tree` 指回执行机路径与 sha256。
- `rows[].lca_path` 是**该行若干字段节点的最近公共祖先**，由路径前缀计算，不是按截图文案猜的；同时给出 `chain_to_root`，可直接看出行容器（如结果首行的 `Row[165,818][1266,1045]`，其中 `路线` 按钮与名称/楼层同属这一行）。
- 同一行内字段存在并列候选时（例如搜索框里残留同名查询词），按「字段节点并集包围盒最小」选择，规则写在导出脚本注释里；`field_paths` 保留最终选中路径，可人工复核。

## 这份材料能证明/不能证明什么

**能证明**：入口页真实控件与文本、`text=搜索` 的可点容器是 `Row[54,518][1020,707]`、该容器声明 `vpd_entry` 被策略拒绝的完整返回、搜索页的真实输入框结构（`Search[@id="search_input"] > SearchField[@id="__SearchField__search_input"]`）、四次搜索各自的首行字段与行容器、收藏面板的真实项。

**不能证明**：产品结论（判据仍 unconfirmed）、请求/加载终态、VPD 首页的两种文案（本轮没进到首页）、以及这些字段是否「本应如此」——本目录只提供现场事实。
