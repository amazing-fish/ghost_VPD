# 各步脚本模板

> 所有脚本用 node + playwright，Edge 路径 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`。Playwright 安装（华为内网，公网 registry 超时）：`npm config set registry https://mirrors.tools.huawei.com/artifactory/api/npm/npm-public/; npm config set strict-ssl false; npm install playwright`。
>
> **⚡ 执行引擎已本地化（2026-09-02）——优先直接用引擎，勿从本文档拷贝代码现场拼装**：
> - 单条：`node scripts/run-sql.js config/dataset.json <sql文件>`（SQL 放 config 的 workspace 目录）
> - 批量：`node scripts/run-sql-batch.js config/dataset.json <sql1> [sql2] ...`（V2 冷启实测 2026-09-02：首条摊导航 8.9s，后续每条 4.4s；无别名 `SELECT *` 走指纹旁路 4.1s/poll 0 检出）
> - 登录：`node scripts/login-capture-cdp.js config/dataset.json`（零命令，见 S1）
> 引擎 vendored 自上游 dataworks-kpi-query（ENGINE_VERSION v2.3-2026-09-04，同步方法见 scripts/ENGINE.md）。**引擎修复只改上游再同步，不直接改本技能 scripts/**。
>
> **本工作区固定参数**（已固化至 `config/dataset.json`）：
> - 环境：`pre-prod` → 域名 `pre-prod.adscloud.huawei.com`，BI iframe 域 `bi.preprod.adscloud.yinwang.com`（prod 对应去掉 `preprod`）
> - 数据集树路径：`['ADS','ADS维测KPI','泊车','hza_vpd']`
> - 数据源路径：`自定义看板/ADS/ADS维测KPI/dws-ads-kpi`（BI iframe 内"第一步：请选择数据源"输入框的值）
> - 数据表：`datalake.kpi.dwi_vpd_event_track_to_adssdk`
> - workspace：`C:/Users/h00899877/Documents/DSH/ads-kpi`（cookies/SQL/归档落盘处）
>
> **表与字段语义（实测）**：
> - hza_vpd 数据集定义涵盖**两张底层物理表**：`datalake.kpi.dwi_vpd_event_track_to_vpd`（event_id 303xx，result 取值 `'success'/'failed'/''`，**失败= `result='failed'``'fail'`**）和 `datalake.kpi.dwi_vpd_event_track_to_adssdk`（event_id 30601~30606，IOS result 取值 `'1'`/`'0'`，Android/harmonyOS 仍为 `'success'`/`'failed'`）。两表均含三端（Android/IOS/harmonyOS）数据，非 IOS-only。⚠️ **列数勘误（2026-09-02 prod SELECT * 实测，修正 08-20 旧记录"两表均 32 列同构"）**：vpd 表 **33 列**（原 32 列清单 + `total_time`）；adssdk 表 **28 列且异构**——前 15 列与 vpd 共享（request_id ~ result），后接 `jump_code, poi, failure_reason, intf_name, service_failure_reason, total_time, trans_id, dws_create_time, dws_update_time, bit_rates, frame_rates, total_frozen_times, drop_delta_frame_nums`，**无** roaddistance/city_code/keyword 等 vpd 特有列。pre-prod 若有差异，以列存在性探针（`SELECT COUNT(列) AS c1, ... FROM <表>` 带别名，实测 ~4.4s）现场校验为准。⚠️ 两表 event_id 范围与 result 取值规则不同，不要混用。
> - `os_name` 取值：`Android` / `IOS` / `harmonyOS`（精确匹配，首字母大写；harmonyOS 偶有 `result='fail'` 而非 'failed'）。
> - `sdk_version` 形如 `1.3.07.137-deepal-pre`，用 `regexp_replace(sdk_version,'-.*$','')` 去后缀；存在非标 `1.3.08.<时间戳>ads` 构建/测试版本号。
> - BI iframe 域：pre-prod = `bi.preprod.adscloud.yinwang.com`。
> - **周活口径（2026-09-15 用户确认）**："本周周活"指**滚动最近 7 天**（now-7d ~ now），非自然周；周活 = vpd+adssdk 两表合并 `COUNT(DISTINCT vin)`，按 `os_name` 过滤，剔除空 vin（Android vpd 表空 vin 事件占比不低，2026-09-14 实测 1240/2025）。参考 SQL：ads-kpi workspace `wau7d-v2-summary.sql`（SUM CASE src 单条出全量指标）+ `wau7d-v2-daily.sql`（窗口内按天 DAU 拆分）。
>
> **⚡ 双环境执行模式（2026-09-10 首次打通，DTS2608110099372 生产观察实证）**：
> - **prod 观察不动 skill 固定 config**——在 workspace 建环境副本配置（如 `dataset-prod.json`：`"env": "prod"` + `"cookies_file": "cookies-prod.json"`，其余字段照抄 config/dataset.json），执行时以副本为 config 参数：`node scripts/run-sql-batch.js <workspace>/dataset-prod.json <sql...>`（登录捕获同理：`node scripts/login-capture-cdp.js <workspace>/dataset-prod.json 9224`，跳 SSO 时在弹窗完成登录，profile 持久免再登）
> - **cookies 捷径**：看板工作区 `C:/Users/h00899877/Documents/DSH/看板/cookies.json`（pre-prod）可直接复制到 ads-kpi workspace 复用（同域登录态，2026-09-09 实测免登录）；prod cookies 缺失时走 CDP 捕获
> - **prod/pre-prod 差异速查**（跨环境跑批必读）：① 版本后缀 `-product`/`-pro` vs `-pre`——版本分组、轨道判定 SQL 按环境适配（如 LIKE '%-pre' 仅匹配 pre-prod）；② 表体量 prod 5500 万 vs pre-prod 790 万（聚合窗口适当收敛）；③ **pre-prod vpd 表无 `vehicle_brand` 列**（prod 才有，2026-09-09 实测 SQL_ERROR）——跨环境复用 SQL 前先跑列存在性探针

## S1: 会话准备（有头登录捕获 Cookie）

> 本步是唯一允许持久化 profile 的步骤，但必须用**专属** profile 目录（如 `.edge-profile`），不得与用户日常 Edge 共用；且严禁 `Stop-Process/kill msedge` 关闭用户已打开的 Edge 窗口。
>
> **推荐（2026-09-02 起引擎本地化）：零命令登录** `node scripts/login-capture-cdp.js config/dataset.json`——自动起/复用持久 CDP Edge（专用 profile + 端口 9224，登录态长期保存），窗口经 explorer 委托必可见，登录一次后免登。下方模板保留为无 CDP 时的原理演示。

```javascript
const { chromium } = require('playwright');
const fs = require('fs');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const USER_DATA = '<workspace>\\.edge-profile'; // 有头持久化 profile
const TARGET = 'https://pre-prod.adscloud.huawei.com/dataworks-frontend/#/layout/self-service-report/dataset-manage';

(async () => {
  const browser = await chromium.launchPersistentContext(USER_DATA, {
    executablePath: EDGE, headless: false, viewport: null, locale: 'zh-CN',
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled', '--start-maximized'],
  });
  const page = browser.pages()[0] || await browser.newPage();
  await page.goto(TARGET, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(()=>{});
  console.log('INFO: please log in the Edge window (40 min limit)');
  const deadline = Date.now() + 40 * 60 * 1000;
  let ok = false;
  while (Date.now() < deadline) {
    await page.waitForTimeout(3000);
    if (page.url().includes('dataworks-frontend')) { ok = true; break; }
  }
  if (!ok) { console.log('ERROR: login timeout'); await browser.close(); process.exit(1); }
  await page.waitForTimeout(6000);
  const cookies = await browser.cookies();
  fs.writeFileSync('cookies.json', JSON.stringify(cookies, null, 2));
  console.log('INFO: saved cookies.json (' + cookies.length + ')');
  await browser.close();
})();
```

**通过条件**：`cookies.json` 含 `AUTH_SESSION_ID`。后台运行（run_in_background），用户登录后检测 URL 回 dataworks-frontend。

## S2: 界面导航（无头 + 注入 Cookie）

> ⚠️ **硬约束**：执行类脚本（Step 2-5）必须用 `chromium.launch()` + `browser.newContext()`（非持久化临时隔离上下文）+ `addCookies`，**不得**用 `launchPersistentContext` 复用 profile 目录。严禁关闭用户已打开的 Edge 窗口（`Stop-Process/kill msedge`）；遇到 profile 冲突（exitCode=21/Singleton lock）只能改用临时上下文，绝不动已有进程。Cookie 已通过 `addCookies` 注入，无需持久化 profile。

```javascript
const { chromium } = require('playwright');
const fs = require('fs');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const TARGET = 'https://pre-prod.adscloud.huawei.com/dataworks-frontend/#/layout/self-service-report/dataset-manage';
const cookies = JSON.parse(fs.readFileSync('cookies.json', 'utf8'));
const TREE_PATH = ['ADS','ADS维测KPI','泊车','hza_vpd']; // 固定

(async () => {
  // 非持久化：每次新开隔离浏览器，不复用 profile，不与已开 Edge 冲突
  const browser = await chromium.launch({
    executablePath: EDGE, headless: true,
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'],
  });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, locale: 'zh-CN' });
  try {
    await context.addCookies(cookies); // 必须在 goto 前
    const page = await context.newPage();
    await page.goto(TARGET, { waitUntil: 'networkidle', timeout: 90000 }).catch(()=>{});
    await page.waitForTimeout(500);
    if (page.url().includes('sso') || page.url().includes('login')) {
      console.log('ERROR: session expired, re-login needed'); process.exit(1);
    }
    // V2 事件驱动导航（替代固定 sleep 6s+2.5s×4+8s≈21s → ~3s）：goto 后等树容器可见
    await page.locator('.ep-tree, .ep-tree-node__content').first().waitFor({ state: 'visible', timeout: 15000 }).catch(()=>{});
    if (page.url().includes('sso') || page.url().includes('login')) {
      console.log('ERROR: session expired, re-login needed'); process.exit(1);
    }
    const parentSegs = TREE_PATH.slice(0, -1);
    for (let i = 0; i < parentSegs.length; i++) {
      await page.locator('.ep-tree-node__content', { hasText: parentSegs[i] }).first().click().catch(e => console.log('WARN: click', parentSegs[i], e.message.slice(0,60)));
      // 点父级后等下一级（或叶子）节点可见，替代固定 2.5s/级
      const next = (i + 1 < parentSegs.length) ? parentSegs[i + 1] : TREE_PATH[TREE_PATH.length - 1];
      const appeared = await page.locator('.ep-tree-node__content', { hasText: next }).first().waitFor({ state: 'visible', timeout: 5000 }).then(() => true).catch(() => false);
      if (!appeared) await page.waitForTimeout(1500); // 懒加载/动画慢于 5s 兜底
    }
    for (let retry = 0; retry < 3; retry++) {
      const ok = await page.evaluate((leaf) => {
        const n = [...document.querySelectorAll('.ep-tree-node__content')].find(x => (x.innerText||'').trim().includes(leaf));
        if (n) { n.click(); return true; } return false;
      }, TREE_PATH[TREE_PATH.length - 1]).catch(() => false);
      if (ok) break;
      await page.locator('.ep-tree-node__content', { hasText: TREE_PATH[TREE_PATH.length - 1] }).first().click().catch(()=>{});
      await page.waitForTimeout(2000);
    }
    // 叶子后 500ms 轮询等 bi iframe（替代固定 8s）
    let biFrame = null;
    for (let i = 0; i < 30 && !biFrame; i++) {
      biFrame = page.frames().find(f => f.url().includes('bi.preprod.adscloud.yinwang.com')); // prod: bi.adscloud.yinwang.com
      if (!biFrame) await page.waitForTimeout(500);
    }
    console.log(biFrame ? 'INFO: bi frame found ' + biFrame.url().slice(0,80) : 'ERROR: no bi frame');
  } finally {
    await context.close().catch(()=>{});
    await browser.close().catch(()=>{});
  }
})();
```

**通过条件**：frames 含 `bi.*.adscloud.yinwang.com/bi/Viewer`。失败→dump `.ep-tree-node` 列表定位。

## S3: SQL 配置与执行

```javascript
// 接 S2 的 browser/page/biFrame，不 close
const SQL = fs.readFileSync('user-sql.sql', 'utf8').replace(/\r\n/g, '\n').trim();
// 校验数据源
const ds = await biFrame.evaluate(() => {
  const inp = [...document.querySelectorAll('input')].find(e => e.placeholder === '第一步：请选择数据源');
  return inp ? inp.value : null;
});
console.log('INFO: datasource =', ds);
// 注入 SQL：setValue 预填 + 键盘 nudge 触发 React onChange（教训 #14：setValue 单独不触发 onChange）
// 完整兜底链（keyboard.type / CJK 处理）见引擎 scripts/run-sql-batch.js V2 注入段
await biFrame.evaluate(sql => { const m = monaco.editor.getModels(); if (m.length) m[0].setValue(sql); }, SQL);
await biFrame.locator('.monaco-editor').first().click().catch(()=>{});
await page.keyboard.press('End');
await page.keyboard.type(' ');
await page.keyboard.press('Backspace');
await page.waitForTimeout(1500);
// 点击刷新数据
await biFrame.locator('button.yh-btn', { hasText: '刷新数据' }).first().click();
// 轮询（V2，1.5s 粒度）：fiber 列名匹配本条 SQL 的 AS 别名
// ⚠️ 禁止 body.innerText 扫描——会命中 Monaco 里的 SQL 文本造成误报（教训 #11）
// ⚠️ 无别名 SQL（SELECT * 探针）不能走别名匹配——引擎用指纹旁路（刷新前网格指纹 vs 轮询指纹）
// 对话框（列变化确认/执行SQL失败）每轮检测处理，见引擎 handleDialogs
const ALIASES = [...SQL.matchAll(/\bAS\s+([A-Za-z_]\w*)\b/gi)].map(m => m[1]);
for (let i = 0; i < 80; i++) { // 120s 上限不变
  await page.waitForTimeout(1500);
  const full = await extractResults(biFrame); // S4 提取器（与引擎同源）
  if (full && full.columns && ALIASES.some(a => full.columns.some(c => c.key === a))) {
    console.log('INFO: results detected at poll', i, 'rows=' + ((full.data)||[]).length); break;
  }
}
```

**通过条件**：结果列名出现且无错误 toast。数据源输入框 placeholder 为 `第一步：请选择数据源`，值应为数据源路径。

## S4: 结果提取（React fiber）

```javascript
const full = await biFrame.evaluate(() => {
  function findFiber(el) {
    const key = Object.keys(el).find(k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance'));
    return key ? el[key] : null;
  }
  let columns = null, data = null;
  const seen = new Set();
  function walk(fiber, depth) {
    if (!fiber || seen.has(fiber) || depth > 50) return;
    seen.add(fiber);
    const props = fiber.memoizedProps || {};
    if (props.root && props.root.props) {
      if (!columns && Array.isArray(props.root.props.columns) && props.root.props.columns.length >= 1) // 教训 #9②：>=1，单列结果（COUNT(*)）不漏
        columns = props.root.props.columns.map(c => ({ key: c.key, label: c.label, colVisible: c.colVisible }));
      if (!data && Array.isArray(props.root.props.data) && props.root.props.data.length > 0)
        data = props.root.props.data.map(row => { const o = {}; for (const k of Object.keys(row)) o[k] = row[k]?.data != null ? row[k].data : row[k]; return o; });
    }
    if (columns && data) return;
    walk(fiber.child, depth+1); walk(fiber.sibling, depth+1);
  }
  // 教训 #9①：结果 fiber 不一定在 .yh-data-grid 子树内（可能在祖先节点），且页面有多个 grid
  // —— 遍历所有 grid 起点，逐个尝试；全部失败再从 document.body 兜底
  for (const g of document.querySelectorAll('.yh-data-grid, .yh-data-grid-wrap, [class*="data-grid"]')) {
    const f = findFiber(g);
    if (f) { walk(f, 0); if (columns && data) break; }
  }
  if (!data) { const f = findFiber(document.body); if (f) walk(f, 0); }
  return { columns, data };
});
fs.writeFileSync('query-full-results.json', JSON.stringify(full, null, 2));
```

**通过条件**：`data.length > 0` 且 `columns.length` = SQL SELECT 列数。失败→见 fiber-paths.md 降级。

## S5: 分析产出

```javascript
const fs = require('fs');
const res = JSON.parse(fs.readFileSync('query-full-results.json', 'utf8'));
const cols = (res.columns||[]).map(c => c.key); // S4 落盘格式是 {columns, data}
const csv = [cols.join(',')].concat((res.data||[]).map(r => cols.map(c => String(r[c] ?? '')).join(','))).join('\n');
fs.writeFileSync('query-results.csv', '\ufeff' + csv); // BOM for Excel
// 统计：总量、各列求和、TopN、失败率 —— 按业务需要计算
```

**通过条件**：CSV 行数 = 结果行数，摘要含总量+各列降序+TopN。

---

## S6: 通用 SQL 执行（引擎脚本，推荐直接复用）

> 2026-09-02 起 S1-S5 的完整工程化实现已 vendoring 到 `scripts/`（上游 dataworks-kpi-query，ENGINE_VERSION v2.3-2026-09-04，同步方法见 scripts/ENGINE.md）。**不要再使用本文档旧版内嵌模板**（其检测/提取存在教训 #9/#11 已判定的问题且未同步修复）。SQL 写入 config 的 workspace 目录下 `.sql` 文件即可（绝对路径参数也已支持，v2.1 起）。

**单条执行**：

```
node scripts/run-sql.js config/dataset.json user-sql.sql
```

- SQL 文件中第一个 `AS <别名>` 自动作为结果检测关键词（fiber 列名匹配，非文本扫描）
- 可选第三参显式指定结果列名关键词

**批量执行（多条 SQL 迭代场景必用）**：

```
node scripts/run-sql-batch.js config/dataset.json probe-schema.sql main.sql followup.sql
```

- 首条摊导航（V2 事件驱动，冷启实测 8.9s），后续每条 4.4s（1.5s 轮询 + fiber 列名匹配）
- 无别名 `SELECT *`（探表结构）走指纹旁路（冷启实测 4.1s，poll 0 检出；刷新前网格指纹比对，不再空转 120s）
- 每条自动归档到 `workspace/archive/<时间戳>/`（SQL + full-results.json + CSV）
- 空结果（"暂无数据"）连续 2 次轮询确认才判 EMPTY（防列变化过渡期误判）

**结果检测规则**：带 AS 别名 → fiber 列名匹配（快路径）；无别名 → 指纹旁路；引擎自动选择，无需手工指定。

**错误 toast / 对话框**：引擎每轮轮询自动检测"执行SQL失败/SQL错误"错误框与"列变化/清空/是否"确认框（精准文本匹配，教训 #12），失败即报错退出并保留现场。

### 关键经验（实测教训）

1. **禁止杀已有 Edge 进程**：用户可能正在用 Edge，`Get-Process msedge | Stop-Process` 会误伤。改用**每次独立 profile 目录**（`Date.now()` 后缀），运行结束 `fs.rmSync` 清理。
2. **Profile 锁残留**：上一次脚本启动的 Edge 若未完全退出，复用同一 `--user-data-dir` 会报 `Target page, context or browser has been closed`（exitCode=21）。独立目录彻底规避。
3. **adssdk 表 result 三端不一致**：IOS=`'1'`/`'0'`，Android/harmonyOS=`'success'`/`'failed'`（harmonyOS 偶有 `'fail'`）。按端过滤失败时需分别处理：IOS 用 `result='1'`，Android/harmonyOS 用 `result='failed'`。
4. **hza_vpd 数据集涵盖 vpd + adssdk 两表、三端全量**：数据集定义非 IOS-only，包含 `dwi_vpd_event_track_to_vpd`（303xx）和 `dwi_vpd_event_track_to_adssdk`（306xx）两张底层表，均含 Android/IOS/harmonyOS。导航到 hza_vpd 打开 BI Viewer 入口后，可覆盖 SQL 查任意一张表（数据源 `dws-ads-kpi` 有权访问两表），SQL 内容不受数据集默认定义限制。
5. **修复验证需时间窗口切分**：修复版版本号相同但含修复前旧数据。必须用 `event_time >= '截止日期'` 分离修复前/后，否则全量重复率被旧数据拉高（实测 5.62% vs 修复后 0.49%）。
6. **BI Viewer 预览行数限制**：默认 200 行。若结果超 200 行需在界面调整预览行数输入框。聚合查询（GROUP BY）结果通常 < 200 行，无此问题。
7. **SQL 方言是 PostgreSQL flavour（非 ODPS，2026-08-29 实测；date_trunc 09-02 补充实测）**：`PERCENTILE_APPROX` 不存在（用 `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY CAST(col AS NUMERIC))`）；无 `DOUBLE` 类型（用 `NUMERIC`）；`DATE_FORMAT` 不可用（按天/周分组用 `date_trunc('day', CAST(ts AS TIMESTAMP))`——2026-09-02 prod 实测可用，或 `SUBSTR(CAST(ts AS VARCHAR),1,10)`）；时长类字段（如 total_time）多为 varchar，比较/聚合前须 `CAST(col AS NUMERIC)`；`INFORMATION_SCHEMA` 不可访问（探表结构用 `SELECT * FROM <表> LIMIT n`，查列不存在会弹"执行SQL失败 column does not exist"错误框）。
8. **批量执行摊薄导航开销**：每条 SQL 独立起浏览器要付 ~26s 导航（goto+树+monaco），多条 SQL 迭代场景用批量执行器 `scripts/run-sql-batch.js`（2026-09-02 已本地化，V2 引擎，上游 dataworks-kpi-query）：首条摊导航 8.9s（事件驱动，V2 冷启实测），后续每条 4.4s（1.5s 轮询）；无别名 `SELECT *` 走指纹旁路 4.1s；无依赖探针合批执行再省一次导航。pre-prod 并发跑会 SSO 顶号只能串行，批内串行是唯一提速手段。
9. **fiber 提取两个坑（2026-08-29 实测）**：① 结果数据的 fiber 节点不一定在 `.yh-data-grid` 元素子树内，轮询/提取必须带 body 兜底遍历（否则永远检测不到）；② 提取器要求 `columns.length >= 2` 会漏单列结果（如 `SELECT COUNT(*)`），应为 >= 1。
10. **loading 检测不可做轮询门控**：`if (loading) continue` 在 pre-prod 会卡真（spinner 元素常驻 offsetParent 非 null），导致 240s 空转。loading 仅日志，结果判定靠 fiber 列名与 SQL 别名匹配。
11. **结果检测关键词不可扫 monaco 文本**：`body.innerText.includes(别名)` 会命中编辑器里的 SQL 文本造成误报"已出结果"。必须用 fiber 列名匹配。
12. **对话框处理须文本精准匹配**：不要盲点所有对话框的"确定"（可能误关无关窗口，如"新建报告"）；仅对"执行SQL失败/SQL错误"和"列变化/清空/是否"特征文本点击。
13. **引擎单源（2026-09-02，ENGINE_VERSION 现 v2.2-2026-09-03）**：本文档 S2-S6 曾出现"经验已修、代码未跟"落后（#11 innerText 检测、#9 提取器两坑、无对话框处理、固定 sleep 导航、S5 读写格式不一致、无批量）。已将上游引擎 vendoring 到 `scripts/`（见 scripts/ENGINE.md）：**引擎修复只改上游再一键同步，不直接改本技能 scripts/**；本文件只保留原理节选，执行一律优先引擎。引擎级防错条目主体见上游 dataworks-kpi-query/AGENTS.md（44 条，含 #23 loading 卡真、#29 evaluate 多参数吞错、#31 无别名空转→指纹旁路、#33 冷启瓶颈翻转与知识沉淀、#34 batch 绝对路径解析、#35 错误对话框宽匹配（类名不可信）、#36 超时兜底防误提取（NO_RESULT）、#37 网络探针盲区、#38 树导航脆弱、#39 引擎两线分叉处置、#44 引擎挂死 agent 侧处置（session-log 判活+kill 重跑，⛔禁杀浏览器，挂钩 SKILL.md 硬约束 6）等）。
14. **错误对话框宽匹配 + 超时不提取（v2.2，beta 2026-09-03 prod 冷启教训）**：①错误弹层类名在 prod 与 pre-prod 不同——不可按类名白名单匹配，按"错误特征文本（含 does not exist/不存在等 PG 报错）+ 可见弹层"识别，SQL_ERROR 输出完整对话框文本，agent 收到即改 SQL 转向勿重试；②轮询超时未检出 → NO_RESULT，不把网格数据当结果归档（默认预览误当结果=错误数据，比无数据更危险）；③session-log.jsonl 时间线自动记录阶段耗时。详见上游 AGENTS.md #35/#36。

---

## S7: 修复回归判定模板（2026-09-10，DTS2608110099372 实证提炼）

> 适用场景：SDK/云端**码值类修复**回归（修复改变失败原因/错误码的上报内容，如"failure_reason 改用车云具体错误码"）。完整案例见 business-cases.md 用例 4（81018）/用例 5（99372）。

### 判定锚点优先级（重要）

**SNAPSHOT 构建时间戳 > 码值按天首现/止步日 > 版本首现日**

1. **构建时间戳**（最可靠）：Android SNAPSHOT 版本号内嵌毫秒构建时间（如 `1.3.08.01787387501433ads-SNAPSHOT-qiankun-pre` = 08-22 16:49 构建），与 MR 合入时刻精确比对，判定该构建是否含修复
2. **码值首现/止步日**（次可靠）：修复特征码（如 -1/11000/映射码）按天首现日 vs 旧码止步日，与 MR 合入日衔接即行为切换证据；本地码精确止步日比特征码首现更有说服力（排除偶发）
3. **版本首现日**（最不可靠）：版本号可横跨修复前后（同版本号热更后行为切换）、构建可早于首现很久——仅作辅助

**同版本号行为按天切换的解读**：某版本号在修复合入日后开始报修复特征码 = 修复以热更/新构建铺开而 `sdk_version` 字段未变（勿误判为"版本号相同行为矛盾"）。切换时刻与 MR 合入日吻合本身即修复生效佐证。

### 模板 1：修复特征码 vs 旧码按天矩阵（一屏判切换日）

```sql
SELECT
  os_name,
  SUBSTR(CAST(event_time AS VARCHAR), 1, 10) AS day,
  SUM(CASE WHEN failure_reason IN ('<修复特征码1>', '<修复特征码2>') THEN 1 ELSE 0 END) AS fix_code_cnt,
  SUM(CASE WHEN failure_reason IN ('<旧码1>', '<旧码2>') THEN 1 ELSE 0 END) AS old_code_cnt,
  SUM(CASE WHEN failure_reason IS NULL OR TRIM(CAST(failure_reason AS VARCHAR)) = '' THEN 1 ELSE 0 END) AS empty_cnt,
  COUNT(*) AS failed_total
FROM datalake.kpi.<表>
WHERE event_id = '<事件>' AND result = 'failed' AND event_time >= '<起点>'
GROUP BY os_name, SUBSTR(CAST(event_time AS VARCHAR), 1, 10)
ORDER BY os_name, day
```

读法：old→fix 的切换日与 MR 合入日衔接 = 修复生效；empty 列全程监控提单空值现象；残留 old_code 归因到版本（模板 2）再判是否存量旧版（预期内）。

### 模板 2：端 × 版本 × 码交叉（载体版本行为 + 残留归因）

```sql
SELECT
  os_name,
  sdk_version AS pv_ver,
  CASE WHEN failure_reason IS NULL OR TRIM(CAST(failure_reason AS VARCHAR)) = '' THEN '(empty)'
       ELSE CAST(failure_reason AS VARCHAR) END AS pv_reason,
  COUNT(*) AS pv_cnt, MIN(event_time) AS pv_first, MAX(event_time) AS pv_last
FROM datalake.kpi.<表>
WHERE event_id = '<事件>' AND result = 'failed' AND event_time >= '<修复合入日前几天>'
GROUP BY os_name, sdk_version, 3
ORDER BY os_name, pv_ver, pv_cnt DESC
```

读法：修复载体版本（构建时间戳 > 合入时刻）失败 100% 报修复特征码 = 通过；报旧码的版本逐个核对构建时间/轨道，存量旧版或非目标轨道 = 预期内残留，如实归因勿笼统判"未修复"。

### 生产铺开状态判定（双环境回归的 prod 侧）

prod 侧跑模板 1/2：修复特征码全程 0 + 活跃版本全为存量旧版 = **修复未上生产**（不是修复失效）；此时结论写"prod 未铺开 + 复验触发条件=生产出现载体版本流量（列出版本特征，如 QIANKUNT10.B010 / 4.x 新线 / 特定版本号以上）"，后续复跑同两条 SQL 即可判定。
