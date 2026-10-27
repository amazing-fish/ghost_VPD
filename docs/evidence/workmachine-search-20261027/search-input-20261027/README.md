# 搜索输入目标实机复验 + #107 入口复验（2026-10-27 21:45–21:56，issue #118）

基线：main **`8f148c5`**（含 #116 `4f48860`、#120 `0b882a8`）。工作机不改代码、不开 PR。

- 设备 A：`6HQ0226505000341`（Pura X Max 折叠，折叠态 1264×1848），车 **2681**（爱车页 `赛力斯M9 F2A`），园区 **由度金闽园 共 695 个**。会话 `session-s120`。
- 设备 B：`5MT0225B05000904`（SGT-AL10 直板 1320×2848），园区 **金桥出口加工区 共 742 个**。会话 `session-s120m`。
- 两机均为乾崑 `com.yinwang.qiankunapp.hm` **1.0.26.100**，已登录、未强制登出。

## 1. 搜索输入目标复验（设备 A）

受控路径：`launch` → 爱车页 → `tap qk_ads --side-effect vpd_entry --authorize vpd_entry`（policy=任务范围允许 vpd_entry）→ `qk_vpd_home`（`obs-0006`，`搜索车位号设为目的地`）→ `tap qk_search_entry --side-effect navigation`（locate `[423,903]`，policy=通过）→ `qk_spot_search`（`obs-0009`）。

| 步骤 | 目标 | locate | dispatch | SearchField 实际值 | page.state | obs |
| --- | --- | --- | --- | --- | --- | --- |
| 输入前 | — | — | — | `''`（空，占位「输入目的地或车位号」） | `qk_spot_search` | `obs-0009` |
| A4 输入 | `qk_search_input` | found `[637,184]` bounds `[242,129,1033,239]` | `input_text` sent=true | `'1号车位'` | `qk_spot_search` | `obs-0012` |
| A5 清空 | `qk_search_input --clear` | — | **未派发** | `'1号车位'`（未变） | `qk_spot_search` | `obs-0013` |
| A6 清空（`--text ""` 重试） | `qk_search_input --text ""` | — | **未派发** | `'1号车位'`（未变） | `qk_spot_search` | `obs-0014` |
| A7 输入 | `qk_spot_search_input` | found `[637,184]` 同 bounds，strategy=xpath | `input_text` sent=true | **`'1号车位333'`**（追加，未替换） | `qk_spot_search` | `obs-0017` |
| A8 清空 | `qk_spot_search_input --clear` | — | **未派发** | `'1号车位333'`（未变） | `qk_spot_search` | `obs-0018` |

- 两个目标在 #120 的新 xpath 下**都命中**（`strategy_kind=xpath`，point 一致），说明 `SearchPoiPage//Search[@id=search_input]/SearchField[@id=__SearchField__search_input]` 在本 build 可定位；输入前后与清空后 `page.state` 恒为 `qk_spot_search`。
- **清空路径被拒（首次原因）**：`action_status=input_required`、`outcome=blocked`、`dispatched=false`、`attempts[0].status=input_required`、`reason="所选输入缺值或引用未解析；保留当前页面，补齐外部输入后重新观察重入"`、`action_required="请补齐外部输入 None，然后重新运行并观察当前页面；不要从历史步骤号续发"`、`attribution=environment`。两个目标同一表现，`--clear` 与 `--text ""` 同样被拒 → **实际空值未观察到**，因此不宣称"已清空"。
- 追加而非替换的连带事实：第二次输入后输入框是 `'1号车位333'`（脏查询），**不是** `'333'`。会话结束时用 `stop_app` 复位（`pidof` 空）。
- 未点「路线」，未碰车控。

## 2. #107 首页到达路径复验

| 设备 | 路径 | 落点 | page.state | 「搜索车位号设为…」 | obs |
| --- | --- | --- | --- | --- | --- |
| A 6HQ/2681 | 爱车 → `qk_ads`(vpd_entry) | `qk_vpd_home`（由度金闽园 共 695 个） | `qk_vpd_home` | **`设为目的地` 自然出现** | `obs-0006` |
| B 5MT/金桥 | 社区 → `qk_tab_car` → `qk_ads`(vpd_entry) | **入口页**（金桥出口加工区 共 742 个 + 搜索 + 我的位置 + 开启智慧停车缴费 + 停车区域/L1/B1） | `unknown` | **未观察**（入口页无该文案） | `obs-0009`（会话 s120m） |

- 设备 B 后续无可控目标：对入口页执行 `tap qk_search_entry --side-effect navigation` 得 `target_unresolved` / `locate=not_found`（其 xpath 要求 `搜索车位号设为起点|目的地|输入车位号设为目的地`，入口页都没有），**未派发、未点击**。→ 金桥入口页 → 首页这一跳仍无受控路径，记为阻塞。
- 「设为起点」本轮**两机都未观察**；设备 A 观察到的是「设为目的地」。

## 3. 证据

- `cli/*.json`：本轮 22 条命令的**完整 stdout 原文**（含 `session/seq/step_id/result.attempts[].locate|dispatch|policy`、`action_status`、`dispatched`、`budget`）。
- `trees/*.json`：对应 obs 的 `tree.json` 逐字节副本（7 份）。
- 截图 7 张只登记 sha256/字节（`MANIFEST.json`），原件留执行机。
- 恢复/清理：设备 A `stop_app`（obs-0024 前一步）、设备 B `stop_app`（`m08`）均 passed，两机 `pidof com.yinwang.qiankunapp.hm` 为空。
