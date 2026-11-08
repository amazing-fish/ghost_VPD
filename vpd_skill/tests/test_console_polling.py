"""Exercise the shipped page functions with synthetic DOM/requests; no device or browser claims."""
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).parents[1] / "vpdtest" / "console" / "page.html"


def run_page(body, real_api=False, with_status=False):
    node = shutil.which("node")
    if not node:
        pytest.skip("Console page behavior tests require Node.js")
    page = PAGE.read_text(encoding="utf-8")
    constants = page[page.index("  const STATE ="):page.index("  const reduceMotion")]
    element = page[page.index("  function el("):page.index("  const log =")]
    api = page[page.index("  async function api("):page.index("  async function send(")]
    status = page[page.index("  async function pollStatus()"):page.index("  // ---- 运行进度 ----")]
    functions = page[page.index("  const hhmmss ="):page.index("  pollStatus(); pollRuns();")]
    harness = r"""
const assert = require('node:assert/strict');
class Node {
  constructor() { this.children = []; this.attributes = {}; this.dataset = {}; this.parentElement = {}; this.style = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(k, v) { this.attributes[k] = v; }
  addEventListener() {}
  querySelector(selector) { return selector === 'button' ? $('submit-button') : null; }
  get textContent() { return this.children.map(x => x instanceof Node ? x.textContent : x).join(' '); }
  set textContent(value) { this.children = [value]; }
}
const nodes = new Map(), $ = id => {
  if (!nodes.has(id)) nodes.set(id, new Node());
  return nodes.get(id);
};
const document = { createElement: () => new Node(), activeElement: null };
const TOKEN = 'synthetic-test-token';
let selected = null, lastRuns = '';
let inputOn = false, statusPending = false, streaming = true;
const screen = new Node(), img = new Node(), startStream = () => { streaming = true; };
const pending = [];
function request(path, signal) {
  return new Promise((resolve, reject) => pending.push({path, signal, resolve, reject}));
}
const sample = id => ({run_id: id, title: id, kind: 'run', kind_label: '用例', state: 'finished',
  updated: 1794124800, finished: true, verdict: 'passed', tail: [],
  verdict_detail: {verdict: 'passed', label: '通过'}});
"""
    harness += constants + element + (api if real_api else "const api = request;\n") + functions
    if with_status:
        harness += status
    harness += "\nlet completed = false;\n(async () => {\n" + body + "\n})()"
    harness += ".then(() => { completed = true; }).catch(e => { console.error(e); process.exit(1); });"
    harness += "\nprocess.on('beforeExit', () => { if (!completed) { console.error('Unfinished test'); process.exitCode = 1; } });"
    subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True, timeout=15)


@pytest.mark.parametrize("old_fails", [False, True])
def test_old_detail_response_cannot_replace_new_selection(old_fails):
    run_page("""
selected = 'run-A'; const a = pollDetail();
selected = 'run-B'; const b = pollDetail();
assert.equal(pending[0].signal.aborted, true);
pending[1].resolve(sample('run-B')); await b;
const before = $('detail').textContent;
""" + ("pending[0].reject(new Error('old request failed'));" if old_fails else
        "pending[0].resolve(sample('run-A'));") + """
await a;
assert.equal($('detail').textContent, before);
assert.match(before, /run-B/);
assert.match($('detail-status').textContent, /刷新正常/);
""")


def test_reselect_same_run_rejects_first_generation():
    run_page("""
selected = 'run-A'; const a1 = pollDetail();
selected = 'run-B'; const b = pollDetail();
selected = 'run-A'; const a2 = pollDetail();
pending[2].resolve({...sample('run-A'), title: 'latest A'}); await a2;
pending[0].resolve({...sample('run-A'), title: 'old A'}); await a1;
pending[1].reject(new Error('old B')); await b;
assert.match($('detail').textContent, /latest A/);
assert.doesNotMatch($('detail').textContent, /old A|old B/);
""")


@pytest.mark.parametrize("poll,setup,payload", [
    ("pollDetail", "selected = 'run-A';", "sample('run-A')"),
    ("pollRuns", "", "{runs: [sample('run-A')]}"),
])
def test_same_resource_polls_do_not_overlap(poll, setup, payload):
    run_page(f"""
{setup}
const first = {poll}(); await {poll}(); await {poll}();
assert.equal(pending.length, 1);
pending[0].resolve({payload}); await first;
const next = {poll}(); assert.equal(pending.length, 2);
pending[1].resolve({payload}); await next;
""")


@pytest.mark.parametrize("poll,surface,payload,setup", [
    ("pollRuns", "runs", "{runs: [sample('run-A')]}", ""),
    ("pollDetail", "detail", "sample('run-A')", "selected = 'run-A';"),
])
def test_failed_refresh_preserves_snapshot_and_recovery_clears_warning(poll, surface, payload, setup):
    run_page(f"""
{setup}
const first = {poll}(); pending[0].resolve({payload}); await first;
const snapshot = $('{surface}').textContent, stamp = freshness.{surface}.at;
const failure = {poll}(); pending[1].reject(new Error('network unavailable')); await failure;
assert.equal($('{surface}').textContent, snapshot);
assert.equal(freshness.{surface}.at, stamp);
assert.match($('{surface}-status').textContent, /历史快照.*不代表当前状态/);
assert.match($('{surface}-status').textContent, /network unavailable/);
const recovery = {poll}(); pending[2].resolve({payload}); await recovery;
assert.equal($('{surface}-status').attributes['data-stale'], 'false');
assert.match($('{surface}-status').textContent, /刷新正常/);
""")


def test_selection_clears_previous_snapshot_before_fetch_and_initial_failure():
    run_page("""
selected = 'run-A'; const first = pollDetail(); pending[0].resolve(sample('run-A')); await first;
selectRun('run-B');
assert.doesNotMatch($('detail').textContent, /run-A/);
assert.match($('detail').textContent, /正在读取 run-B/);
assert.equal(freshness.detail.at, null);
pending.find(r => r.path === '/api/runs').resolve({runs: []});
pending.find(r => r.path === '/api/runs/run-B').reject(new Error('HTTP 404'));
await new Promise(resolve => setImmediate(resolve));
assert.match($('detail-status').textContent, /尚无可用快照/);
assert.match($('detail-status').textContent, /HTTP 404/);
""")


def test_snapshot_ages_without_relabeling_product_verdict():
    run_page("""
selected = 'run-A'; Date.now = () => 100000;
const first = pollDetail(); pending[0].resolve(sample('run-A')); await first;
Date.now = () => 113001; showFreshness('detail');
assert.match($('detail-status').textContent, /历史快照/);
assert.match($('detail').textContent, /通过/);
const card = runItem({...sample('run-A'), state: 'stale', finished: false, verdict: 'pending'});
assert.match(card.textContent, /执行状态未知/);
assert.match(card.textContent, /待确认/);
assert.match(card.textContent, /2026/);
assert.match(card.textContent, /日志文件更新/);
assert.doesNotMatch(card.textContent, /进行中/);
""")


def test_response_identity_mismatch_does_not_show_other_run():
    run_page("""
selected = 'run-A'; const first = pollDetail();
pending[0].resolve(sample('run-B')); await first;
assert.doesNotMatch($('detail').textContent, /run-B/);
assert.match($('detail-status').textContent, /标识异常/);
""")


def test_malformed_list_does_not_advance_snapshot_time_or_prevent_recovery():
    run_page("""
Date.now = () => 100000;
const first = pollRuns(); pending[0].resolve({runs: [sample('run-A')]}); await first;
const before = $('runs').textContent, signature = lastRuns;
Date.now = () => 102000;
const bad = pollRuns(); pending[1].resolve({runs: [null]}); await bad;
assert.equal(freshness.runs.at, 100000);
assert.equal($('runs').textContent, before);
assert.equal(lastRuns, signature);
assert.match($('runs-status').textContent, /历史快照/);
const retry = pollRuns(); pending[2].resolve({runs: [sample('run-A')]}); await retry;
assert.equal(freshness.runs.at, 102000);
assert.match($('runs-status').textContent, /刷新正常/);
""")


def test_api_timeout_covers_response_body_and_recovers():
    run_page("""
let cancelTimer, cleared = 0, first = true;
global.setTimeout = callback => { cancelTimer = callback; return 1; };
global.clearTimeout = () => { cleared++; };
global.fetch = async (path, options) => {
  assert.equal(options.headers['X-VPD-Token'], TOKEN);
  if (!first) return {ok: true, json: async () => ({runs: []})};
  first = false;
  return {ok: true, json: () => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true});
  })};
};
const slow = api('/api/runs');
await Promise.resolve(); cancelTimer();
await assert.rejects(slow, /读取超时/);
assert.deepEqual(await api('/api/runs'), {runs: []});
assert.equal(cleared, 2);
""", real_api=True)


def test_api_cancels_superseded_request_and_removes_listener():
    run_page("""
let removed = 0;
const external = new AbortController();
const original = external.signal.removeEventListener.bind(external.signal);
external.signal.removeEventListener = (...args) => { removed++; original(...args); };
global.fetch = (path, options) => new Promise((resolve, reject) => {
  options.signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true});
});
const a = api('/api/runs/run-A', external.signal); external.abort();
await assert.rejects(a, /已取消/);
assert.equal(removed, 1);
""", real_api=True)


def test_status_refresh_is_serial_and_failure_disables_input_until_recovery():
    run_page("""
const good = {input: true, device: 'synthetic-device', frames: {status: 'live', fps: 12}};
const first = pollStatus(); await pollStatus();
assert.equal(pending.length, 1);
pending[0].resolve(good); await first;
assert.equal(inputOn, true);
assert.equal($('submit-button').disabled, false);
const failed = pollStatus(); pending[1].reject(new Error('offline')); await failed;
assert.equal(inputOn, false);
for (const id of ['b-back', 'b-home', 'b-wake', 'submit-button']) assert.equal($(id).disabled, true);
assert.match($('p-input').textContent, /状态未知/);
const recovered = pollStatus(); pending[2].resolve(good); await recovered;
assert.equal(inputOn, true);
for (const id of ['b-back', 'b-home', 'b-wake', 'submit-button']) assert.equal($(id).disabled, false);
""", with_status=True)
