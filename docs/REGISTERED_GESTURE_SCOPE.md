# 相对滑动引用已登记容器

同一页面有多个匿名 `Grid` 时，`within=type=Grid` 会保持歧义拒绝。相对滑动现在可引用 `targets.json` 中的容器定义，而不扩展通用定位范围：

```sh
python -m vpdtest act swipe --app motor_hm --session example --device replay:example.json --within target=reviewed_grid --start 0.8,0.5 --end 0.2,0.5 --speed 800
```

上面的名称和设备仅说明命令形状，仓库没有因此新增真实页面目标。对应步骤可保存为 `"within": {"target": "reviewed_grid"}`。引用必须已经登记，明确声明 `navigation`，且全部策略为带非空值的 `text`、`id` 或 `xpath`；模板、像素坐标和内联目标不适用。定义问题在创建会话、打开设备之前拒绝。

执行前重新观察当前树，用现有定位器解析唯一节点；该节点本身必须是当前可用、可见且可滚动的 `Scroll/List/Grid`。不从命中的文字向上猜容器，也不从多个结果中选择第一个。比例坐标从当前可见区域计算，随后仍经过原有整段路径、接收者、禁止节点、未校准表面、前台、锁屏、预算和安全暂停门禁。旧 `id`/`type` 相对范围保持原行为；普通方向滑动与通用 `--select` 不接受这种新引用。

会话固化保留注册 ID。目标定义纳入用例依赖指纹，包括展开的 flow/recipe 和 cleanup；目标仍为 `hypothesis` 时会进入原有资产 lint 检查，不能因增加引用方式而成为已校准资产。本能力不提升、重绑或批准任何生产资产。

验证仅使用合成双 Grid 和变异路径。真实页面目标还需要原始 UI 树、配对截图、当前版本及设备形态证据；工具能力通过不代表工作机现场问题已修复。合成回归：

```sh
python -m pytest vpd_skill/tests/test_relative_scope_target.py vpd_skill/tests/test_relative_swipe.py -q
```
