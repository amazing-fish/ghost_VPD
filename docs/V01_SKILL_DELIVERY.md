# 可控范围初版 Skill v0.1 交付清单与边界说明

- **锚定 `main HEAD`**：[`ee9b8818`](https://github.com/amazing-fish/ghost_VPD/commit/ee9b88186ec8b7e456c1b1d5200174a92afe290e)（`ee9b88186ec8b7e456c1b1d5200174a92afe290e`）
- **决策依据**：维护者 2026-11-23 `DEC-V01-SHIP` / `GATE-V01-PACKAGE` / `DEC-299-CLOSE`
- **主干基线巡检**：
  - `python -m vpdtest case readiness`：**43 / 43**（`analysis_errors=0`，`inspection_errors=0`，`approved=2`：`TC-HM-S01` 与 `TC-MOTOR-HM-002`）
  - `python -m pytest -q tests`：**4905 passed, 28 skipped in 1330.42s**（0 failures）

---

## 1. v0.1 已就绪能力与安全护栏（可控范围）

1. **设备解锁与前置唤醒**：自动观测锁屏/亮屏状态并执行受控唤醒、上滑与 PIN 解锁，失败即按 `unlock-guard` 停止，不盲试。
2. **良性弹窗与 APP 内中断清除**：
   - `UiTree._content_box`（[PR #302](https://github.com/amazing-fish/ghost_VPD/pull/302)）：模态框内框识别，保持透明遮罩拦点保护直到弹窗关闭；
   - `vpd_skill/data/apps/qiankun_hm/states.json` 注册 `qk_digital_key_invite`（仅点「取消」，绝不触碰「开通」），保持全局 `vpd_skill/data/system_dialogs.json` 不变，从而保护 `motor_hm` 已批准资产的 `context_hash` 不失效。
3. **输入法全屏窗口误遮挡防护**：
   - `UiTree._ime_keyboard_box`（[PR #303](https://github.com/amazing-fish/ghost_VPD/pull/303)）：裁剪系统输入法窗口上报的全屏 bounds 为实际键盘区域，放行上方输入框点击与输入，同时保留系统非输入法浮窗阻断与隐藏子树过滤。
4. **防误触与禁碰控车护栏**：
   - `vpd_skill/data/policy.json` 限制 APP 白名单、禁止按钮文本（如未授信/控车动作）与副作用分级（`task` / `gated` / `supervised` / 禁止）；
   - 「只到算路结果、绝不点『开始』控车」，`vehicle_motion` / `vehicle_control` 必须显式 `--authorize` + `--supervisor` + 用例 `safety`。

---

## 2. 已验证用例与已确认判据（v0.1 核心链路）

| 类别 | 资产 / 判据 ID | 当前状态与说明 |
| --- | --- | --- |
| `motor_hm` 回归用例 | `TC-HM-S01`、`TC-MOTOR-HM-002` | `readiness` 均保持 `approved`（同会话账户接力，不改全局弹窗 hash） |
| `qiankun_hm` 准入链路 | `TC-QK-215` | 工作机已实现真机 2 连过（`A1/A2 verified=true`）；[PR #304](https://github.com/amazing-fish/ghost_VPD/pull/304) 已按方案 2 移除不可测 `vpd_task_state`，关闭 [Issue #300](https://github.com/amazing-fish/ghost_VPD/issues/300) |
| 已确认 `K-QK-*` 判据 | `K-QK-013`、`K-QK-TC-004`、`K-QK-015`、`K-QK-016`、`K-QK-019`、`K-QK-018`、`K-QK-TC-059` | 经 [PR #297](https://github.com/amazing-fish/ghost_VPD/pull/297) 完成维护者签字 `confirmed` 落库，`conflicts() == {}` |

---

## 3. 安装与快速使用（Windows PowerShell）

```powershell
cd <仓库根目录>\vpd_skill
.\install.ps1

# 1) 只读设备自检（真机需先确认 hdc list targets 可见）
.\vpdtest.ps1 doctor --device hdc:<serial>

# 2) 全量资产就绪度核验
.\vpdtest.ps1 case readiness

# 3) 运行已批准回归用例 / TC-QK-215（不传 --env vpd_task_state=none）
.\vpdtest.ps1 run TC-MOTOR-HM-002 --device hdc:<serial>
.\vpdtest.ps1 run TC-HM-S01 --device hdc:<serial>
.\vpdtest.ps1 run TC-QK-215 --device hdc:<serial> --env account_state=logged_in

# 4) 无真机离线回放演示
.\.venv\Scripts\python.exe scripts\demo_offline.py
```

---

## 4. v0.1 正式签发前最后 2 项真机冒烟门禁

在最新 `main`（[`ee9b8818`](https://github.com/amazing-fish/ghost_VPD/commit/ee9b88186ec8b7e456c1b1d5200174a92afe290e)）上执行以下 2 项实机冒烟并纯文本回传 `run_id` 与结果：

1. **`#300` 裸跑验证**：`.\vpdtest.ps1 run TC-QK-215 --device hdc:<serial> --env account_state=logged_in`（不加 `--env vpd_task_state=none`），确认无 `PRECONDITION_FAILED` 且 `A1/A2` 通过。
2. **`#299` 输入链路验证**（`DEC-299-CLOSE`）：在实机触发密码保险箱与输入法弹出后，验证点击「手动输入」及在登录输入框 / VPD 搜索框注入文本顺畅通过，以确认关闭 [Issue #299](https://github.com/amazing-fish/ghost_VPD/issues/299)。

---

## 5. 开放 Issue 分档路线图（22 条开放 Issue）

- **v0.1 收口冒烟（1 项）**：[#299](https://github.com/amazing-fish/ghost_VPD/issues/299)（核心裁剪 PR #303 已合入，待真机输入冒烟关单）。
- **第二梯队 · v0.1 紧随业务闭环（6 项）**：[#135](https://github.com/amazing-fish/ghost_VPD/issues/135)、[#136](https://github.com/amazing-fish/ghost_VPD/issues/136)（已签 7 判据逐例重绑与补采）、[#111](https://github.com/amazing-fish/ghost_VPD/issues/111)（先读收藏再按名搜）、[#257](https://github.com/amazing-fish/ghost_VPD/issues/257)（device_form/app_version 适配）、[#288](https://github.com/amazing-fish/ghost_VPD/issues/288)（2.0.4.106 搜索结果行结构适配）、[#237](https://github.com/amazing-fish/ghost_VPD/issues/237)（去设备身份匿名别名）。
- **第三梯队 · v0.2+ 长期能力与特种场景（15 项）**：[#45](https://github.com/amazing-fish/ghost_VPD/issues/45)、[#54](https://github.com/amazing-fish/ghost_VPD/issues/54)、[#56](https://github.com/amazing-fish/ghost_VPD/issues/56)、[#58](https://github.com/amazing-fish/ghost_VPD/issues/58)、[#64](https://github.com/amazing-fish/ghost_VPD/issues/64)、[#65](https://github.com/amazing-fish/ghost_VPD/issues/65)、[#80](https://github.com/amazing-fish/ghost_VPD/issues/80)、[#82](https://github.com/amazing-fish/ghost_VPD/issues/82)、[#108](https://github.com/amazing-fish/ghost_VPD/issues/108)、[#109](https://github.com/amazing-fish/ghost_VPD/issues/109)、[#118](https://github.com/amazing-fish/ghost_VPD/issues/118)、[#134](https://github.com/amazing-fish/ghost_VPD/issues/134)、[#142](https://github.com/amazing-fish/ghost_VPD/issues/142)、[#247](https://github.com/amazing-fish/ghost_VPD/issues/247)、[#254](https://github.com/amazing-fish/ghost_VPD/issues/254)（覆盖 AVM 自绘表面交互、双指缩放、折叠态右缘、纯画面 Toast 识别、WeLink 通知等）。

---

## 6. 零外部回传安全与脱敏约束

- 真机运行目录 `vpd_skill/runs/` 与个人配置 `vpd_skill/config.local.json` 仅保留在本地执行机。
- **严禁**向 GitHub Issue、PR、Commit 或跨机汇报消息上传或粘贴真实屏幕截图、UI 树 JSON 原文、运行日志压缩包、原始设备序列号或内网 `IP:PORT`。
- 账号密码及锁屏 PIN 必须通过环境变量 `VPD_SECRET_*` 或本地 `config.local.json` 注入，严禁明文写入代码或用例资产。
