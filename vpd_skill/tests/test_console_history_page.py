"""Actual page-function tests with synthetic DOM and history; not browser/device QA."""

import pytest
from test_console_polling import PAGE, run_page


def test_history_controls_have_labels_and_existing_mobile_layout():
    page = PAGE.read_text(encoding="utf-8")
    for fragment in [
        'id="runs-recent" type="button" aria-pressed="true"',
        'id="runs-history" type="button" aria-pressed="false"',
        'for="history-search"', 'for="history-kind"',
        'id="history-count" class="hint" role="status"',
        '@media (max-width: 760px)', 'flex-wrap: wrap',
        'select:focus-visible', '不是同一时刻的快照',
    ]:
        assert fragment in page


def test_history_enter_loads_once_and_lifecycle_never_polls_history():
    run_page(r"""
const initial = pollRuns();
assert.equal(pending[0].path, '/api/runs');
pending[0].resolve({runs: [sample('run-live')]}); await initial;
assert.match($('runs-at').textContent, /最近 1 条/);
const entering = $('runs-history').dispatch('click');
assert.equal(pending[1].path, '/api/runs?scope=all');
assert.equal($('runs-history').attributes['aria-pressed'], 'true');
assert.equal($('history-controls').hidden, false);
assert.equal($('history-refresh').disabled, true);
pending[1].resolve({runs: [sample('run-old')]}); await entering;
assert.equal($('history-refresh').disabled, false);
assert.match($('runs-status').textContent, /手动历史读取.*不代表当前状态/);
const stamp = freshness.runs.at;
await $('runs-history').dispatch('click');
await pollRuns();
for (const timer of intervals.filter(t => t.ms === 3000 || t.ms === 1000)) await timer.callback();
assert.equal(pending.length, 2);
const visible = document.dispatch('visibilitychange');
assert.equal(pending[2].path, '/api/status');
pending[2].resolve({frames: {}}); await visible;
assert.equal(pending.filter(r => r.path === '/api/runs?scope=all').length, 1);
const recent = $('runs-recent').dispatch('click');
assert.equal(pending[3].path, '/api/runs');
pending[3].resolve({runs: [sample('run-live')]}); await recent;
await $('runs-history').dispatch('click');
assert.equal(pending.length, 4);
assert.equal(freshness.runs.at, stamp);
assert.match($('runs').textContent, /run-old/);
""", with_lifecycle=True)


def test_search_complete_history_before_limit_and_show_more_is_local():
    run_page(r"""
Date.now = () => 100000;
const rows = Array.from({length: 121}, (_, i) => ({...sample('run-' + i),
  title: i === 120 ? 'Needle [a.*]' : 'Synthetic', case_title: i === 119 ? 'Old CASE title' : ''}));
const first = setRunScope('all'); pending[0].resolve({runs: rows}); await first;
assert.equal($('runs').children.length, 40);
assert.match($('history-count').textContent, /匹配 121 \/ 总计 121 条.*已显示 40/);
Date.now = () => 200000;
await $('history-more').dispatch('click');
assert.equal($('runs').children.length, 80);
await $('history-more').dispatch('click');
assert.equal($('runs').children.length, 120);
await $('history-more').dispatch('click');
assert.equal($('runs').children.length, 121);
assert.equal($('history-more').hidden, true);
$('history-search').value = '[A.*]'; await $('history-search').dispatch('input');
assert.match($('runs').textContent, /run-120/);
assert.match($('history-count').textContent, /匹配 1 \/ 总计 121 条.*已显示 1/);
assert.equal(historyLimit, 40);
$('history-search').value = 'old case'; await $('history-search').dispatch('input');
assert.match($('runs').textContent, /run-119/);
$('history-search').value = 'RUN-118'; await $('history-search').dispatch('input');
assert.match($('runs').textContent, /run-118/);
$('history-search').value = ''; await $('history-search').dispatch('input');
assert.equal($('runs').children.length, 40);
assert.equal(freshness.runs.at, 100000);
assert.equal(pending.length, 1);
showFreshness('runs');
assert.match($('runs-status').textContent, /手动历史读取/);
assert.doesNotMatch($('runs-status').textContent, /刷新正常|刷新中断/);
""")


def test_kind_and_query_intersection_counts_tied_rows_preserves_server_order():
    run_page(r"""
const rows = Array.from({length: 105}, (_, i) => ({...sample('entry-' + i),
  kind: i < 50 ? 'run' : (i < 100 ? 'suite' : 'custom'), kind_label: i < 50 ? '用例' : '合成类型',
  title: i % 2 ? 'Match' : 'Else', updated: 123}));
const first = setRunScope('all'); pending[0].resolve({runs: rows}); await first;
assert.deepEqual($('history-kind').children.map(option => option.value), ['', 'custom', 'run', 'suite']);
$('history-kind').value = 'suite'; await $('history-kind').dispatch('change');
assert.match($('history-count').textContent, /匹配 50 \/ 总计 105 条.*已显示 40/);
assert.equal($('runs').children[0].children[0].dataset.id, 'entry-50');
await $('history-more').dispatch('click');
assert.equal($('runs').children.length, 50);
assert.equal($('runs').children[49].children[0].dataset.id, 'entry-99');
$('history-search').value = 'mAtCh'; await $('history-search').dispatch('input');
assert.match($('history-count').textContent, /匹配 25 \/ 总计 105 条.*已显示 25/);
assert.equal($('runs').children[0].children[0].dataset.id, 'entry-51');
assert.equal($('history-more').hidden, true);
$('history-kind').value = 'custom'; await $('history-kind').dispatch('change');
assert.match($('history-count').textContent, /匹配 2 \/ 总计 105 条.*已显示 2/);
assert.equal(pending.length, 1);
""")


def test_empty_archive_and_no_match_have_distinct_messages():
    run_page(r"""
const first = setRunScope('all'); pending[0].resolve({runs: []}); await first;
assert.match($('runs').textContent, /历史归档中还没有运行记录/);
assert.match($('history-count').textContent, /匹配 0 \/ 总计 0/);
const next = refreshHistory(); pending[1].resolve({runs: [sample('run-A')]}); await next;
$('history-search').value = 'missing'; await $('history-search').dispatch('input');
assert.match($('runs').textContent, /没有匹配的历史记录/);
assert.doesNotMatch($('runs').textContent, /还没有运行记录/);
assert.match($('history-count').textContent, /匹配 0 \/ 总计 1/);
assert.equal(selected, null);
assert.equal($('history-more').hidden, true);
""")


@pytest.mark.parametrize("old_fails", [False, True])
def test_recent_history_recent_generation_rejects_old_success_or_failure(old_fails):
    run_page("""
Date.now = () => 100000;
const recent1 = pollRuns();
const history = setRunScope('all');
assert.equal(pending[0].signal.aborted, true);
const recent2 = setRunScope('recent');
assert.equal(pending[1].signal.aborted, true);
Date.now = () => 103000;
pending[2].resolve({runs: [sample('run-new')]}); await recent2;
const text = $('runs').textContent, stamp = freshness.runs.at;
""" + ("""
pending[0].reject(new Error('obsolete recent failure'));
pending[1].reject(new Error('obsolete history failure'));
""" if old_fails else """
pending[0].resolve({runs: [sample('run-old')]});
pending[1].resolve({runs: [sample('run-history')]});
""") + """
await recent1; await history;
assert.equal($('runs').textContent, text);
assert.equal(freshness.runs.at, stamp);
assert.equal(freshness.runs.error, '');
assert.equal(runViews.all.rows, null);
assert.equal(runViews.all.at, null);
assert.equal(runsRequest, null);
assert.match(text, /run-new/);
""")


@pytest.mark.parametrize("old_fails", [False, True])
def test_old_history_completion_cannot_unlock_or_replace_new_history_read(old_fails):
    run_page("""
const history1 = setRunScope('all');
const recent = setRunScope('recent');
const history2 = setRunScope('all');
assert.equal(pending[0].signal.aborted, true);
assert.equal(pending[1].signal.aborted, true);
const active = runsRequest;
""" + ("pending[0].reject(new Error('old history'));" if old_fails else
        "pending[0].resolve({runs: [sample('old-history')]});") + """
await history1;
assert.equal(runsRequest, active);
assert.equal($('history-refresh').disabled, true);
assert.equal(runViews.all.rows, null);
await refreshHistory(); await pollRuns();
assert.equal(pending.length, 3);
pending[1].resolve({runs: [sample('old-recent')]}); await recent;
pending[2].resolve({runs: [sample('new-history')]}); await history2;
assert.match($('runs').textContent, /new-history/);
assert.doesNotMatch($('runs').textContent, /old-/);
assert.equal($('history-refresh').disabled, false);
""")


def test_repeated_history_refresh_is_serial_and_preserves_same_scope_snapshot_on_error():
    run_page("""
Date.now = () => 100000;
const first = setRunScope('all');
await refreshHistory(); await $('history-refresh').dispatch('click');
assert.equal(pending.length, 1);
pending[0].resolve({runs: [sample('history-A')]}); await first;
const snapshot = $('runs').textContent;
Date.now = () => 200000;
const refresh = $('history-refresh').dispatch('click');
await $('history-refresh').dispatch('click'); await refreshHistory();
assert.equal(pending.length, 2);
pending[1].reject(new Error('history unavailable')); await refresh;
assert.equal($('runs').textContent, snapshot);
assert.equal(freshness.runs.at, 100000);
assert.match($('runs-status').textContent, /刷新失败.*历史快照.*不代表当前状态/);
assert.match($('runs-status').textContent, /history unavailable/);
const recent = setRunScope('recent');
assert.doesNotMatch($('runs').textContent, /history-A/);
pending[2].resolve({runs: [sample('recent-B')]}); await recent;
await setRunScope('all');
assert.equal($('runs').textContent, snapshot);
assert.equal(freshness.runs.at, 100000);
assert.match($('runs-status').textContent, /history unavailable/);
assert.equal(pending.length, 3);
const retry = refreshHistory(); pending[3].resolve({runs: [sample('history-C')]}); await retry;
assert.equal(freshness.runs.at, 200000);
assert.equal($('runs-status').attributes['data-stale'], 'false');
assert.doesNotMatch($('runs-status').textContent, /history unavailable|刷新正常/);
""")


def test_initial_history_failure_has_no_snapshot_and_does_not_show_recent_as_history():
    run_page("""
const recent = pollRuns(); pending[0].resolve({runs: [sample('recent-A')]}); await recent;
const history = setRunScope('all'); pending[1].reject(new Error('HTTP 500')); await history;
assert.equal(freshness.runs.at, null);
assert.doesNotMatch($('runs').textContent, /recent-A|还没有运行记录/);
assert.match($('runs-status').textContent, /HTTP 500.*尚无可用快照/);
assert.match($('history-count').textContent, /暂不能统计/);
assert.equal($('history-refresh').disabled, false);
const retry = refreshHistory(); pending[2].resolve({runs: []}); await retry;
assert.match($('runs').textContent, /历史归档中还没有运行记录/);
assert.notEqual(freshness.runs.at, null);
""")


def test_history_selection_filters_and_refresh_do_not_replace_independent_detail():
    run_page("""
Date.now = () => 100000;
const first = setRunScope('all'); pending[0].resolve({runs: [sample('run-A'), sample('run-B')]}); await first;
Date.now = () => 105000;
await $('runs').children[0].children[0].dispatch('click');
assert.equal(selected, 'run-A');
assert.equal(pending.length, 2);
assert.equal(pending[1].path, '/api/runs/run-A');
pending[1].resolve(sample('run-A')); await new Promise(resolve => setImmediate(resolve));
assert.equal(freshness.detail.at, 105000);
assert.equal(freshness.runs.at, 100000);
assert.match($('detail-status').textContent, /刷新正常/);
assert.match($('runs-status').textContent, /手动历史读取/);
$('history-search').value = 'run-B'; await $('history-search').dispatch('input');
assert.equal(selected, 'run-A');
assert.match($('detail').textContent, /run-A/);
assert.equal(pending.length, 2);
Date.now = () => 110000;
const history = refreshHistory(); pending[2].resolve({runs: [sample('run-B')]}); await history;
assert.equal(selected, 'run-A');
assert.match($('detail').textContent, /run-A/);
assert.equal(freshness.detail.at, 105000);
const detail = pollDetail(); pending[3].reject(new Error('HTTP 404')); await detail;
assert.equal(selected, 'run-A');
assert.match($('detail').textContent, /run-A/);
assert.match($('detail-status').textContent, /HTTP 404/);
assert.match($('runs-status').textContent, /手动历史读取/);
const recent = setRunScope('recent'); pending[4].resolve({runs: [sample('run-C')]}); await recent;
assert.equal(selected, 'run-A');
assert.match($('detail').textContent, /run-A/);
""")


def test_malformed_history_preserves_snapshot_and_corrupt_record_keeps_verdict():
    run_page(r"""
const corrupt = {...sample('run-corrupt'), state: 'unknown', total: 5, done: 2,
  progress_reliable: false, read_errors: [{source: 'events.jsonl', reason: 'invalid_record', count: 2}],
  validation_level: 'offline_replay', informal: true, asset_review: {state: 'pending'}};
Date.now = () => 100000;
const first = setRunScope('all'); pending[0].resolve({runs: [corrupt]}); await first;
const snapshot = $('runs').textContent, signature = lastRuns;
assert.match(snapshot, /记录异常.*进度未知/);
assert.match(snapshot, /通过/);
assert.match(snapshot, /离线回放（不代表真机）.*非正式运行.*尚未审核/);
assert.doesNotMatch(snapshot, /2\/5/);
Date.now = () => 200000;
const invalid = refreshHistory(); pending[1].resolve({runs: [null]}); await invalid;
assert.equal($('runs').textContent, snapshot);
assert.equal(lastRuns, signature);
assert.equal(freshness.runs.at, 100000);
assert.match($('runs-status').textContent, /运行列表格式异常/);
assert.equal(runViews.all.rows[0].run_id, 'run-corrupt');
""")


def test_local_selection_preserves_card_focus_without_reading_list_again():
    run_page("""
const first = setRunScope('all'); pending[0].resolve({runs: [sample('run-A'), sample('run-B')]}); await first;
const card = $('runs').children[1].children[0];
card.focus(); await card.dispatch('click');
assert.equal(document.activeElement.dataset.id, 'run-B');
assert.equal(document.activeElement.attributes['aria-pressed'], 'true');
assert.equal(pending.length, 2);
pending[1].resolve(sample('run-B'));
await new Promise(resolve => setImmediate(resolve));
assert.equal(pending.filter(r => r.path === '/api/runs?scope=all').length, 1);
""")


def test_refresh_keeps_filter_when_kind_disappears_without_changing_selection():
    run_page(r"""
const first = setRunScope('all');
pending[0].resolve({runs: [{...sample('suite-A'), kind: 'suite', kind_label: '用例集'}, sample('run-B')]});
await first;
$('history-kind').value = 'suite'; await $('history-kind').dispatch('change');
const refresh = refreshHistory(); pending[1].resolve({runs: [sample('run-B')]}); await refresh;
assert.equal($('history-kind').value, 'suite');
assert.ok($('history-kind').children.some(option => option.value === 'suite'));
assert.match($('runs').textContent, /没有匹配的历史记录/);
assert.match($('history-count').textContent, /匹配 0 \/ 总计 1/);
assert.equal(selected, null);
assert.equal(pending.length, 2);
""")


@pytest.mark.parametrize("bad_row", [
    "null",
    "{...sample('run-bad'), kind: 42}",
    "{...sample('run-bad'), read_errors: 'not an array'}",
])
def test_failed_history_render_keeps_rows_counts_and_timestamp(bad_row):
    run_page("""
Date.now = () => 100000;
const first = setRunScope('all'); pending[0].resolve({runs: [sample('run-original')]}); await first;
const snapshot = $('runs').textContent, count = $('history-count').textContent, signature = lastRuns;
Date.now = () => 200000;
const refresh = refreshHistory();
""" + f"pending[1].resolve({{runs: [sample('run-new'), {bad_row}]}});" + """
await refresh;
assert.equal($('runs').textContent, snapshot);
assert.equal($('history-count').textContent, count);
assert.equal(lastRuns, signature);
assert.equal(freshness.runs.at, 100000);
assert.equal(runViews.all.rows[0].run_id, 'run-original');
assert.match($('runs-status').textContent, /刷新失败/);
""")
