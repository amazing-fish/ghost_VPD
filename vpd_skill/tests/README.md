# 判定测试的数据边界

`test_verdict.py` 测的是判定契约：确定性观测加上适用、已确认的独立判据，才能判通过或失败。
它使用 `verdict_ws`：固定合成用例和 `K-TEST-VERDICT-*` 知识在临时工作区经真实 add/confirm 创建，
不读取生产 knowledge/cases/experience。本地密钥和默认设备配置也不带入该工作区。
崩溃判定固定引用 `K-GEN-001`，夹具为此 ID 单独创建合成不变量，不借用生产来源或确认记录。

APP 描述、定位器、状态、policy、layout、系统弹窗及 replay world 仍使用现有定义；
Runner、Observer、Dispatcher、KnowledgeStore 和 Verdict 均真实执行。
这些依赖变化造成的失败仍应检查，本次隔离不表示一套完全自包含的 APP 模型，更不表示产品验收通过。

`test_verdict_isolation.py` 在临时 Skill 副本里清空知识并删除生产用例，或改变知识状态/类型/scope，
再独立运行判定契约测试，防止重新引入生产判据依赖。子进程运行 `test_verdict.py` 及
`test_explore_promote.py` 的两项成熟度门禁测试，不会递归运行自身。

两项成熟度门禁复用 `verdict_ws` 的合成设置判据；探索、Runner、重放记录和晋级仍走真实路径。
修改断言使旧重放失效的测试，先确认两次运行通过且当前重放依据充分，避免原本就不合格的重放制造假绿。

`test_verdict_corpus.py` 则有意读取生产导航用例和判据，检查当前已确认样例及失去确认后的 pending。
生产语料变化时应核对该集成测试的预期；不得为了通过而自动确认生产知识。
其余沿用 `ws` 的测试仍可能依赖生产数据；本轮只在 #9 首个切片上补齐上述两项成熟度门禁隔离。

在 `vpd_skill` 目录执行：

```powershell
python -m pytest tests/test_verdict.py tests/test_explore_promote.py tests/test_verdict_isolation.py tests/test_verdict_corpus.py -q
```
