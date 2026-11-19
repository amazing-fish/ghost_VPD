# 运行车位收藏名称子用例

`TC-QK-230-SPOT-NAME` 是独立子用例。它输入一个本轮名称，严格清理输入框，保留两份搜索结果观测；收藏身份由核验人对照独立来源判定。它没有路点步骤，不测试按编号搜索，也没有继承 TC-QK-230 的历史。[原需求](https://github.com/amazing-fish/ghost_VPD/issues/106#issuecomment-6431475672) · [名称搜索范围裁决](https://github.com/amazing-fish/ghost_VPD/issues/80#issuecomment-6381962059)

## 工作机执行

1. 在本轮账号、车辆、园区选择任一已有的收藏车位。打开左侧卡片同一 `GridItem` 中的 `⋮` 信息面板，先保存卡片和面板的会话、观测 ID、时间、完整名称、车位类型、楼层和区分字段，再返回 VPD 首页。宽屏已见的入口是与名称同属一张卡片的控件；现场用整窗树和截图确认归属，避免按旧坐标查找。面板独立识别不同车位可用不同字段，编号是可能的见证。可选标签按钮的文字本身不能证明标签已选中。[当前版本工作机实测](https://github.com/amazing-fish/ghost_VPD/issues/118#issuecomment-6976988165)
2. 有搜索前取得的名称即可运行。若只有首页名称线索，记录来源与缺项，照常采名称行，收藏身份待判；没有查询词时不从结果反填。用最新 PR 代码，在 `vpd_skill` 中将此次实际读到的名称传给标准入口。设备参数使用 `hdc list targets` 所示序列号。

```powershell
$deviceSerial = Read-Host '输入 hdc 设备序列号'
$spotName = Read-Host '输入本轮独立读回的完整车位名'
.\vpdtest.ps1 run TC-QK-230-SPOT-NAME --device "hdc:$deviceSerial" --data "fav_spot=$spotName"
```

3. 若首页准备步骤实际重新启动了 APP，先对照两侧账号、车辆和园区；上下文无法对应时保留身份待判。打开新 `runs/<run_id>/run.json`，按 `steps` 找到 S4 与 S5 各自的 `final_obs`，用各自的 `observation.json`、所指结果树和截图复核两次取样。CLI 摘要不含所有断言证据。分别核对两帧的实际查询、完整标题及同行身份字段。定位器只能统计本帧已挂载、符合行结构且未显式隐藏的同名候选。两帧各见一条同名行，也可能先后显示两个对象。检查当前树与截图中的其他类型 POI、裁剪和嵌套行。
4. 只提交文字 issue，按下表逐项填写。原树与原图留工作机。无需再向用户确认这个查询该不该搜。

| 需记录 | 内容 |
| --- | --- |
| 同轮来源 | 实际 PR SHA、设备形态、APP 版本、账号/车辆/园区是否一致、来源会话和卡片/面板 obs、采集时间、车位类型、名称、楼层、区分标识及其字段位置。 |
| 本次运行 | 新 run ID、两个结果 obs 及时间、实际输入、各帧完整标题及其同行字段、相邻/同名候选、画面是否稳定、清理结果。 |
| 指定收藏身份 | 本轮命中 / 待判，核验人及原因。逐帧写明能绑定到搜索前同一收藏的同行区分字段。只有名称、楼层或行数，实体标识冲突、疑似邻行、未稳定、键盘遮挡候选、原件缺失或同名歧义时，记待判。 |
| 结果列表覆盖范围 | 分别记录两帧的键盘状态、可见行数及观察范围。默认填“当前窗口，列表边界未核实”。只有结论依赖排除其余同名项时，才补收键盘、从列表起点逐段检查至末尾，并记边界观测。即使遍历至末尾，覆盖范围也只限本次查询在界面提供的结果。 |

若来源与两帧均有可区分该收藏的同一身份字段，且角色、上下文和归属一致，可记录本轮命中，同时把列表范围记为当前窗口。已有运行先用保留的原件补这两项；无法补证的项记待判或范围未知，无需为改填回执重复同次搜索。[键盘与同名候选的历史观测](https://github.com/amazing-fish/ghost_VPD/issues/118#issuecomment-6683093305)

自动检查提供页面、输入与行的六条观测线索，核验人对照的是两侧原件。已校对的编号须属于同一行中的编号字段；若同一园区、同类型仍有多个候选，或来源晚于搜索、换车换园区、计时或原件无法对应，身份待判。行内可选文字裁剪会使严格行线索无法判断；可在原件上分别辨认标题与可见字段，不能把缺项记成字段必显缺陷。

必需的 `favorite-identity` 断言在正常取证后返回 `pending`。参数被记录为 `run_arguments / measured=false`，人工结论另附上述证据与 run ID，不能改写 run.json。截图采集报错、设备异常和动作阻塞保留其原归因。这类待判运行不能计入确定性晋级重放。后续若要形成可自动晋级的版本，需先在独立收藏源和结果行之间建立可验证的唯一身份绑定，再复审新定义并重新运行。[当前资产机制](asset-readiness.md) · [路点与字段要求](https://github.com/amazing-fish/ghost_VPD/issues/108)

## 首验后的候选登记

[首验回执](https://github.com/amazing-fish/ghost_VPD/issues/118#issuecomment-6996637734)记录了核验人、来源会话、两侧观测及实际运行 ID。工作机在 `e5d37b1` 上通过 12 项 pytest，人工确认当轮命中，范围为当前窗口。原 `run.json` 的身份和产品结论是 `pending`。对照原件时须连同会话或 run ID 使用局部 obs 编号。

在 `vpd_skill` 目录、当前 PR 的独立工作副本运行以下命令。登记候选只使用当前定义与依赖，整个过程无需设备。

```powershell
python -m vpdtest --workspace ./data case readiness TC-QK-230-SPOT-NAME --to candidate
```

若已是 `candidate`，直接查下一阶段。若为 `draft`，核对 `incomplete=false`、该资产 `issues=[]`、`review.state=not_required`、`promotion.status=eligible` 后执行第一条。第二条应显示 `replay_verified` 仍被阻断。

```powershell
python -m vpdtest --workspace ./data case promote TC-QK-230-SPOT-NAME --to candidate
python -m vpdtest --workspace ./data case readiness TC-QK-230-SPOT-NAME --to replay_verified
```

前后 `review.current` 的用例 ID、内容、依赖、审核、策略和上下文指纹应一致。资产差异只有状态与本次候选历史，事件由 CLI 生成。文字回执保留实际 SHA、指纹、该事件和下一阶段受阻原因。此前人工结论继续查首验回执；候选登记不会追加重放，也不会改写原运行。[就绪度与晋级字段](asset-readiness.md)
