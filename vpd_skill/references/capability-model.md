# 能力模型：动作 → 原子能力 → 状态/流程 → 用例 → 用例集

| 层 | 职责 | 输入 | 输出 | 落地位置 |
|---|---|---|---|---|
| 动作 | 设备原语，只保证命令下发与执行器返回 | 坐标、按键、bundle | `DeviceResult{sent, ok, error_class}` | `vpdtest/devices/*.py` |
| 原子能力 | 动作 + 定位 + 安全检查 + 执行前后观测 + 后置条件 + 重试/恢复 | 步骤定义（`action/target/expect_state/timeout_s/retry`） | 步骤结果（attempts、recoveries、outcome） | `vpdtest/execute.py` |
| 状态/流程 | 已知页面的签名与到达方式（recipe），供前置条件、重置与探索复用 | `states.json` | `ensure_state` 结果 | `data/apps/<app>/states.json` |
| 用例 | 目的、前置、数据、步骤、断言（关联判据）、清理、来源 | `data/cases/*.json` | `run.json` + 报告 | `vpdtest/runner.py` |
| 用例集 | 回归批量执行；经验验证的覆盖面 | `data/suites/*.json` | 多条运行结果 | `suite` / `experience validate` |

**合并说明**："功能流程"没有单独建框架层，而是合并进"状态/流程"：v0.1 中流程的可复用部分就是"如何到达某状态"
（recipe），其余业务序列直接写在用例步骤里。单独的流程层只会重复用例的步骤结构，却没有独立的判定职责。

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
