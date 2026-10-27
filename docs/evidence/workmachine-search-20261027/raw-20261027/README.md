# session-s293 五次成功输入的原记录 + session-s118 新实测（2026-10-27）

对应 issue #118。本目录只放**原样复制**的文件，不解包、不改写、不重排。

## 一个先说清的缺口：完整 CLI 返回没有

`python -m vpdtest act ...` 的 **stdout JSON 没有落盘**（执行器只写 `steps.jsonl`、`events.jsonl` 与 `obs/`），所以「完整 CLI 返回」原件**在这次会话里不存在**，无法原样提供。已交付的是执行器实际持久化的三份原始记录：

| 原件 | 内容 | 与 CLI 返回的关系 |
| --- | --- | --- |
| `session-s293/steps.jsonl` | 每步一条：`seq/ts/step/result/input_value`；`result` 内含 `attempts[]`（`locate`、`dispatch`、`policy`）、`recoveries`、`adapted`、`outcome`、`final_obs` | 与 CLI 的 `result` 字段同源 |
| `session-s293/events.jsonl` | 事件流：`act_request`（含 `request`，即 `select/side_effect/allow_input/text` 实参）、`attempt`、`dispatch`、`revalidate`、`clear_text`、`act_result`（`outcome/effect_unknown/dispatched/obs_before/obs_after`） | 与 CLI 顶层 `action_status`/`dispatched` 同源 |
| `session-s293/trees/*.json`、`session-s118/trees/*.json` | 每次 observe 的 `obs/<id>/tree.json` **逐字节副本**（未 gzip、未改写） | CLI 不产出，属 obs 证据 |

**不做的事**：不把 `steps.jsonl` + `events.jsonl` 拼装成一份「完整 CLI 返回」冒充原件；缺的就是缺。

## 五次成功输入 → obs 映射（session-s293，5MT0225B05000904 / 金桥出口加工区）

| seq | step_id | 实际查询 | 结果 | obs_before | obs_after | 结果 obs | 本目录文件 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 14 | `A14` | `1号车位` | passed | `obs-0042` | `obs-0045` | `obs-0046` | `trees/obs-0042-tree.json`、`trees/obs-0045-tree.json`（与 obs-0046 同 sha） |
| 15 | `A15` | `I1` | passed | `obs-0047` | `obs-0050` | `obs-0051` | `trees/obs-0050-tree.json`（与 obs-0051 同 sha） |
| 16 | `A16` | `1号停车点` | passed | `obs-0052` | `obs-0055` | `obs-0056` | `trees/obs-0055-tree.json`（与 obs-0056 同 sha） |
| 17 | `A17` | `333` | passed | `obs-0057` | `obs-0060` | `obs-0061` | `trees/obs-0060-tree.json`（与 obs-0061 同 sha） |
| 18 | `A18` | `ZZZ999`（空结果） | passed | `obs-0062` | `obs-0065` | `obs-0066` | `trees/obs-0065-tree.json`（与 obs-0066 同 sha） |

- 五次都走同一声明：`select=id=__SearchField__search_input`、`side_effect=navigation`、`allow_input=true`（见 `events.jsonl` 的 `act_request.request`）。
- `obs_after` 与「结果 obs」是同一页面的两次 observe，AA 树 sha256 相同（例：`obs-0045` 与 `obs-0046` 树同 sha），所以只存一份树。
- 每份文件的 sha256 与原始路径见 `MANIFEST.json`；**截图只登记指纹、不入库**（9 张：结果页 5 张 + 本轮 s118 页面 4 张，原件留执行机），需要时按指纹核对。

## session-s118 新实测（本轮，6HQ0226505000341 / 由度金闽园 / 赛力斯M9 F2A=2681）

| seq | 动作 | 结果 obs | 说明 |
| --- | --- | --- | --- |
| 1 | `launch` | `obs-0004` | 系统「USB 连接方式」弹窗（单选+确定），AA 树 `nodes=1` |
| 2 | `back`（受控关闭弹窗） | `obs-0007` | 爱车页，当时车=启境GX7（`qk_car_tab`） |
| 3 | `tap qk_ads --side-effect vpd_entry --authorize vpd_entry` | `obs-0010` | 落 ADS **降级变体**（`收藏及常去` 空、遥控泊车辅助），树内 `泊车代驾`/`搜索车位号` **0 命中** |
| 4 | `stop_app` | `obs-0012` | 清理；随后用户现场把车切到 2681（切换不在执行器记录内） |
| 5 | `launch` | `obs-0015`/`obs-0016` | `page.state=qk_vpd_home`，`sig-43330d928b`，`由度金闽园 泊车代驾 共 695 个`，**`搜索车位号设为目的地`** |
| 6 | `tap qk_tab_car` | `obs-0019` | 爱车页显示 **`赛力斯M9 F2A`**（=2681） |
| 7 | `tap qk_ads --side-effect vpd_entry --authorize vpd_entry` | `obs-0022` | 仍 `qk_vpd_home`、同签名，**受控路径 爱车→ADS→首页 复现成立** |
| 8 | `stop_app` | `obs-0024` | `pidof com.yinwang.qiankunapp.hm` 为空 |

`搜索车位号设为起点`：本机/本园区**未观察**（金桥 5MT 历史会话出现的是「起点」）。
