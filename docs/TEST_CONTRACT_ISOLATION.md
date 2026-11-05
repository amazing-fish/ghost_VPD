# 通用判定测试与生产语料分开验证

Refs #45。通用契约测试应验证执行器的行为，不能把生产知识当前仍存在、已确认或可再次确认为前提。

`tests/test_goal_loop.py` 中九项判定/成熟度测试使用已有 `verdict_ws`：缺步骤、尚未执行的断言、适用范围、
必需清理、失败签名、安全停止、稳定已知失败、混合重放及授权指纹。导航使用 `NAV_CASE`，文案用例使用
`COPY_CASE`，判据全部由 `tests/verdict_fixtures.py` 构造。APP 定位、策略和回放世界仍走既有集成路径；
这不证明真实产品表现，也不使其它测试自动获得隔离性。

`tests/test_error_consumers.py` 的工作区测试同样复用 `verdict_ws` 和 `NAV_CASE`，不再为构造错误运行
摘要读取生产 `TC-MOTOR-HM-001`。CLI 错误退出码、suite 的执行错误证据、重放中的执行/产品投影及
经验与晋级门禁保持原断言。该模块用合成错误摘要验证消费方协议，不替代真实 Runner 异常路径测试。

`tests/test_verdict_isolation.py` 在独立临时 Skill 副本执行两种变异：

- `removed`：清空生产知识并移除生产用例目录。
- `ineligible`：将生产知识改为退役观察、限定到其它 APP。

两种变异均执行这九项测试、完整错误消费模块及既有判定/检索、成熟度契约；测试失败时保留子进程输出。子进程临时目录
位于该测试自己的 `tmp_path`，不依赖机器默认临时目录权限，不递归执行隔离测试本身。

`tests/test_verdict_corpus.py` 有意读取生产资产与生产知识，继续由外层测试运行。生产语料变化导致其失败时，
应重新评估集成期望，不能用通用夹具替换它来消除失败。

在 `vpd_skill` 目录复现相关验证：

```powershell
python -m pytest tests/test_goal_loop.py tests/test_verdict.py tests/test_error_consumers.py tests/test_verdict_isolation.py tests/test_verdict_corpus.py -q
```

这里的确认、已知失败登记和晋级仅发生在测试临时目录。生产知识、生产用例、审核记录和运行时不随这项整理修改。
