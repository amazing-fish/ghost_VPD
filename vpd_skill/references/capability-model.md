# 能力模型：动作 → 原子能力 → 状态/流程 → 用例 → 用例集

| 层 | 职责 | 输入 | 输出 | 落地位置 |
|---|---|---|---|---|
| 动作 | 设备原语，只保证命令下发与执行器返回 | 坐标、按键、bundle | `DeviceResult{sent, ok, error_class}` | `vpdtest/devices/*.py` |
| 原子能力 | 动作 + 定位 + 安全检查 + 执行前后观测 + 后置条件 + 重试/恢复 | 步骤定义（`action/target/expect_state/timeout_s/retry`） | 步骤结果（attempts、recoveries、outcome） | `vpdtest/execute.py` |
| 状态/流程 | 已知页面的签名与到达方式（recipe），供前置条件、重置与探索复用 | `states.json` | `ensure_state` 结果 | `data/apps/<app>/states.json` |
| 功能流程 | 带参数的原子能力序列（登录、切车、进入 VPD、算路、收藏…），每个子步骤仍走完整的观测/定位/策略/后置条件 | `flows.json` + 调用方 `params` | 子步骤结果与最后一次读数 | `data/apps/<app>/flows.json` |
| 用例 | 目的、前置、数据、步骤、断言（关联判据）、清理、来源 | `data/cases/*.json` | `run.json` + 报告 | `vpdtest/runner.py` |
| 用例集 | 回归批量执行；经验验证的覆盖面 | `data/suites/*.json` | 多条运行结果 | `suite` / `experience validate` |

**分层说明**：recipe（如何到达某状态）与功能流程分开。recipe 没有参数，只用于前置条件、重置与探索回到来源状态；功能流程带参数（`${param:x}`，密钥参数只能进 `secret` 输入框），在多个用例里复用同一段业务操作。流程本身不做判定：判定仍只在用例断言里，并引用知识条目。

## 贯穿示例：TC-MOTOR-HM-001「我的页进入设置并返回」

1. **用例**（`data/cases/TC-MOTOR-HM-001.json`）来自文字用例 `data/knowledge/text_cases/TC-MOTOR-HM-001.md`，
   断言 A1–A3 关联知识 K-MOT-001/002（来源：原工程 `motor_config.py`、`vpd.py` 的具体行）。
2. **状态/流程**：S1 `ensure_state car_tab` 使用 recipe：`stop_app` → `launch`（期望 `@in_app`）→ `tap tab_car`（期望 `car_tab`）。
3. **原子能力**：S2 `tap tab_my`：执行前观测 → 按 `targets.json` 策略定位（`text=我的`）→ policy 检查
   （"我的"不命中禁止文本）→ 下发 `uitest uiInput click x y` → 执行后观测，状态签名 `my_tab`
   （"我的"为选中态）→ `post.met=true`。
4. **动作**：`HdcDevice.tap` 返回 `sent=true, ok=true`；这只说明命令成功，业务状态由第 3 步确认。
5. **判定**：A1 `node_selected tab_my` 在 S2 的最终观测上成立；若 K-MOT-001 未确认 → 待确认，确认后 → 通过。
6. **用例集**：`motor_hm_smoke` 包含 001–003，用于验证 `tab_my` 定位经验是否在其他用例上也成立。

## 第二个示例：TC-QK-101「登录→切车→进入 VPD→默认车位算路，有故障时不能激活」

1. **用例**：步骤只有 `ensure_state` 与 4 个 `flow`（`login_password`、`switch_vehicle`、`enter_vpd`、`plan_default_spot`），账号/密码写成 `{"secret": "QIANKUN_PHONE"}` 等；申请 `account_state`、`vehicle_selection`、`vpd_entry`、`route_planning` 授权。
2. **功能流程**：`login_password` 展开为 12 个原子能力（点头像、选密码登录、输入账号、按需点「手动输入」、输入密码、勾选协议、登录、读账号区）；每个子步骤的选择器原样引用 `mhero_config.py` 并附行号。
3. **原子能力**：`plan_default_spot` 的第 2 步是 `monitor`：等到算路结果页，同时记录「路线规划中」与故障提示的出现时间。
4. **判定**：A1 `target_enabled qk_start expected=false`，按原工程规则「Button opacity=1 才可用」读 `opacity`；判据 K-QK-USR-002 / K-QK-002 未确认 → 待确认，`observed_holds` 表示观测本身成立。
5. **互补用例**：TC-QK-103 在同一位置用 `tap qk_start require_enabled`：可用才点，不可用判阻塞，不会误触发运动。

## 操作选路与产品断言的边界（#56）

flow/state 只提供操作路径；子步骤 `when` 可按当前独立条件自动选路，未触发记录 skipped，未知保留 pending。
用例的 `guard` 与规则 `condition_requirements` 另行决定产品断言适用性，不能把 flow 到终态视为产品通过。
条件定义、证据范围及生命周期见 [contracts](contracts.md#断言前置与操作选支56-第二切片)。
211/212 的来源和真实登录路径迁移仍归 #31，本轮仅提供公共能力。

## 量产登录复用（#31）

TC-QK-223通过ensure_state qk_target_account_ready复用当前登录能力。211/212只作有来源的操作经验；旧login_password的debug证伪不变。互斥条件复合能力允许未选中跳过，选中后内部必需失败/未知必须传播；末尾显式验证目标账户终态。条件input缺值在定位/清空/输入前交接，短信页保留；补值后重新观察重入。来源、scope与审核见[试点说明](../../docs/LOGIN_REUSE_PILOT.md)。登录完成不等于VPD产品断言通过。
