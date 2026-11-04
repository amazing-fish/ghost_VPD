# 知识退役契约的独立测试

Refs #45。七个通用测试函数、20个参数化场景使用主干已有的 `verdict_ws`，不依赖生产知识的当前状态或生产用例存在。
涉及依赖指纹和晋级的候选用例从合成 `NAV_CASE` 构造；退役前先验证用例完整，退役后明确因用例不完整拒绝晋级，
防止其它无关的拒绝原因掩盖本来要检查的知识失效。

`tests/test_knowledge_retirement_isolation.py` 在独立临时Skill副本做两种变异：

- `removed`：复制时不带生产用例目录，并清空副本知识。
- `ineligible`：把副本生产知识改为退役观察条目并限定到其它APP。

子进程显式选择七个通用节点，临时目录位于该测试自己的 `tmp_path`，不递归运行隔离检查本身。
`test_issue33_retirement_keeps_history_and_debug_scope` 仍用生产夹具，原件保留并由外层完整测试执行。
它验证真实退役历史；生产语料变化时应单独评估，不移入变异子批次消除失败。

本项直接基于主干已有夹具，和PR #138的目标驱动契约隔离没有代码依赖，不修改其测试文件或说明。
二者独立验证各自的通用测试；没有另建判定、执行或知识资格引擎。运行时、生产知识/用例、Policy和人审记录保持不变。

在 `vpd_skill` 目录复现：

```powershell
python -m pytest tests/test_knowledge_retirement.py tests/test_knowledge_retirement_isolation.py -q
```

确认、退役和晋级仅发生在测试临时目录；全部是E1离线验证，不产生业务验收、资产批准或正式覆盖。
