# #236 页签定位：工作机实机复验的独立复核记录

Refs #236, #240。独立复核方与实现方隔离：只读运行证据，未改文件、未连设备、未执行 `case approve`。

## 被复核的基线

- 工作树分支 `merge/pr240-into-main`，合并提交 `be3721f`（最新 main `252bb0e` + PR #240 `c28c404`，无冲突）
- 资产当前定义：`TC-HM-S01` content `515054e86da9` / deps `9c4436c1b914`；`TC-MOTOR-HM-002` content `b4de659323c5` / deps `19d7bdc10603`
- 运行：`run-20261112-152707-e255`、`run-20261112-153128-376e`、`run-20261112-152847-708c`、`run-20261112-152936-0e00`、`run-20261112-154638-1b94`，另补 `run-20261112-152759-f470`

## 结论

有条件建议写入新的独立审核绑定：定位链与断言链在 3.0.3.200／SGT-AL10／sm 直板上闭合。条件为 basis 保留三项排除、如实披露下节证据链缺口、说明 1b94 的布置动作无独立日志留痕。

## 证据链缺口（披露，不改变通过结论）

1. `TC-MOTOR-HM-002` 的 A1 证据包只记「我的 sel=false」，未记「爱车列 sel=true」；同树手工核对 car_tab 上唯一 `selected=true` 的 TabBar 子 Column 为 `[528,2565][792,2754]`。
2. `run-20261112-152847-708c` 的 S1 `launch` `post.expected=None`（零落点校验），故只能说「S1b 起点确为爱车页」，不称整段为「从爱车起跑」。
3. `run-20261112-154638-1b94` 的布置动作（stop_app → launch → tap tab_my）在本工作树没有独立事件日志，独立性由 `obs-0001` 状态+签名与上一运行 `376e` 结束于 `out_of_app` 共同印证。
4. 既存行为：`TC-MOTOR-HM-002` `review.required=false`，stale 时 `informal=false` 仍可运行；`TC-HM-S01`（探索来源）则被拒绝。本轮未改准入规则。
5. 既有经验候选 `XP-20261013-101449-def5`（`prefer_strategy_index=1`）在新定义下语义漂移（index 1 已是窄屏区域兜底），若并入经验库需退役或重写；本次 overlay 仍为 v0，未触发。

## 独立复核原文

见同目录 `236-tab-scope-workmachine-review.original.txt`，其 UTF-8（LF 换行）字节的 SHA-256 为：

```
1C866DD473D887865EB4AA270C3C676F020C76610E66E1BA64BFB626CB217890
```

即 `docs/validation/236-tab-scope-workmachine-review.md#original-review-sha256=1C866DD473D887865EB4AA270C3C676F020C76610E66E1BA64BFB626CB217890`。
