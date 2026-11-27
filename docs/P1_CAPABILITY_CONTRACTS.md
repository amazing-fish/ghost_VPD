# P1 原子能力契约拆解（基于 `capability_runtime.py`）

基线：`main@f182893e` + P0 `capability_runtime.py` / `Executor.run_capability` 接线。  
目标：将 `qiankun_hm/flows.json` 中高频业务操作流（搜索、收藏、算路）与 `motor_hm` 登录（Issue `#247`）收敛为基于 `CapabilityRuntime` 的标准化 L2/L3 原子能力，保持统一动作预算、策略门禁、挑战页保现场与业务归因隔离。

---

## 0. 公共运行时契约（已落地于 `vpd_skill/vpdtest/capability_runtime.py`）

所有 P1 能力必须复用 `CapabilityRuntime.open(..., _ctx=...)`，禁止另起第二套轮询、解锁或弹窗关闭循环：

- **预算共享**：支持传入 `_ctx: RunContext`，在 `execute.py` 前置/步骤调用或复合链式调用时共享同一 `max_actions_per_run` 与 `RunStore`。
- **统一拦截**：每轮 `observe_fast`（`screenshot=False`）后经 `classify` 检查 `safe_code` / `sms_verify`（先于超时判断），命中即原屏冻结并返回 `action_required=True`；reviewed 良性弹窗受预算与超时约束关闭。
- **失败归因隔离**：原子能力仅返回语义状态（`ready` / `switched` / `logged_in` / `blocked` / `indeterminate` 等），绝不直接判定业务 `PASS/FAIL`。接入 `Executor.run_capability` 后，前置/能力失败一律归因为 `blocked` 或 `error`，保持 `product_verdict == "pending"`、`product_failed == False`。

---

## 1. `vpdtest search-spot`（来源：`search_and_plan` 搜索段 + `TC-QK-230-SPOT-NAME` 同轮读回）

| 字段 | 契约定义 |
|---|---|
| **对应能力 ID** | `search_spot`（CLI：`vpdtest search-spot --app qiankun_hm`） |
| **取代/收编流** | `qiankun_hm/flows.json#search_and_plan` 前 2 步（进入搜索页 + 输入关键词并定位结果） |
| **授权作用域** | 仅依赖 `vpd_entry`（`policy.json` `task_side_effects`，随任务自动许可；不触发 `route_planning` 点击「去这里」） |
| **输入参数** | `query: str`（非空，不可含空白字符）、`mode: "name" \| "code"`（默认 `"name"`）、`expect_park: Optional[str]`、`vin_tail: Optional[str]`、`timeout_s: float = 15.0`（范围 `(0, 60]`） |
| **前置收敛** | 可选先调 `enter_vpd_app(..., _ctx=rt.ctx)` 确保处于可用 `qk_vpd_home` |
| **核心步骤** | 1) 点击 `qk_search_entry`（备选 `qk_spot_input`）进入 `qk_spot_search`；2) 清空 `qk_search_input` 并复核空值后输入 `query`；3) `rt.poll_until` 轮询结果区或空态/加载提示 |
| **输出字段** | `status`: `found` \| `empty_result` \| `ambiguous` \| `vpd_required` \| `safe_code_required` \| `sms_required` \| `blocked` \| `timeout` \| `indeterminate`<br>`park_name`: 同园区结果组标题（`qk_search_same_park_result_header`）<br>`matched_row`: 同行读回 `{name, floor, code, has_route_entry}` |
| **依赖 Targets 与校准缺口** | `qk_search_entry`（`unverified`）、`qk_search_input`（`unverified`）、`qk_search_same_park_result_header`（`unverified`）、`qk_search_name_result_row`（`unverified`）。<br>**风险备注**：Issue `#109` 已记录 VPD 空搜索结果无「未找到」空态（结果区整块空白），`empty_result` 须结合稳态窗口判定而非猜测文案；`2.0.4.106` 已验证名称行可用（Issue `#308`）。 |

---

## 2. `vpdtest manage-favorite`（来源：`favorite_random_spot` / `unfavorite`）

| 字段 | 契约定义 |
|---|---|
| **对应能力 ID** | `manage_favorite`（CLI：`vpdtest manage-favorite --app qiankun_hm --op <...>`） |
| **取代/收编流** | `qiankun_hm/flows.json#favorite_random_spot`、`#unfavorite` |
| **授权作用域** | **强制要求 `--authorize user_data`**（`policy.json` `gated_side_effects`）。无授权时零动作返回 `authorization_required`。 |
| **输入参数** | `op: "add" \| "rename" \| "tag" \| "remove"`、`spot_name: Optional[str]`、`new_name: Optional[str]`、`tag: Optional[str]`、`timeout_s: float = 20.0`（范围 `(0, 60]`） |
| **清理与回滚契约** | 任何被用例消费的写入型 `manage_favorite`（`add`/`rename`/`tag`）必须配合用例 `cleanup`（`after_dispatch` 归属校验 + `op="remove"` 恢复），确保测试账号收藏列表（上限 5 个车位，Issue `#142`）不污染。 |
| **输出字段** | `status`: `done` \| `authorization_required` \| `favorite_limit_reached` \| `not_found` \| `safe_code_required` \| `sms_required` \| `blocked` \| `timeout` \| `indeterminate`<br>`spot_name` / `applied_tag` / `cleanup_required: bool` |
| **上线门禁（硬阻断）** | 当前 `targets.json` 中 `qk_spot_fav`、`qk_spot_rename`、`qk_rename_input`、`qk_spot_tag`、`qk_tag_option`、`qk_fav_item`、`qk_spot_unfav` 全部为 **`status=hypothesis`**（未真机校准），且收藏上限提示「最多可收藏5个车位」只存在于画面层而 AA 树不可读（Issue `#142`）。**在工作机完成目标校准与模板/瞬态提示核验前，严禁将 `manage_favorite` 接入正式回归用例。** |

---

## 3. `vpdtest start-plan`（来源：`plan_default_spot` / `search_and_plan` 算路段）

| 字段 | 契约定义 |
|---|---|
| **对应能力 ID** | `start_plan`（CLI：`vpdtest start-plan --app qiankun_hm`） |
| **取代/收编流** | `qiankun_hm/flows.json#plan_default_spot`、`#search_and_plan` 后半段（点击「去这里」并监控算路结果） |
| **授权作用域** | 依赖 `route_planning`（`policy.json` `task_side_effects`，随任务自动许可；精确动作标签受 `task_action_labels.route_planning` 校验）。**严禁**触发或越界到 `vehicle_motion`（不点击「开始」「一键泊车」等控车按钮）。 |
| **输入参数** | `mode: "default_spot" \| "search_result"`、`query: Optional[str]`（`search_result` 必填，或先串联 `search_spot`）、`vin_tail: Optional[str]`、`timeout_s: float = 120.0`（受 `policy.max_wait_s` / 能力上限 `(0, 180]` 约束） |
| **核心步骤** | 1) `default_spot` 确认处于 `qk_vpd_home` 后点击 `qk_default_spot`；`search_result` 在搜索结果行点击 `qk_spot_route_btn` / `qk_search_go`；2) 调用 `rt.poll_until` 监控 `qk_route_result` 与故障正则（`泊车代驾辅助暂不可用\|路径规划失败\|路线规划失败\|无可达路径\|暂不可用\|规划失败\|异常退出`） |
| **输出字段** | `status`: `planned` \| `planning_fault` \| `vpd_unavailable` \| `safe_code_required` \| `sms_required` \| `blocked` \| `timeout` \| `indeterminate`<br>`fault_text`: 若命中算路失败提示，记录原文以供上层用例做产品断言 |
| **依赖 Targets 与风险** | `qk_default_spot`（`unverified`）、`qk_search_go`（`unverified`）、`qk_spot_route_btn`（`unverified`）。注意算路可能唤醒车辆（`K-QK-006`），能力本身只停留在 `qk_route_result` 终态。 |

---

## 4. `motor_hm login` 迁移契约（Issue `#247`，基于 `MOTOR_LOGIN_PILOT_247.md`）

| 字段 | 契约定义 |
|---|---|
| **问题根因** | 当前 `vpdtest/login.py` 硬编码 `app.id == "qiankun_hm"` 与 `qk_*` 目标/状态名（`classify_qiankun_screen`），无法复用到 `app.huawei.hmos.motor`（`motor_hm`）。 |
| **配置驱动收敛方案** | 在 `capability_runtime.py` 引入数据驱动 `AuthFlowSpec`（由各 App 的 `states.json` / `app.json` 或声明表提供）：<br>- `logged_in_states` + `account_positive_target`（独立正证区，非「登录/注册消失」）<br>- `logged_out_markers` + `entry_targets`（`login_register` / `other_method` / `pwd_login`）<br>- `form_targets`（`account_input` / `password_input` / `manual_btn` / `protocol_unchecked` / `submit_btn`）<br>- `challenge_markers`（`safe_code` / `sms_verify`）<br>由此让 `login_app` 与 `CapabilityRuntime` 通过单一状态机同时驱动 `qiankun_hm` 与 `motor_hm`。 |
| **两阶段行为约束** | 对齐 [MOTOR_LOGIN_PILOT_247.md](validation/MOTOR_LOGIN_PILOT_247.md)：<br>1) **只读探测优先**：已存在独立账号正证时直接 `logged_in (method="already")`，零下发、不读凭据；若遇到其他账号或挑战页立即原地停止交接（不退出/切号）。<br>2) **缺失凭据零下发**：确认需要登录且任一 secret 缺失时在点击登录入口前返回 `missing_secret`（`action_required=True`）。 |
| **真机校准前置清单（未完成前不开放生产 `motor_hm login`）** | 1) NEXT `app.huawei.hmos.motor` 未登录我的页、账号方式选择、密码输入框、手动输入分支与提交控件的真机目标校准（Android 旧 XPath 仅作路径来源，不可标已校准）；<br>2) 目标账号独立正向辨识区（区分另一账号与未登录态）；<br>3) 协议勾选态、验证码/安全码挑战、重复登录互踢策略核对。 |
