# 业务用例库

> 本文件记录在 hza_vpd 数据集上执行过的具体业务需求、对应 SQL、关键发现与结论，供后续复用与对照。新用例追加到末尾，勿覆盖既有记录。

---

## 用例 1：核查 event_id=30305 在安卓 >1.3.08.006 版本中 4099/10003 失败情况

### 业务需求
读取**安卓（Android）** `sdk_version > 1.3.08.006` 的版本中，`event_id = 30305` 的**失败事件**里：
1. 是否**不再报** `failure_reason = 4099`？
2. 是否**出现** `failure_reason = 10003`？

### 关键表与字段语义（实测）
- 数据集 `hza_vpd` 涵盖**两张底层物理表**，均含三端（Android/IOS/harmonyOS），非 IOS-only：
  - `datalake.kpi.dwi_vpd_event_track_to_vpd`：event_id 303xx，`result` 取值 `'success'/'failed'/''`（harmonyOS 偶有 `'fail'`），**失败 = `result='failed'`**。
  - `datalake.kpi.dwi_vpd_event_track_to_adssdk`：event_id 30601~30606，IOS `result='1'` 表失败、`'0'` 表成功；Android/harmonyOS 仍为 `'success'/'failed'`。
- ⚠️ 30305 在 **vpd 表**（不在 adssdk 表）；30601 在 **adssdk 表**（不在 vpd 表）。两表 event_id 范围不重叠，不要混用。
- `os_name` 精确取值：`Android` / `IOS` / `harmonyOS`（首字母大写）。
- `sdk_version` 形如 `1.3.07.137-deepal-pre`，`regexp_replace(sdk_version,'-.*$','')` 去后缀；存在非标 `1.3.08.<时间戳>ads` 构建/测试版本号。

### 版本过滤逻辑
`sdk_version > 1.3.08.006` 因版本号零填充（`1.3.08.NNN` 等位宽）用字符串比较有效，但需修正两点：
- `LIKE '1.%'`：排除 4.x 旁系（4.x 是不同版本线，且数值 4>1 会被误纳入）。
- `NOT LIKE '1.3.7.%'`：修正非标未补零的 `1.3.7.xxx`（语义上 < 1.3.08，但字符串比较 `'7'>'0'` 会误判为大于 `1.3.08.006`）。

### 主 SQL
```sql
SELECT
    CASE
        WHEN sdk_version IS NULL OR TRIM(sdk_version) = '' THEN '无版本号'
        ELSE regexp_replace(sdk_version, '-.*$', '')
    END AS sdk_version_clean,
    CASE
        WHEN regexp_replace(sdk_version, '-.*$', '') LIKE '1.%'
         AND regexp_replace(sdk_version, '-.*$', '') > '1.3.08.006'
         AND regexp_replace(sdk_version, '-.*$', '') NOT LIKE '1.3.7.%'
        THEN 'Y' ELSE 'N'
    END AS is_gt_13008006,
    COUNT(*) AS total_failure,
    SUM(CASE WHEN failure_reason = '4099'  THEN 1 ELSE 0 END) AS reason_4099,
    SUM(CASE WHEN failure_reason = '10003' THEN 1 ELSE 0 END) AS reason_10003,
    SUM(CASE WHEN failure_reason NOT IN ('4099','10003') THEN 1 ELSE 0 END) AS reason_other
FROM datalake.kpi.dwi_vpd_event_track_to_vpd
WHERE os_name = 'Android'
  AND event_id = '30305'
  AND result = 'failed'
GROUP BY 1, 2
ORDER BY sdk_version_clean;
```

### 探针 SQL（定位正确表/字段时使用）
**探针 A**——确认 event_id 分布（发现 adssdk 表只含 306xx）：
```sql
SELECT event_id, COUNT(*) AS cnt
FROM datalake.kpi.dwi_vpd_event_track_to_adssdk
GROUP BY event_id ORDER BY cnt DESC LIMIT 50;
```
**探针 B**——在正确表确认 os_name/result/failure_reason 分布：
```sql
SELECT os_name, result, failure_reason, COUNT(*) AS cnt
FROM datalake.kpi.dwi_vpd_event_track_to_vpd
WHERE event_id = '30305'
GROUP BY os_name, result, failure_reason
ORDER BY cnt DESC LIMIT 100;
```

### 结果与结论（2026-08-20，pre-prod）
| 指标 | >1.3.08.006（Y，15 版本） | ≤1.3.08.006（N，37 版本，基线） |
|---|---|---|
| 失败总数 | 137 | 2061 |
| reason_4099 | **3** | 666 |
| reason_10003 | **0** | 50 |

完整性校验：4099 合计 666+3=669、10003 合计 50+0=50，与独立分布查询（42 行）一致。

- **1) 4099 是否不再报？→ 否（未清零）**。>1.3.08.006 仍报 3 次：正式版 `1.3.08.007`=1、`1.3.08.008`=1，构建版 `1.3.08.01785892287032ads`=1；旧版本重灾 `1.3.07.001`=451、`1.3.07.002`=106、`1.3.07.004`=48、`1.3.08.001`=41。即大幅下降但未完全消除。
- **2) 10003 是否出现？→ 否（未出现）**。>1.3.08.006 版本 10003=0；10003 仅存于旧版本 ≤1.3.08.006（`1.3.08.003`=27、`1.3.08.005`=22、`1.3.05.002`=1）。

> 正式版（匹配 `1.3.NN.NNN`）>006 明细：1.3.08.007(16,4099=1)、1.3.08.008(9,4099=1)、1.3.08.009(7,4099=0)、1.3.08.010(1,4099=0)，10003 均为 0。

### 待办/可追问
- 1.3.08.007/008 各 1 次 4099 量极小，可拉明细（request_id/vin/event_time）确认是否同一边界 case 残留。
- 若需判断 10003 是否在某版本开始出现作为回归信号，可按版本时间线追踪 10003 首现版本。

---

<!-- 新用例在此行下方追加 -->

---

## 用例 2：验证 iOS 30601 自检接口「重复误报」修复（pre-prod）

### 业务需求
生产环境三端 30601（自检接口）存在请求失败、报「车辆信息验证失败」，其中 **iOS 7%**、Next 1%、Android <0.1%。MR 1584 修复 iOS「重复误报」（同一请求被多次失败上报导致失败率虚高）。需在 **pre-prod** 验证修复版中重复误报是否消除。

### 关键表与字段语义（实测，补充用例 1）
- **表选择**：30601 在 `dwi_vpd_event_track_to_adssdk`（非 `dwi_vpd_event_track_to_vpd`，后者只含 303xx）。
- **result 取值（adssdk 表）**：`'1'` = 失败、`'0'` = 成功（⚠️ 与 vpd 表 `'failed'/'success'` 不同，adssdk 表用数字字符串）。
  - Android 在 adssdk 表中 result 取值仍为 `'success'/'failed'`（三端不一致，IOS 用 0/1）。
- **failure_reason 码表**（30306 响应表，用户提供）：
  | 码 | 含义 |
  |---|---|
  | 14 | 自检接口请求失败、4\*\*/5\*\*等接口异常错误 |
  | 15 | 其他原因造成车辆信息验证失败，请重试的场景 |
  | 18 | 自检成功，位置接口响应失败（图外场景） |
  | 2 | 未授权位置 |
  | 3 | token 失效 |
  | 4 | 请前往维修中心完成认证 |
  | 17 | 车主 app 传入的车辆信息为空（vehicleId/vehicleType 等） |
  | 其他 | 1/5/6/7/8/9/10/11/12/13/16 见完整码表 |
- **IOS 30601 失败分布**（pre-prod 实测）：reason 14（1379）、15（398）、18（110）为主，2（32）、3（30）、17（23）、4（17）次之。
- **vin 字段**：adssdk 表中 IOS 30601 失败记录的 vin **普遍为空**，重复检测实际仅靠 request_id（request_id 有值，无空值干扰）。
- **event_time**：格式 `2026-08-15 19:06:51.040`，可用于时间窗口切分（修复前 vs 修复后）。

### 重复误报检测逻辑
1. **定义**：同一 vin + request_id 对 30601 产生多条 result='1' 记录 = 重复误报。修复后每个请求应仅 1 条。
2. **两层聚合**：
   - 内层：`GROUP BY ver, vin, request_id` → `COUNT(*) AS cnt`（每请求出现次数）
   - 外层：`SUM(cnt)` = 总记录数、`COUNT(*)` = 唯一请求数、`SUM(CASE WHEN cnt>1 ...)` = 重复请求数/多余记录数
3. **excess 率** = 多余记录数 / 总记录数 × 100%（衡量重复误报严重程度）。
4. **时间窗口切分**：用 `event_time >= '截止日期'` 将修复前/后数据分离，避免修复前旧数据干扰结论。

### SQL 套件（6 条，按执行顺序）

**SQL-0：探针 — 确认表/字段/分布**
```sql
SELECT os_name, event_id, result, failure_reason, COUNT(*) AS cnt
FROM datalake.kpi.dwi_vpd_event_track_to_adssdk
WHERE event_id = '30601'
GROUP BY os_name, event_id, result, failure_reason
ORDER BY cnt DESC
LIMIT 100
```

**SQL-1：主验证 — 修复版 vs 旧版重复率（全量）**
```sql
SELECT
  CASE
    WHEN ver IN ('1.3.08.104','1.3.08.105','1.3.08.107','1.3.08.108','1.3.08.109','1.3.08.112')
    THEN 'FIXED(修复版)' ELSE 'OTHER(旧版)'
  END AS ver_group,
  SUM(cnt) AS total_failed_records,
  COUNT(*) AS unique_requests,
  SUM(CASE WHEN cnt > 1 THEN 1 ELSE 0 END) AS dup_requests,
  SUM(CASE WHEN cnt > 1 THEN cnt - 1 ELSE 0 END) AS excess_records,
  ROUND(SUM(CASE WHEN cnt > 1 THEN cnt - 1 ELSE 0 END) * 100.0 / SUM(cnt), 2) AS excess_rate_pct,
  SUM(CASE WHEN reqid_empty > 0 THEN 1 ELSE 0 END) AS reqs_with_empty_reqid,
  MAX(cnt) AS max_dup_count
FROM (
  SELECT
    regexp_replace(sdk_version,'-.*$','') AS ver,
    vin, request_id,
    COUNT(*) AS cnt,
    SUM(CASE WHEN request_id IS NULL OR TRIM(request_id)='' THEN 1 ELSE 0 END) AS reqid_empty
  FROM datalake.kpi.dwi_vpd_event_track_to_adssdk
  WHERE os_name = 'IOS' AND event_id = '30601' AND result = '1'
    AND failure_reason IN ('14','15','18')
  GROUP BY regexp_replace(sdk_version,'-.*$',''), vin, request_id
) t
GROUP BY 1
ORDER BY 1
```

**SQL-2：按版本+failure_reason 明细拆分（定位哪个版本/reason 残留）**
```sql
SELECT ver, failure_reason,
  SUM(cnt) AS total_records, COUNT(*) AS unique_reqs,
  SUM(CASE WHEN cnt > 1 THEN 1 ELSE 0 END) AS dup_reqs,
  SUM(CASE WHEN cnt > 1 THEN cnt - 1 ELSE 0 END) AS excess,
  MAX(cnt) AS max_dup,
  ROUND(SUM(CASE WHEN cnt > 1 THEN cnt - 1 ELSE 0 END) * 100.0 / SUM(cnt), 1) AS excess_pct
FROM (
  SELECT regexp_replace(sdk_version,'-.*$','') AS ver, failure_reason, vin, request_id, COUNT(*) AS cnt
  FROM datalake.kpi.dwi_vpd_event_track_to_adssdk
  WHERE os_name = 'IOS' AND event_id = '30601' AND result = '1'
    AND failure_reason IN ('14','15','18')
    AND regexp_replace(sdk_version,'-.*$','') IN ('1.3.08.104','1.3.08.105','1.3.08.107','1.3.08.108','1.3.08.109','1.3.08.112')
  GROUP BY regexp_replace(sdk_version,'-.*$',''), failure_reason, vin, request_id
) t
GROUP BY ver, failure_reason
ORDER BY ver, failure_reason
```

**SQL-3：重复请求明细（含时间跨度，定位是否修复前遗留）**
```sql
SELECT ver, failure_reason, vin, request_id, cnt AS dup_count, min_time, max_time, hours_span
FROM (
  SELECT regexp_replace(sdk_version,'-.*$','') AS ver, failure_reason, vin, request_id,
    COUNT(*) AS cnt, MIN(event_time) AS min_time, MAX(event_time) AS max_time,
    ROUND((UNIX_TIMESTAMP(MAX(event_time)) - UNIX_TIMESTAMP(MIN(event_time))) / 3600.0, 2) AS hours_span
  FROM datalake.kpi.dwi_vpd_event_track_to_adssdk
  WHERE os_name = 'IOS' AND event_id = '30601' AND result = '1'
    AND failure_reason IN ('14','15','18')
    AND regexp_replace(sdk_version,'-.*$','') IN ('1.3.08.104','1.3.08.105','1.3.08.107','1.3.08.108','1.3.08.109','1.3.08.112')
  GROUP BY regexp_replace(sdk_version,'-.*$',''), failure_reason, vin, request_id
) t
WHERE cnt > 1
ORDER BY cnt DESC
LIMIT 50
```

**SQL-4：各修复版数据时间范围（判断版本是否仍活跃、重复是否修复前遗留）**
```sql
SELECT regexp_replace(sdk_version,'-.*$','') AS ver,
  COUNT(*) AS total_failed, MIN(event_time) AS earliest, MAX(event_time) AS latest,
  SUM(CASE WHEN event_time >= '2026-08-16' THEN 1 ELSE 0 END) AS records_after_0816,
  SUM(CASE WHEN event_time >= '2026-08-19' THEN 1 ELSE 0 END) AS records_after_0819
FROM datalake.kpi.dwi_vpd_event_track_to_adssdk
WHERE os_name = 'IOS' AND event_id = '30601' AND result = '1'
  AND failure_reason IN ('14','15','18')
  AND regexp_replace(sdk_version,'-.*$','') IN ('1.3.08.104','1.3.08.105','1.3.08.107','1.3.08.108','1.3.08.109','1.3.08.112')
GROUP BY regexp_replace(sdk_version,'-.*$','')
ORDER BY ver
```

**SQL-5：修复后窗口重复检测（event_time >= 截止日期，最终判定）**
```sql
SELECT
  CASE WHEN ver IN ('1.3.08.104','1.3.08.105','1.3.08.107','1.3.08.108','1.3.08.109','1.3.08.112')
    THEN 'FIXED(修复版)' ELSE 'OTHER(旧版)' END AS ver_group,
  SUM(cnt) AS total_records, COUNT(*) AS unique_reqs,
  SUM(CASE WHEN cnt > 1 THEN 1 ELSE 0 END) AS dup_reqs,
  SUM(CASE WHEN cnt > 1 THEN cnt - 1 ELSE 0 END) AS excess,
  ROUND(SUM(CASE WHEN cnt > 1 THEN cnt - 1 ELSE 0 END) * 100.0 / SUM(cnt), 2) AS excess_pct,
  MAX(cnt) AS max_dup
FROM (
  SELECT regexp_replace(sdk_version,'-.*$','') AS ver, vin, request_id, COUNT(*) AS cnt
  FROM datalake.kpi.dwi_vpd_event_track_to_adssdk
  WHERE os_name = 'IOS' AND event_id = '30601' AND result = '1'
    AND failure_reason IN ('14','15','18')
    AND event_time >= '2026-08-16'
  GROUP BY regexp_replace(sdk_version,'-.*$',''), vin, request_id
) t
GROUP BY 1
ORDER BY 1
```

**SQL-6：修复后窗口残留重复明细（HAVING COUNT > 1）**
```sql
SELECT regexp_replace(sdk_version,'-.*$','') AS ver, failure_reason, request_id,
  COUNT(*) AS dup_count, MIN(event_time) AS min_time, MAX(event_time) AS max_time
FROM datalake.kpi.dwi_vpd_event_track_to_adssdk
WHERE os_name = 'IOS' AND event_id = '30601' AND result = '1'
  AND failure_reason IN ('14','15','18')
  AND event_time >= '2026-08-16'
  AND regexp_replace(sdk_version,'-.*$','') IN ('1.3.08.104','1.3.08.105','1.3.08.107','1.3.08.108','1.3.08.109','1.3.08.112')
GROUP BY regexp_replace(sdk_version,'-.*$',''), failure_reason, request_id
HAVING COUNT(*) > 1
ORDER BY dup_count DESC
```

### 结果与结论（2026-08-21，pre-prod）

**全量对比**：
| 分组 | 失败总数 | 唯一请求 | 重复请求 | 多余记录 | excess率 | 最大重复 |
|---|---|---|---|---|---|---|
| 修复版 | 836 | 789 | 12 | 47 | 5.62% | 20 |
| 旧版 | 1051 | 543 | 157 | 508 | 48.33% | 30 |

**修复后窗口（≥08-16）**：
| 分组 | 失败总数 | 唯一请求 | 重复请求 | 多余记录 | excess率 | 最大重复 |
|---|---|---|---|---|---|---|
| 修复版 | 203 | 202 | **1** | 1 | **0.49%** | 2 |
| 旧版 | 335 | 146 | 67 | 189 | 56.42% | 15 |

**按版本**：
| 版本 | 时间范围 | 活跃 | 重复时间 | 08-16后 | 08-16后重复 |
|---|---|---|---|---|---|
| .104 | 08-06~08-07 | 否 | 08-06 | 0 | 0 |
| .105 | 08-12~08-18 | 否 | 08-12/13/18 | 6 | 1（08-18,2条,0.2s） |
| .107 | 08-08~08-13 | 否 | 无 | 0 | 0 |
| .109 | 08-14~08-20 | **是** | 08-14/15 | 4 | **0** |
| .112 | 08-15~08-20 | **是** | 无 | 193 | **0** |

**08-16后唯一残留**：v105, reason=14, request_id=B0B5A8892A7F42A297F667F8B01525EF, 2条记录间隔0.2秒, 08-18（版本已退役）。

**结论：iOS 重复误报在 pre-prod 已基本修复完成。**
- 修复后 excess 率从 48%→0.49%（降 99%）。
- 最新活跃版本 .109/.112 在 08-16 后零重复。
- 所有重复集中在 08-15 及之前（修复前数据）。
- 08-16 后仅 1 例残留（已退役版本 .105 的 0.2 秒双发），不影响结论。

### 经验与教训
1. **adssdk 表 result 取值三端不一致**：IOS='1'/'0'，Android='success'/'failed'，harmonyOS='success'/'failed'/'fail'。按端过滤时需分别处理。
2. **vin 在 adssdk 表 IOS 记录中普遍为空**：重复检测实际靠 request_id 单字段即可（request_id 无空值）。
3. **时间窗口切分是关键**：修复版中仍含修复前旧数据（版本号相同但时间早于修复部署），必须用 event_time 切分修复前/后，否则结论被旧数据污染（全量 5.62% vs 修复后 0.49%）。
4. **版本活跃度判断**：用 MIN/MAX(event_time) + COUNT 确认版本是否仍在产生数据，已退役版本的重复无分析价值。
5. **ODPS SQL 注意**：`regexp_replace(sdk_version,'-.*$','')` 去版本后缀；`UNIX_TIMESTAMP` 可算时间差；`HAVING COUNT(*) > 1` 筛选重复组。

### 待办/可追问
- v105 08-18 的 0.2 秒双发（request_id B0B5A88...）：是客户端重试还是服务端重复入库？可拉该 request_id 全字段明细对比。
- 若需确认修复部署精确时间点，可对 08-14~08-16 的重复按天/小时追踪递减趋势。
- failure_reason=15（车辆信息验证失败）的 20 次/13 次集中爆发（v109 08-15）：是否与特定车辆/场景相关，可拉 vin（若有）或 request_id 关联其他事件。

---

<!-- 新用例在此行下方追加 -->

## 用例 3：核查 NEXT(harmonyOS) 上报 KPI 数据 sdk_version 为空 是否仍存在（pre-prod）

### 业务需求
原问题：NEXT 上报的 KPI 数据中存在大量 sdk_version 字段值为空。在 pre-prod 分析新上报数据是否仍存在空值。
- NEXT = `os_name='harmonyOS'`（用户确认）。

### 关键字段与陷阱
- 表 `datalake.kpi.dwi_vpd_event_track_to_vpd`，`os_name` 精确取值 `harmonyOS`（首字母小写 h）。
- `source` 字段是 integer 类型，**不可** `COALESCE(source,'(null)')`（报 `invalid input syntax for integer: "null"`，Position 29）；需 `CAST(source AS VARCHAR)`。
- ADS 的 `DATE_FORMAT(x,'YYYY-MM-DD')` 不生效（返回字面量 'YYYY-MM-DD'）；按天分组用 `SUBSTR(CAST(dws_create_time AS VARCHAR),1,10)`。
- "新上报数据"按 `dws_create_time`（入湖时间）过滤，而非 `event_time`。
- 刷新失败时 BI 弹**错误对话框**（非 toast），`[class*="error"]` 的 toast 检测抓不到；需检测 `.yh-dialog/.el-dialog` 含"执行SQL失败"，并在下次刷新前点"确定"关闭对话框。
- 执行类脚本须用 `chromium.launch()`+`newContext()`（非持久化），**禁止** kill 已有 msedge 进程（见 SKILL.md 硬约束 6）。

### 主 SQL（按日趋势）
```sql
SELECT
    SUBSTR(CAST(dws_create_time AS VARCHAR),1,10) AS dt,
    COUNT(*) AS total_cnt,
    SUM(CASE WHEN sdk_version IS NULL OR TRIM(CAST(sdk_version AS VARCHAR)) = '' THEN 1 ELSE 0 END) AS empty_ver_cnt,
    SUM(CASE WHEN sdk_version IS NOT NULL AND TRIM(CAST(sdk_version AS VARCHAR)) <> '' THEN 1 ELSE 0 END) AS filled_ver_cnt,
    ROUND(100.0 * SUM(CASE WHEN sdk_version IS NULL OR TRIM(CAST(sdk_version AS VARCHAR)) = '' THEN 1 ELSE 0 END) / COUNT(*), 2) AS empty_pct
FROM datalake.kpi.dwi_vpd_event_track_to_vpd
WHERE os_name = 'harmonyOS'
  AND dws_create_time >= DATE_SUB(CURRENT_DATE(), 14)
GROUP BY SUBSTR(CAST(dws_create_time AS VARCHAR),1,10)
ORDER BY dt DESC
LIMIT 100
```

### 结果与结论（2026-08-22，pre-prod）
- 近 14 天（08-08~08-22）harmonyOS 共 43,444 条，sdk_version 空 27,264 条，**总空率 62.76%**。
- 趋势：最早 3 天空率 80.18% → 最近 3 天 40.62%，下降但未清零；8-18 回退到 76.51%。
- 最新完整日 08-21：49.03%（1948/3973）空；08-22 当日 31.49%（308/978）。
- 按 event_id（近 7 天）空率普遍 30~63%（30307/30319/30317/30308 等），系统性问题非单事件。
- **结论：问题仍严重存在**，修复有进展但未完成。

### 待办/可追问
- 拉 8-22 空样本明细（request_id/vin/event_time/sdk_version）定位未上报版本号的车型/版本。
- 追踪 8-18 空率回退原因。

---

<!-- 新用例在此行下方追加 -->

---

## 用例 4：DTS2607230081018 回归——三端 30302 失败事件 failure_reason 空值/格式非约定（pre-prod）

### 业务需求
DTS2607230081018（Major，测试人员回归测试节点）：三端 30302 失败事件 failure_reason 报空值 + 部分错误码车云传值格式非约定。修复方案（WIKI2026081117197761，MR iossdk/81 + harmonyossdk/1225 + manifest/4213，08-11/12 合入，08-14 转测）：三端统一优先读 code（云端码）无 code 读 resultCode（车端码）；两者皆空报 -1；网络码映射 2300006→9999、2300007→100008、2300028→888888。

### 关键经验
- **回归口径按版本分组，不按 event_time 时间窗二分（2026-09-09 用户纠正）**：转测时间=修复版本上预生产的时间（生产更晚），预生产车辆版本异构，存量旧版本（1.3.03~07 线等）在转测后仍持续上报旧行为，时间窗"修复前/后"会互相污染。正确做法：按「版本是否含修复」分组、组内取该版本全窗口数据；event_time 仅用于剔除回填、确定版本首现日。dws_create_time 永远不用于窗口/趋势（08-28 入湖峰值系历史回填，event_time 早于 07-23）。
- **版本→修复映射锚点**：sdk_version 原始值带 OEM 轨道后缀（-qiankun/-hima/-chery/-voyah/-adsapp 等，regexp_replace 前先看原始值分布）；harmonyOS SNAPSHOT 构建号内嵌构建日期（1.3.08.2-20260815733568-SNAPSHOT-adsapp-pre=08-15 构建），可与 MR 合入日精确比对；IOS 无构建号，用首现日期近似（首现=首次失败上报日，非构建日）。
- **-1/映射码都不是可靠修复信号**：-1 修复前就存在（远控超时码）；hima APP 侧自行做码映射（旧版 1.3.06.136 报 49 条 9999/888888）；Android 的 -1 全是 APP 侧远控超时码。最可靠锚点是转测载体/最新构建的行为（如 harmonyOS 09.2 构建：原始网络码清零 + -1×17 + 空值率 2.3%）。
- **1XXXX=云端码未录表的"未知错误"**（WIKI 测试建议明确要录表），其持续出现不等于 SDK 读码缺陷；乾崑轨道（APP 透传两字段）能读到云端码 → 11000 类集中出现在 qiankun 轨道而非 SDK 缺陷。
- **Android 无 androidsdk MR 时不存在"含修复 SDK 版本"**：改按 APP 轨道（OEM 后缀）分析——空值率由 APP 是否透传决定（乾崑 43% / deepal 0% vs chery/voyah ≈100%）。
- **引擎坑：相邻两条 SQL 若 AS 别名完全相同，第二条会在 poll 0 抓到第一条的陈旧网格结果**。规避：加区分列或改别名；本例用 CASE WHEN 合并 PRE/POST 为一条 SQL。
- ADS SQL 不支持 RLIKE，用 LIKE '1.3.09.%' OR LIKE '1.3.9.%'（非补零 1.3.9.101 = 1.3.09.101）。

### 回归结论（2026-09-09 终版：✅ 通过；v2 版本口径 + request_status 交叉验证）

**最终判定（处理人 2026-09-09）：✅ 通过**——空值场景经业务确认 = 上电请求 60s 超时（`request_status='2'`，SDK 双路径失败判定之一：①回调错误码 ②等 60s），**疑似车端问题后续另行分析，不影响本单判定**。空值记录几乎 100% 带 status='2'（三端交叉验证），链路无环节产生错误码、天然无码；`reason='-1'` = 回调带回的车云/APP 侧远控超时码（与 SDK 自身 60s 超时是两个超时源，-1 行 status 全空）。

| 端 | 判定 | 关键数据 |
|---|---|---|
| IOS | ✅ 修复生效 | 含修复版本组 99/11000 占比 3.2% vs 存量组 16.5%（收敛 84%）；残留 11000 类均为云端码未录码表 |
| harmonyOS | ✅ 修复生效 | adsapp 转测载体：原始网络码清零、-1 兜底可见、最新 09.2 构建空值率 2.3% |
| Android | ✅ 通过（本单不涉及 SDK 修改） | 无 androidsdk MR；空值由 OEM APP 是否透传决定 + 超时场景（status=2，转车端问题分析），另单跟踪 |

**可复用要点**：KPI"缺码失败"监控口径 = `request_status='2' AND failure_reason 为空`（修复/整改铺开后应降、-1 应升）；业务路径确认后"空值率"重定性为"上电 60s 超时率"（业务质量信号，非纯上报格式问题）。

完整报告：看板工作区 `reports/DTS2607230081018-30302回归报告-简明.md`（终版简明）+ `reports/DTS2607230081018-30302回归报告.md`（详细数据分析，顶部有终版结论横幅）；SQL 套件 `archive/sql-reg81018/`（21 条），原始结果 `archive/2026-09-09T01-41-*`~`06-15-*`。

---

<!-- 新用例在此行下方追加 -->

## 用例 5：DTS2608110099372 回归——30301 获取远控证书失败事件 failure_reason 具体错误码（pre-prod）

### 业务需求
DTS2608110099372（Major，提单 08-11 黄子傲，状态=测试人员回归测试）：0811 生产 KPI（7.28~8.11）30301 失败事件安卓 9 次 failure_reason 空 + 三端码值分裂（安卓码2"结果返回为空"40 次 / iOS 码1"请求失败"900+ / Next 码1 1400+）。修复：失败原因改从车云错误码获取（MR androidsdk !1511/!1650、harmonyossdk !1373、iossdk !183 + manifest !4364/!4705/!4720，08-14~08-24 合入；CMO 08-28 转测手机SDK 8月迭代第四轮，QIANKUNT10.B010）。

### 关键表与码表
- 30301 = 获取远控证书，在 **vpd 表**（`dwi_vpd_event_track_to_vpd`）。码表（接口字典）：failure_reason **0=成功 / 1=获取本地证书请求失败 / 2=获取本地证书结果返回为空**。
- 修复特征码：`-1`=车云无码兜底、`11000`=车云端码（未录 KPI 码表）、`100008`=网络码映射（2300007→100008，同 81018 模式）。
- ⚠️ **pre-prod vpd 表无 `vehicle_brand` 列**（prod 才有，SQL_ERROR 实测）；判轨道用 sdk_version 后缀（-qiankun/-hima/-chery…）与构建时间戳。
- Android SNAPSHOT 版本号内嵌毫秒构建时间戳（`1.3.08.01787387501433ads-SNAPSHOT-qiankun-pre` → 08-22 16:49 构建），可与 MR 合入时刻精确比对——比首现日期更可靠。

### 回归结论（2026-09-09，✅ 通过）
- **Android ✅**：08-22 起 -1 首现（!1650 08-21 合入次日），reason=1 于 08-21 精确止步；修复后构建版（08-22+ SNAPSHOT、1.3.09）失败 100% 报 -1 零空值。
- **harmonyOS ✅**：11000 于 08-21 首现（!1373 08-20 次日）；转测窗口 1.3.08.216 报 100008（映射生效）；09-03 后本地码彻底归零。
- **IOS（乾崑）⚠️ 覆盖度低**：修复版载体（1.3.08.109/112/114/119/128-qiankun、1.3.9.101-qiankun）pre-prod 零失败，失败场景未触发；残留码1 失败全在 hima/chery/freelander 轨道（构建于 08-24 release 前后）。
- **空值 ✅**：pre-prod 07-01 以来仅 1 条空值（07-02 修复前），08-22 后三端零空值。
- 残留本地码归因：1.3.05.002 存量旧版（7 月线）+ chery 等非乾崑轨道，版本异构预期内；同版本号 08-22/24 后行为切换（-1 出现）= SDK 热更铺开、sdk_version 未变。
- 遗留：Android 4.18.020（4.x 无后缀线）09-04 报码2×2 待开发确认轨道/构建；11000 建议补录码表；IOS 待生产补验；prod 空值专项待铺开复跑。

### 生产观察（2026-09-10，口径按版本）
- **提单窗口（7.28~8.11）prod 精确复现**：安卓 empty=9（与提单精确一致，全部集中在 07-28 21:27~21:53 一小时内、vin 全空=单批次环境事件，此后零新增）、码2=70；IOS 码1=898（提单 900+）；harmonyOS 码1=1895（提单 1400+）。
- **生产修复铺开状态=尚未铺开**（截至 09-10）：修复特征码（-1/11000/100008）生产全程零；32 个版本×码组合 100% 本地码，全部为存量旧版；修复载体版本（QIANKUNT10.B010 / 4.17+ 线 / 1.3.08.109+）生产零出现。生产复验触发条件=生产出现修复载体版本流量。
- prod 版本后缀 `-product`/`-pro`（pre-prod 为 `-pre`）；prod 表 5500 万行 vs pre-prod 790 万；prod 配置 ads-kpi workspace `dataset-prod.json`（cookies-prod.json，CDP 捕获）。

### 可复用要点
- 修复行为切换判定锚点优先级：**SNAPSHOT 构建时间戳 > 码值按天首现/止步日 > 版本首现日**；同版本号内行为按天切分可佐证热更铺开。
- 三端一致性回归口径：修复前三端本地码分布分裂 vs 修复后统一车云码体系（-1/11000/100008），用「修复特征码 vs 本地码」按天矩阵（单条 SQL SUM CASE 输出 neg1/c11000/c100008/local/empty 五列）一屏判定。
- 载体版本零失败 ≠ 回归通过：需如实标注"无违规数据但覆盖度低"，生产铺开后补验。

报告：`看板/reports/DTS2608110099372_30301回归报告.md`；SQL：ads-kpi workspace `probe0~probe10*.sql`（11 条），归档 `ads-kpi/archive/2026-09-09T09-53-*`~`10-29-*`。

---

## 用例 6：DTS2608110101654 回归——30303 失败 failure_reason 空值（三端上报机制统一，pre-prod + prod）

### 业务需求
DTS2608110101654（Major，测试人员回归测试）：7.28~8.11 生产 KPI 30303 失败中仅 NEXT 出现 900+ 次 failure_reason 空值。定位结论（2026-08-29，见 OpenViking `30303_empty_reason_request_status.md`）：空值 = 请求层失败落在 request_status，仅 NEXT 区分请求层/业务层上报，Android/iOS 请求失败场景整体漏报。修复 = Android/iOS 对齐 NEXT 模式（androidsdk!1808 + manifest!5099 **0831 合入**；iossdk!254 **0901 合入**，均 release_g 分支）；NEXT 不改（打点符合预期）。CMO 第八轮转测 20260905。

### 回归特征与锚点（上报机制类修复，非码值类）
- **修复特征** = Android/iOS 30303 失败记录出现 request_status 有值（reason 空 + status 有值，对齐 NEXT）；基线 = 修复前三端中 Android/iOS req_status 恒 0（07-01~09-01 pre-prod 1911+ 条失败实证）
- 锚点：特征首现日 vs MR 合入日；Android SNAPSHOT 构建时间戳（1.3.09.01788397376855ads = 09-03 09:02 构建 > 0831 ✓）；1.3.05.002-qiankun 版本号未变但 09-02 行为切换 = 热更铺开（同 30301 案例模式）
- 第八轮 Android 转测载体识别：`1.3.09.01788593506697ads-SNAPSHOT-qianku`（09-05 15:26 构建 ≈ CMO 3C2XMWAY_20260905-153147）

### 回归结论（2026-09-10）
- **Android ✅ 修复生效**：特征 09-02 首现（0831 后第 2 天），9 条请求层失败上报（500×7/2×2），结构 100% 对齐 NEXT（reason 空+status 有值+request_id 齐全），业务码 15/16/17 上报不受影响。
- **iOS ⚠️ 无法判定（覆盖度缺口）**：载体版本（1.3.9.101-qiankun 09-02 首现、1.3.09.103-hima 09-05 首现≈CMO 引望智行 202609051819）0901 后 133+ 条失败全为业务码 15/16，零请求层失败场景触发——零记录既不能证实也不能证伪；**需在载体版本主动触发断网/5xx/超时场景后复验**。
- **NEXT ✅ 无回归**：模式延续（33 条，码值 2×24/403×9/1×1）。
- **prod 未铺开**（截至 09-10）：Android/iOS req 特征全程 0；提单窗口复现 NEXT 空 1235（"900+"吻合）/Android 0/6231/iOS 0/10160，口径校准 ✓。复验触发条件 = 生产出现 release_g 载体版本流量。
- **KPI 口径提示**：铺开后 Android/iOS 空值失败将非 0（设计内），看板需把 request_status 并入失败原因统计或映射兜底码（9999/888888），防误读为新问题。

### prod 铺开跟踪（2026-09-20 版本交叉）
- **iOS ✅ 修复已上生产**：载体版本 `1.3.09.123-qiankun-pro`（09-18 15:17 首现），19 条失败中 1 条请求层上报（09-18 16:28:32，reason 空 + request_status=1 网络不通，vin/request_id 齐全）——结构 100% 对齐 NEXT。⚠️ 样本量 1、覆盖度低，建议持续观察或载体版本主动触发断网/超时复验。pre-prod 已知载体（1.3.9.101-qiankun / 1.3.09.103-hima）prod 失败记录中未出现，prod 铺开走的是更新的 1.3.09.123-qiankun-pro。空值口径提醒已到期：iOS 空 reason 将开始非 0（该条即为 reason 空）。
- **Android 尚不可观测**：09-18 同波次出现 `1.3.09.014-qiankun-product`（15:35 首现，12 条失败全 reason 有值、0 请求层）；52 个版本全部 req_filled=0 且 empty_reason=0（Android 失败恒为业务层）——无违规数据但请求层失败场景未在载体上触发，与 pre-prod 阶段 iOS 覆盖度缺口同理，保持复验。
- NEXT 现象稳定（近 7 天 2×85.9% / 1×13.1% / 403×4），非修复对象。
- 定时复查链路：sched1654-prod-status.js（dsh cron 09-10/09-17/09-20 三次；09-20 判定 rolledOut=true 触发本版本交叉）。
- 版本交叉 SQL：workspace `p1654-v0-ping / v1-iosverstat / v2-iosreqdetail / v3-androidver`；归档 `ads-kpi/archive/2026-09-20T01-42-*`；报告 `看板/reports/prod-status-check/DTS2608110101654-prod-status-20260920-*.md` §7。

### SQL 套件（p1654-0~10 + p1654-p0~p2，14 条）
核心四条：三端失败按天矩阵（os_name×day：failed/req_filled/empty_reason）；端×版本×req_filled 交叉（识别载体）；Android 特征明细（req_status 有值记录的时间/版本/码值/request_id，验证结构对齐）；历史基线（07-01~08-20 三端 req_filled 对照，排除偶发旧记录）。prod 侧同口径三条（ping/三端总览/提单窗口复现）。

报告：`看板/reports/DTS2608110101654_30303回归报告.md`；SQL：ads-kpi workspace `p1654-*.sql`；归档 `ads-kpi/archive/2026-09-10T04-07-*`~`04-15-*`。

---

## 用例 7：VPD 使用人鸿蒙系统版本占比（prod）

### 业务需求
看 VPD 使用人（distinct vin）实际的鸿蒙系统（os_name='harmonyOS'）版本分布，生产环境。

### 关键口径（实测 2026-09-20，prod）
- **`os_version` 字段**：vpd 表第 5 列（request_id/service_tag/sdk_version/os_name/os_version/…），鸿蒙端取值 `6.1.0` / `7.0.0` / `6.0.0` / `5.1.0` / `5.0.1`（另有 5.0.0 极少量），鸿蒙端填充率 100%；Android 样本值为 `16` 等整数（Android 大版本）。adssdk 表与 vpd 表共享前 15 列，同样有 os_version。
- **用户口径须按 vin 收敛**：按版本 GROUP BY 的 `COUNT(DISTINCT vin)` 合计（63,632）> 全体 harmonyOS distinct vin（61,750），~3% 用户窗口内跨多版本（系统升级）——主口径用内层 `GROUP BY vin` + `MAX(os_version)` 再外层分布。
- **鸿蒙空 vin 注意**：14 天窗口 harmonyOS 有 87.6 万事件 vin 为空（占 30.3%），事件口径分布含这些行、用户口径天然剔除——两口径数字有系统性差异。
- **vpd 与 adssdk 两表版本结构一致**（14 天 vin：6.1.0 43,419 vs 43,498；7.0.0 19,850 vs 19,880），跨表结论稳健。

### SQL 套件（hmosv-*.sql，13 条，归档 2026-09-20T03-15/03-19/03-37/04-02~04-*）
1. ping + 鸿蒙 os_version 事件分布 + 三端 vin 占比 + adssdk 交叉（p0~p3）
2. 每用户取最高版本分布（f1，14d）/ 鸿蒙用户占比与空 vin 计数（f2）/ 30 天同口径（f3）
3. hima 口径同分布（w1）+ 按日事件量完整性校验（w2，窗口末端无入库延迟截断）
4. **三端扩展（a1~a6）**：iOS/Android 取值格式探针（iOS 三段式 121 个精确版本长尾；Android 整数大版本 9~17，字典序陷阱须数值 MAX）+ 每用户聚合四条（iOS/Android × 全量/hima）

### 结果（prod，2026-09-06~09-20 滚动 14 天，每用户取最高版本）
| 鸿蒙版本 | 用户数(vin) | 用户占比 | 事件数 | 事件占比 |
|---|---|---|---|---|
| 6.1.0 | 41,587 | 67.34% | 1,863,984 | 64.47% |
| 7.0.0 | 19,850 | 32.15% | 1,007,076 | 34.83% |
| 6.0.0 | 291 | 0.47% | 19,392 | 0.67% |
| 5.1.0 | 20 | 0.03% | 610 | 0.02% |
| 5.0.1 | 2 | ~0 | 84 | ~0 |

- 鸿蒙使用人 61,750，占 vpd 全体使用人（184,280 distinct vin）33.5%。
- 30 天口径（08-21 起，120,279 vin）：6.1.0 72.06% / 7.0.0 27.43% / 6.0.0 0.48% → **7.0.0 用户占比 30 天→14 天从 27.4% 升到 32.2%，新版本爬坡中**。
- 结论：6.1.0+7.0.0 合计 99.5%，5.x 存量可忽略；若做版本兼容性决策，重点覆盖 6.1.0 与 7.0.0。
- **三端扩展（同日，同口径）**：iOS 44,795 使用人——**26.x 占 77.3%**（hima 78.8%）、18.x 存量 9.9%、27.x 新线爬坡 6.6%，精确版本 121 个长尾需按大版本线汇总；Android 83,115 使用人——**12+16 双峰合计 91.2%**（hima 92.4%），17 已现 1.0%；三端使用人 Android 45.0% / 鸿蒙 33.5% / iOS 24.2%，三端合计比全体去重 VIN 高 2.7%（一车绑多部不同系统手机）。**已写入 wiki WIKI2026091517265485 §4.5**（三端版本，wiki-api.js update --file 路径，MCP fetch 复验）。

---

<!-- 新用例在此行下方追加 -->

## 用例 8：DTS2608110101679 回归——30304 证书同步 failure_reason 安卓 0 码 / 鸿蒙空值（pre-prod + prod，三轮）

### 业务需求
DTS2608110101679（Major，测试人员回归测试）：7.28~8.11 生产 KPI 30304 失败上报 next 100+ 次 reason 空 + 三端不一致（安卓 200+ 次报成功码 0、next 200 次证书同步超时 7 码）。修复：双端统一「发送证书失败且无非 0 云端码 → 报 99」（androidsdk !1530/!1567、harmonyossdk !1303，08-22 推库转测；安卓载体→1.3.08.015/016-qiankun、鸿蒙载体→1.3.09.2-adsapp）。

### 关键口径与陷阱
- 30304 在 **vpd 表**；**99 鸿蒙修复前就有 442 条**（sendVehicleRes=false 旧路径），不能单独作鸿蒙修复签名；安卓修复前从未报 99/7（基线 265 条全 0）→ 安卓载体上报 7/99 即修复特征。
- **缺陷路径结构签名（2026-09-21 新发现）**：`(empty) ↔ request_status ∈ {2, 401, 403}`（请求层失败→云端无响应体→SDK 读 e2eResErrorCode 得 undefined→报空，即本单缺陷路径）；`7/99 ↔ rqstat 空`（HTTP 200 有码透传/旧路径）。修复生效的可观测变化 =「empty+rqstat非空」转化为 99——比"空值归零"更精准的复验口径。
- **载体版本≠新版本**：prod `1.3.09.216-adsapp-pro` 虽 09-17 起爬坡，车队却自 2026-04 存在（437 VIN/2.6 万事件）——版本线存在时长须查全历史 MIN(event_time)，勿按首现流量日推断构建新旧。
- 归一化 sdk_version 会剥 OEM 后缀混轨道，回归判定必须用原始串切流（qiankun/adsapp=乾崑线）。
- **4.x 版本号 = 运营 SDK 的 KPI，非 VPD SDK**（2026-09-21 用户确认；与乾崑转测线无关，关系与影响待查）——4.17/4.18 的 0 码记录不计入 VPD SDK 修复判定。
- **KPI 回归单结论四要素（2026-09-21 用户要求固化）**：①原报什么不对/预期报什么 ②端侧现状报了什么、什么没报 ③版本情况（尤其转测日后新出现的版本）④生产/预生产分开；判定口径跟单据声明（写了乾崑就用乾崑载体线，非乾崑轨道只作注记）。

### 回归结论（2026-09-21 终版，乾崑口径，**✅ 暂时通过（用户裁定 09-21）+ 已走单关闭（关闭问题单/问题解决关闭，flowState=FS99），待数据爬坡复验**）
- **Android 乾崑 pre-prod ✅ 通过**：新版本（首现≥08-22，48 个）**190 次 = 180 success + 10 failed（10/10 全报 7）**，0 码/空值/99 均为 0；0 码残留 24 组全为存量旧版。⚠️ 99 场景双环境全历史零样本。**prod 载体已铺开**：1.3.08.015-qiankun-product + 1.3.09.006/010/014，09-01~09-21 21 天窗口 **19 天有上报（09-02/03 无记录），合计 300 次全 success、0 失败**。
- **harmonyOS 乾崑 pre-prod ⚠️ 无法判定（覆盖度缺口）**：转测载体 1.3.09.2-adsapp 正式版全历史零事件；**新版本（首现≥08-22，45 个）201 次 = 189 success + 12 failed（empty×5 全在 SNAPSHOT 日构建 + 7 码×5 + 码1×2）**；08-22 后新出现的 adsapp 正式版（1.3.08.216/220、1.3.09.213~217）失败均带码、零 empty。
- **harmonyOS prod**：载体未铺开；**新版本（9 个）36 次 = 32 success + 4 failed（全码 1，202-voyah）——空值 = 0，prod 空值 100% 来自存量版本**（无版本号 116、201-hima 157、209-adsapp 45、204-avatr 28、206-hima 43 等 + 存量线 216-adsapp-pro empty×7）；**216 爬坡中**（09-18 122→09-21 1091 success），99×1+empty×7 并存（empty 结构=未修复签名），是否含 !1303 待开发确认。
- 范围外：IOS 无修复 MR；4.17/4.18=运营 SDK KPI；1.3.09 非乾崑轨道（hongqi/mhero/voyah 0 码）乾崑 MR 未覆盖待确认。

报告：`看板/reports/DTS2608110101679_30304回归报告.md`；SQL：ads-kpi workspace `n1679-*.sql`（22 条）+ 历史 `r1679*.sql`；归档 `ads-kpi/archive/2026-09-21T07-*`~`10-*`。

---

## 附：SDK KPI 整体周报（指针，2026-09-21 立项）

> **完整周报规范（八节结构/口径/SQL 资产/窗口替换/评审决议）见 `references/weekly-report.md`——仅周报任务时读，日常查询勿加载。**

### 通用口径（周报与日常查询共用）
- **iOS result 判定（2026-09-22 修正）**：vpd 表（303xx）iOS 与其他端同为 success/failed；仅 adssdk 表（306xx）iOS 按码 0=成功/1=失败。wk3v-events-os.sql 旧版误把 vpd iOS 按 0/1 判定导致 iOS 全列"口径外"，已由 wk3v2-events-os.sql 修正——**任何 event×os 查询套用此判定**
- **reason 码表字典**：`ads-kpi/kpi-reason-dict.json`（event→{码:描述}，OneBox KPI 接口字典 376 行派生，2026-09-22）；已知缺口：30601 failureReason 整表、30307 整表、30305 的 -2/40007/10033/2300028、30319 告警 ID 901/5017/5028/5049
- 周报生成器 `ads-kpi/gen-sdk-weekly.js` + SQL 集 wk1~wk12 清单与换周日期替换表：见 weekly-report.md §4

### 事件语义与码表片段（通用业务知识）
- **30605 = 云端接口请求事件**（2026-09-21 用户定性）：prod 一周 1,764 万条、远超其他事件且大量无 vin——具体情况后续专项分析
- **云端错误码 10002 = 算路不可达**：提示"无可用导航路径，请更换目标车位"；实际为法规限制后算路仅允许 30m 内，超过 30m 云端拦截（30305 失败 top reason，55,773 次/周）
- **30317 = 点亮报告查看**事件；其 failure_reason 通过 30605 指标统计、筛选 parkingLot/smartdriving 接口（待办）
- 30307 失败 top reason=空（34,759 次/周）：推进专项
- 30309 reason=6（30,585 次/周）：要求在 30319 中详细分析；30319 存在 result 非标准值（43.2 万条/周，success/failed 之外疑空值）
- 30304 码表（本单语境）：99=车辆未上电/证书下发失败兜底、7=证书同步超时；全局语义待业务确认
- 各事件→泊车阶段映射（阶段成功率视图）：待用户确认口径

---

## 用例 9：DTS2608110101686 回归——30305 算路失败 iOS 成功码 10000 误报 + 三端空值（pre-prod + prod）

### 业务需求
DTS2608110101686（Major，测试人员回归测试）：0811 生产 KPI（7.28~8.11）30305 失败上报三端不一致——安卓 20 次 reason 空、next 160+ 次 reason 空、**iOS 报 1700+ 次成功码 10000**（算路成功无异常被当失败原因）。根因：dealPath 不区分成功/失败码，路径空+errorCode=10000 时把成功码透传到 failure 路径。修复：该场景替换为 unknowFailed(99)，MR **iossdk !198**（08-24 合入 release）；harmony 0821；Android 代码分析无问题。

### 关键口径与陷阱
- 30305 在 **vpd 表**；**10000="算路成功无异常"成功码（本单特有码义，勿当失败码解读）**；修复特征=99 替代 10000。
- ⚠️ **99 非独立修复签名**（unknowFailed 通用兜底，修复前三端已大量存在：prod 窗口 iOS 99×1457）——可靠判定口径 = **08-24 后构建的新版本零 10000**（10000 只能来自未修复代码路径）+ 载体 99 上报佐证。比 30304（99 前已有）更需警惕：99/10000 修复前在同一版本上并存（1.3.08.105-hima 两码同报至 09-21 = 该构建不含修复的铁证）。
- **iOS 10000 主体在非乾崑轨道**（hima/avatar/voyah/mhero/byd/dfmc/freelander，窗口 2,390 条中乾崑仅 9 条单日）——但修复在 iOS SDK 公共层（release 分支），非乾崑轨道新构建同样零 10000，判定不限于乾崑。
- **"新版本"过滤用失败记录首现近似有陷阱**：1.3.06.138-qiankun-pro 失败首现 09-17 却是旧构建迟到流量（报 1 条 10000）——版本号线（1.3.06<<1.3.08）优先于首现日判新旧。
- 基线复现精确锚点：Android empty=**20**（1.1.9.08-avatra 单版单日批次）/ next empty=213 / iOS 10000=2390。

### 回归结论（2026-09-21，**✅ 暂时通过（乾崑口径正向验证完成；非乾崑 8 轨道修复未铺开，挂复验条件）→ 已走单关闭（关闭问题单/问题解决关闭，flowState=FS99）**）
- **iOS ✅ 修复生效**：08-24 后构建新版本（全轨道，36 组中除 2 个旧版迟到流量）零 10000 零空值；乾崑 prod 载体 1.3.09.123-qiankun-pro（09-19 首现）28 次失败=99×23+其他×5；pre-prod 载体（1.3.9.101 99×62、1.3.08.128/119 等）同口径。pre-prod 10000 场景稀薄（全历史 217 条、08-15 后零），正向切换证据由 prod 载体提供。
- **iOS 非乾崑 ⚠️ 未铺开**：存量旧版（1.3.08.105-hima ×1,344 至 09-21、1.3.07.139-hima ×3,036 等 24 组）持续报 10000 30~70/天——轨道未上新版，非修复失效；复验触发=各轨道出现 1.3.08.106+/1.3.09.x 流量。
- **harmonyOS 空值**：乾崑 adsapp 线 ✅（07-01 以来 empty 仅 1 条，1.3.08.2-0822SNAPSHOT+ 载体报 99）；非乾崑 hima 1.3.08.201（×151）等持续 5-12/天，待轨道升级。
- **Android ✅ 无 SDK 缺陷**：20 条=avatra 旧版 07-28 批次，08-15 后仅 1 条。
- 遗留：1.3.06.138-qiankun-pro 09-17 单条 10000 待开发确认；4.x 运营线 10000（4.14.107×69）另单跟踪。

报告：`看板/reports/DTS2608110101686_30305回归报告.md`；SQL：ads-kpi workspace `q1686-p0~p7`（pre-prod 8 条）+ `q1686-b0~b7`（prod 8 条）；归档 `ads-kpi/archive/2026-09-21T13-48-*`~`14-04-*`。

---

<!-- 新用例在此行下方追加 -->

## 用例 10：DTS2608110101698 回归——30601 iOS reason 15 重复打点 8k+ 虚高（pre-prod + prod）

### 业务需求
DTS2608110101698（Major，测试人员回归测试）：0811 生产 KPI（7.28~8.11）30601 失败上报三端不一致，iOS 报 8k+ 次"其他原因造成车辆信息验证失败"（r15）。修复：①vtoken 检测去重复打点 ②匹配补遗漏 tips；CMO 08-27 转测第四轮（iOS 引望智行 202608271927）。与用例 2 同修复族（iossdk MR 1584 线）。

### 关键口径与陷阱
- **缺陷签名 = 同一 request_id 周期性重报**（分钟级间隔持续数小时~数天，vtoken 每次检测重打点），非仅亚秒双发；量化用 excess 率（总记录-唯一请求）。
- **prod `.112-pro` 与 pre-prod `.112` 同版本号不同构建**：pre-prod .112 含修复（用例 2 验证），prod .112-pro 构建于修复合入前（全历史首现 <07-28，excess 41%）——**勿按版本号跨环境推断构建内容**（iOS 无构建时间戳，用全历史 MIN(event_time) 判线龄）。
- prod 全历史 GROUP BY 会暴露时钟错乱行（2016/2026-05 时间戳），首现判定须剔除；新生版本探测 `HAVING MIN(event_time)>='2026-08-28'` 有效识别铺开。
- r2（未授权位置）/r17 路径的周期重报在修复后版本上仍存在（pre-prod .09.111 26次/4min、prod .123-pro 90次/5h）——疑似 vtoken 之外独立重复路径，非本单修复范围（待开发确认）。
- 用例 2 的 excess 口径只统计 reason 14/15/18；全 reason 口径 excess 会高 3~8 个点（其他 reason 也有亚秒双发）——对照历史结论时注意口径差。

### 回归结论（2026-09-22）
- **pre-prod ✅ 暂时通过**：乾崑载体代（.128/.9.101/.09.104/.107/.111/.118/.123，首现 08-31~09-18）excess 67%→1.4~7.8%，r15 周期重报消失；残留亚秒双发 + 3 个反证个例（.09.104 r15 21次/25min、.09.111 r2 26次/4min、.128 r15 6次/25s）待开发确认。
- **prod ❌ 未铺开**：08-28 后无新乾崑 iOS 版本；主力 .112-pro（转测前旧构建）excess 41%、r15 周期重报系统活跃（Top 106次/4天）；.123-pro 为 08-08 前构建非载体（r2 路径周期重报待查）。
- 基线复现：prod 7.28~8.11 IOS r15=9,949（"8k+"✓），67% 来自 1.3.07.141/123-qiankun-pro 存量。
- 复验触发条件：prod 出现 08-27 后构建的乾崑 iOS 版本流量（或 .112/.123 系列同版本号 excess 骤降=热更）；复跑 pr3/pr4/pr5。

报告：`看板/reports/DTS2608110101698_30601回归报告.md`；SQL：ads-kpi workspace `r1698-pp0~pp5` + `r1698-pr0~pr9`（16 条）；归档 `ads-kpi/archive/2026-09-22T03-08-*`~`03-44-*`。

---

## 用例 11：SVS 表与 30404 事件探查（pre-prod）

### 业务需求
用户问 hza_vpd 数据集下有没有 `dwi_vpd_event_track_to_svs` / `dwi_adssdk_event_track_to_svs`，以及 30404 事件在哪；后确认 SVS 数据在 `dwi_svs_kpi`。

### 结论（2026-09-22，pre-prod）
- **`*_to_svs` 两表不存在**（对照组法实证）：
  - `datalake.kpi.dwi_vpd_event_track_to_svs` → `relation does not exist`（对照 vpd 表正常 8,134,600 行）；
  - `datalake.kpi.dwi_adssdk_event_track_to_svs` → `relation does not exist`（对照 adssdk 表正常 6,085,163 行）。
- **30404 事件：vpd/adssdk 两表均为 0 条**；两表 event_id 全集实测（全表 GROUP BY，无时间过滤）：
  - vpd 表 26 个：**30301~30326 连续无缺口**；
  - adssdk 表 5 个：**30601 / 30602 / 30604 / 30605 / 30606**（无 30603）。
- **`dwi_svs_kpi` 表存在但当前账号无权限**：`datalake.kpi.dwi_svs_kpi` → `SELECT permission denied to user "dws_dataworks_ads_kpi_preprod" for relation "kpi.dwi_svs_kpi"`——**权限拒绝错误=表存在**（与 relation does not exist 区分）；SVS/304xx 数据在该表，但 pre-prod hza_vpd 数据源账号查不了，需数据 owner 开权限或把表加进数据集。另：第三条探针报 `column "vin" does not exist`（表结构疑似无 vin 列或有解析顺序差异，待有权限后 SELECT * 验证）。
- **未查 prod**（如需 prod 复核走 dataset-prod.json 副本配置，prod 账号权限未知）。

### 引擎坑（表引用规范，本次实测）
- **表引用必须是三段式全限定名** `datalake.kpi.<表名>`：两段式 `datalake.kpi`（想当 schema.table 用）报 `schema "datalake" does not exist`；裸表名（无前缀）报 `relation "<表名>" does not exist`——后者与"表真不存在"同文案，**探表存在性必须用全限定名对照法**（对照组已知表 + 目标表同批）才能定性。
- **错误文本三态定性**：`relation does not exist`=表不存在；`SELECT permission denied`=表存在但无权限；`current transaction is aborted`=遮蔽性错误（非结论依据，须重跑拿真实错误）。
- SQL：ads-kpi workspace `probe-table-exists*.sql` / `probe-kpi-ping.sql` / `probe-vpd-ctl.sql` / `probe-svs-full.sql` / `probe-adssdk-ctl.sql` / `probe-adssdk-svs.sql` / `probe-30404-*.sql` / `probe-vpd-events-dist.sql` / `probe-ads-events-dist*.sql` / `probe-svskpi-*.sql`；归档 `ads-kpi/archive/2026-09-22T11-45-*`~`12-04-*`。



---

## 用例 12：日活（DAU）入湖稳定性与延时上传分析（prod）

### 业务需求
"日活数据因延时上传一般什么时候稳定？"——判定 DAU 看板/汇报的取数时点。

### 方法
9/16（周三）prod Android 固定窗口 [00:00,24:00) 两表合并 DAU，每小时重查同一天（`dau0916-log.jsonl`，64 采样，9/16 00:00~9/18 11:08 后采集会话中断），T+7（09-23）补终值 + `dws_create_time - event_time` 延迟分布互证。

### 结论（2026-09-23，终值 DAU 10,786 / 事件 575,010）
- **稳定时点**：当日末 86.9% 终值；T+1 上午 8 点 91.8%；**T+1 末 95.2%**；T+2 95.7%；**T+7 ≈100%**。口径建议：粗看 T+1 上午、日报 T+1 晚间、精确口径（周报/回归对比/对外）T+7；当日实时值只能当下界（当日末也只 87%）。
- **长尾显著**：T+2.3→T+7 DAU 仍 +4.4%（459 台）——"次日稳定"直觉不成立。
- **入湖连续非快照**：旧表知识"dws_create_time=每日凌晨 ~03:00 快照（T+1）"被证伪——dws_create_time ≈ 实际入湖时刻（分钟级），延迟分布连续无 03:00 阶跃。
- **字段确认（09-23 SELECT * 列探针，prod）**：入湖时间字段即 `dws_create_time`（两表均有；`dws_update_time` 逐条相等 → 一次性写入）；表内时间字段全集仅 event_timestamp / event_time / dws_create_time / dws_update_time。
- **入湖时刻分布**（9/16 事件按入湖日，`dau0916-landday/landhour-prod.sql`）：当日入湖 81.9%、T+1 10.6%（累计 92.5%）、T+2 2.9%（95.4%）、T+3~T+6 共 ~4.4%、T+7 仍 0.2% 不归零（列探针样本中有 event_time=2026-05-20 的记录 9/21 才入湖，延迟 4 个月）；T+1 有午夜冲刷峰（22–24 点事件跨零点入湖 11k 条/h）+ 早高峰重连补传峰；T+2 起长尾集中在白天/傍晚用车时段——离线车再次使用才补传，按"用车日"滴入。**交叉验证：DAU 日志 9/17 00:00 采样 9,716 台 == dws_create_time 当日桶车辆数 9,716 台，逐台吻合**（逐时 DAU 曲线即该字段的累计分布，两证据链闭环）。
- **跨 15 日大规模验证（防单日偏移，`dau15d-converge-prod.sql` + `dau15d-dist-prod.sql`，9/1~9/15 事件日，~860 万事件/条）**：DAU 收敛 15 日均值——**当日末 88.7%（区间 85.0~90.4%）、T+1 末 93.9%、T+2 末 95.4%、T+6 末 98.3%**；9/16 单日形态（90.1%/94.1%/95.7%）落在区间内，无偏移；周末只抬 DAU 规模（+20-30%）与当日入湖占比，**不改变收敛节奏**；**>7d 长尾在已观察 18-22 天的老日期上 3.5~4.1% 且仍缓涨 → T+7 实际只收敛到 ~98-99% 而非 100%**，"终值"是移动目标，精确口径必须显式定义 dws_create_time ≤ 截止时刻；事件级当日入湖占比 15 日均值 79.6%（区间 74.8~82.3%，9/1 为孤立最低日）。两条 SQL 互验：dau_d0 与 a_d0 桶车辆数逐日相等。
- **往前翻 7/8 月：8/12 管线代际切换（`dau2m-converge-prod.sql` + `jul-dist-prod.sql`，7/1~8/31 事件日）**：收敛规律不是管线固有属性——**7 月整月 3,480 万事件 0 条在 13 天内入湖**（21-30d 10.1% / 31-45d 44.9% / >45d 45.0%），8/1~8/11 同样零实时；~5,000 万积压（7 月整月+8 月上半月）在 **≈8/27~9/2 单一窗口集中回补**（逐日首个非零切面模式自洽证明）——旧知识"历史数据延迟 2~6 周"的实体=积压集中放水；**8/12 起准实时管线启用**（当日 80.4%，次日起 77~88%，9 月 88.7% 持续成熟）。含义：跨 8/12 的历史对比必须分段；回补形态可能重现，入湖新鲜度监控要盯"非近期事件日的突增入湖"；8/12 后日事件量与 DAU 持续下降（120~150 万/日→57~64 万/日）待查。
- **两层延迟结构**：81% 事件 ≤6h（最大桶 10–60min 占 52%，0–10min≈0 → 管线有 ~10min 起步固有批次延迟）；10.5% 事件 >1d = 离线车辆上线补传（1–2d 4.6% + >2d 5.9%，涉及 2,000+ 台车）。
- DAU 收敛百分比恒高于事件百分比（车辆任一事件入湖即被计数，迟到事件多为已计数车辆的后续事件）。
- 局限：单日单端样本（9/16 周三 Android prod）；周末曲线形状可能不同但机制不变。

### SQL/资产
`dau0916-prod.sql`（固定窗口 DAU）、`dau0916-delay-prod.sql`（dws_create_time−event_time 延迟分桶，可作入湖新鲜度模板 C1 标准探针）；日志 `dau0916-log.jsonl`；完整报告 `看板/reports/DAU入湖稳定性分析-20260916.md`；归档 `ads-kpi/archive/2026-09-23T06-52-28-dau0916-prod`、`2026-09-23T06-52-36-dau0916-delay-prod`。
