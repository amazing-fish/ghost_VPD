# 与 ghost_VPD 原工程的复用边界（基于 main@90545b9 的代码核实）

| 分类 | 内容 | 路径 | 理由 |
|---|---|---|---|
| 直接复用 | Harmony 选择器（文本/xpath） | `app/config/motor_config.py:288-549` | 已在原脚本中使用；以"定位线索 + 来源行号"导入 `targets.json`，不当作产品规则 |
| 直接复用 | 业务路径知识（登录态文案、设置/登出路径、弹窗按钮） | `app/service/vpd/vpd.py:2261-2478` | 作为 unconfirmed 知识与种子用例来源 |
| 直接复用 | hdc 命令词汇、包名与 Ability | `app/utils/hdc_util.py`、`harmony_driver.py:106` | 与真机探测一致（`EntryAbility`） |
| 适配后复用 | XPath 语义 | `app/drivers/harmony/hm_driver_new.py`、hmdriver2 `_xpath._json2xml` | 同样"type 作标签"，原 xpath 可原样使用；改为显式报告空树/解析错误 |
| 适配后复用 | 多尺度模板匹配 | `app/drivers/img_parser/img_parser.py` | 修正：返回中心点而非左上角、返回得分、统一阈值比较、报告多匹配 |
| 适配后复用 | Harmony 设备操作 | `app/drivers/harmony/harmony_driver.py` | 改为返回 `DeviceResult`、不吞异常、有超时、列表传参；dumpLayout 限定 `-b <bundle>` |
| 隔离 | Flask / Huey / MySQL / Redis / 任务调度 | `app/__init__.py`、`app/tasks/`、`app/models/` | `import app.drivers...` 会连带导入 Flask 蓝图、peewee、redis，并由 `config_util` 在导入时执行 adb/hdc；新工具完全不 import `app` 包 |
| 隔离 | VPD 车端流程（进图、算路、巡航、泊车） | `app/service/vpd/vpd.py` 其余部分 | 触发车辆运动，v0.1 无实车授权；保留为后续"车端能力"接口边界 |
| 暂不引入 | Android 驱动 | `app/drivers/android/android_driver.py` | 本机无 Android 设备可验证；接口已预留（`devices/` 新增适配器即可） |
| 暂不引入 | 录屏、小鲁班通知、DB 统计 | `scrcpy_util.py`、`xiaoluban_util.py`、`tables.py` | 与 APP 软件验证闭环无直接关系 |
| 暂不引入 | OCR / 视觉模型服务 | — | 本机未安装 OCR；视觉理解由宿主 Agent 看截图完成，只能产生建议或受约束的单步提议 |

## 核实中发现的问题（原工程未修改，只在新工具中规避）

1. `HarmonyDriver.click` / `AndroidDriver.click` 捕获所有异常只返回 `False`，调用方多数不检查返回值；`get_text` 失败返回 `''`。
2. `elif 'x' and 'y' in kwargs` 实际只判断 `'y' in kwargs`（Python 运算优先级）。
3. `ImgParser._match_targets` 返回匹配左上角并直接用于点击；不返回得分；循环内 `>=` 与最终 `<=` 判定不一致。
4. `AndroidDriver.dump_hierarchy` 没有 `return`，恒为 `None`；hmdriver2 `dump_hierarchy` 解析失败返回 `{}`。
5. `HMDriverNew.xpath_exists` 默认 refresh 后又在 `xpath_get_attrib` 中再次 dump，一次判断两次采集。
6. `AppLoginCar.run` 捕获全部异常只记录日志，`vpd_task_run` 随后继续执行 `main_vpd.run()`：登录失败被当作可继续。
7. `click_pause/continue/quit` 以点击返回值记录"成功"，未确认业务状态（与既有分析文档结论一致）。
8. `_handle_common_popups` 会点击「更新」按钮，自动化过程中可能升级被测 APP。
9. 截图文件名只精确到秒，同一秒内多次截图会覆盖证据。
10. hdc 在失败时退出码仍为 0（真机已验证：锁屏启动失败、错误序列号、bm 查询不存在的包）。
11. 仓库提交了凭据类配置：`app/constant.py` 中的通知 token，`config.json` 中的 access/secret key 与账号字段（未在此处复述值）。建议轮换并移出版本库。
12. `app/assets/imgs/**` 被代码引用但不在仓库中，图像模板定位在当前仓库中无法直接运行。
