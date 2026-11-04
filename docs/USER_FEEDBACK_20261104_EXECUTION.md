# 两项用户反馈：工程交付与工作机执行安排

基线：main `3bc40772c127ce04307fc1ce20659e8446e83a13`。新增[六条手工用例集](test-sets/VPD_USER_FEEDBACK_20261104.json)，
完整步骤见[测试资产](../vpd_skill/data/knowledge/text_cases/VPD_USER_FEEDBACK_20261104.md)。未新增正式覆盖；没有实机运行。

## 已落实与下一步

| 事项 | 维护侧交付 | 工作机下一步与完成标准 |
|---|---|---|
| 搜索＋标签 | 三类可区分收藏、两种操作顺序、独立读回、清理和未决语义入手工用例集 | 在当前账户/园区证明容量与原基线，授权后只新增本轮数据；取得创建/完整收藏/非空标签接收者原件并校准，再固化可执行case。未校准不虚构目标或启用自动suite |
| 短Toast | 复用现有帧流，触发前首帧就绪，最终Dispatcher绑定tap；帧/指纹/时间窗/错误/缺口落盘，禁止自动重发 | 先在非敏感导航检查本机帧源和工具版本，再用实际一次提交校验；回报真实run/obs及manifest。采集窗口长度是预算，不是产品SLA |
| 用户提示与日志 | 用户原则、手机/云端/车端DDI三层检查、断网/延迟/补传/重复场景与判定表已收录 | 提供有权查询的接口/人和独立网络、上电事实；以关联ID及权威终态核日志。权限或事实不足记未验证，不猜无日志 |
| 其它业务决策 | 保留待决与原有裁定 | 后续报告继续附背景、原图或明确缺图、每个选项的测试影响；不批量confirm/approve |

短提示接入的是原有`Executor → Dispatcher → Observer/Verdict`链路，没有第二套点击、上传或业务判定引擎。
现有Console帧源的历史性能不保证本机帧率；变化驱动静止、丢帧和启动错误都记录。
帧时间为主机接收时间，不是设备曝光/显示时间；原图只供语义与前后对照，不能据此宣称提示精确持续时长。
该源使用可选`hmdriver2 1.4.3`非公开Captures接口；缺依赖或首帧不就绪则阻塞提交，不偷偷降为普通慢轮询。
同一设备保留单一执行责任人，不在已有Console帧源/现场动作并发时另外启动提交测试。

## 操作入口

在当前版本`vpd_skill`目录，用新会话先observe，按真实控件核定副作用与授权，再通过受控tap采帧：

```powershell
.\vpdtest.ps1 observe --app qiankun_hm --device $D --session feedback-one
# 下列选择器只是语法示例；先核真实唯一接收者，不从文案猜页面或照抄像素。
.\vpdtest.ps1 act tap --app qiankun_hm --device $D --session feedback-one `
  --select "text=<当前真实提交文字>" --side-effect user_data --authorize user_data --capture-transient 3
```

运行产物：`runs/session-feedback-one/steps.jsonl`及`transient/capture-*/manifest.json`、原始JPEG帧。
语法还支持tap步骤`"capture_transient":{"post_s":3,"ready_timeout_s":5}`；参数0.05..10秒。
首帧未就绪则无触发动作；有触发后的取证故障保留下发回执，不重试提交。
`transient_text_seen`只以本次完整新鲜原树提供肯定文字证据；画面独有提示保留视觉待核，未捕获不变成无提示失败。
不要把短Toast放进普通`--expect-text`后置条件；该条件仍检查普通观测，提示已消失时不能用它否定帧中的出现事实。

## 分阶段验收

1. 家庭机：源码及离线正反例，清楚标记E1；不连接设备或访问实际上传服务。
2. 工作机：同一当前版本工具/帧源校准与最小一次动作；按#118/#80提交完整源件，不直接改维护分支。
3. 维护侧：核原件、输入/实体/请求终态、清理归属及有效后置，把可执行case加入对应自动suite前保留既有人审与产品判据门槛。
4. 有权限的日志检查方：按关联ID给云端与DDI各自权威回执；无权限分支未验证。不得为了DDI自动给车辆上电。

未取得实机结果时，不声称捕获成功率、正式覆盖或业务验收通过。既有review、knowledge及Policy不自动改写。
