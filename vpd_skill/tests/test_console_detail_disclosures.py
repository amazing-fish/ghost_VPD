"""Shipped handlers/timer with HTTP payloads and a DOM model, not browser visual QA."""
import json

import pytest
from test_console_polling import run_page
from test_console_response_observation import (
    http_detail,
    make_run,
    records,
    write_record,
)


@pytest.fixture
def detail_payloads(tmp_path):
    condition = {"status": "true", "records": [{"condition": "合成操作条件", "reason": "合成取证",
                                                "source": "synthetic", "obs": "OBS-SYNTHETIC"}]}
    result = {
        "validation_level": "offline_replay", "informal": True, "asset_review": {"state": "pending"},
        "verdict": {"verdict": "pending", "label": "待确认",
                    "coverage": {"verified": [], "unverified": ["A1"]}},
        "steps": [{"step_id": "S1", "action": "synthetic", "outcome": "passed", "condition": condition}],
        "assertions": [{"id": "A1", "result": "pending", "verified": False, "guard": condition}],
    }
    events = records({"event": "run_start", "case": "TC-SYNTHETIC", "title": "合成证据", "steps": ["S1"]},
                     {"event": "step_done", "step": "S1", "outcome": "passed"},
                     {"event": "run_end", "verdict": "pending"})
    path = make_run(tmp_path, events, metadata=result)
    original_bytes = {p.name: p.read_bytes() for p in path.iterdir()}
    initial, unchanged = http_detail(path), http_detail(path)
    assert initial == unchanged and initial["finished"] and "read_errors" not in initial
    assert {p.name: p.read_bytes() for p in path.iterdir()} == original_bytes
    (path / "events.jsonl").write_bytes(events + records({"event": "observe", "state": "新增合成事件"}))
    result["steps"][0]["condition"]["records"][0]["reason"] = "更新合成取证"
    write_record(path / "run.json", result)
    changed = http_detail(path)
    (path / "run.json").unlink()  # Optional result disappears; this is a successful detail read.
    missing_result = http_detail(path)
    assert "steps" not in missing_result and "read_errors" not in missing_result
    write_record(path / "run.json", result)
    restored = http_detail(path)
    return {"initial": initial, "unchanged": unchanged, "changed": changed,
            "missingResult": missing_result, "restored": restored}


SETUP = r"""
// Model the native boolean property only; do not simulate browser focus/layout/scroll.
Object.defineProperty(Node.prototype, 'open', {
  get() { return Object.hasOwn(this.attributes, 'open'); },
  set(value) { if (value) this.attributes.open = ''; else delete this.attributes.open; }
});
const booleanProbe = new Node('details'); booleanProbe.setAttribute('open', 'false');
assert.equal(booleanProbe.open, true, 'Even open="false" is an open boolean attribute');
booleanProbe.open = false; assert.equal(booleanProbe.open, false);
function descendants(node) {
  return node.children.filter(child => child instanceof Node)
    .flatMap(child => [child, ...descendants(child)]);
}
const panel = name => descendants($('detail')).find(node => node.tag === 'details'
  && node.querySelector('summary').textContent === '选支证据：' + name);
const choices = () => [panel('操作条件')?.open, panel('断言前置')?.open];
const choose = states => {
  panel('操作条件').open = states[0]; panel('断言前置').open = states[1];
};
const snapshot = () => descendants($('detail'));
const assertSameNodes = before => {
  const after = snapshot(); assert.equal(after.length, before.length);
  before.forEach((node, index) => assert.equal(after[index], node));
};
const settled = () => new Promise(resolve => setImmediate(resolve));
let now = 100000; Date.now = () => now;
const detailTimer = intervals.find(timer => timer.callback === pollDetail && timer.ms === 3000);
assert.ok(detailTimer, 'Drive the actual registered 3-second detail timer');
async function read(payload) {
  const count = pending.length, reading = detailTimer.callback();
  assert.equal(pending.length, count + 1, 'Preserving the UI must not skip a network read');
  assert.equal(pending.at(-1).path, '/api/runs/' + selected);
  pending.at(-1).resolve(payload); await reading;
}
async function enter(scope = 'recent') {
  const loading = scope === 'all' ? $('runs-history').dispatch('click') : pollRuns();
  pending.at(-1).resolve({runs: [payloads.initial, sample('run-B')]}); await loading;
  await $('runs').children[0].children[0].dispatch('click');
  pending.at(-1).resolve(payloads.initial); await settled();
  assert.deepEqual(choices(), [false, false]);
}
"""


def check_page(payloads, body):
    # Compact Unicode JSON leaves room beneath Windows' quoted command-line limit.
    data = json.dumps(payloads, ensure_ascii=False, separators=(",", ":"))
    run_page("const payloads = " + data + ";\n" + SETUP + body, with_lifecycle=True)


@pytest.mark.parametrize("states", [[True, False], [False, True]])
@pytest.mark.parametrize("scope", ["recent", "all"])
def test_unchanged_timer_retains_all_nodes_and_independent_choices(detail_payloads, states, scope):
    check_page(detail_payloads, f"""
await enter('{scope}'); choose({json.dumps(states)});
const before = snapshot(), historyStamp = freshness.runs.at;
const text = $('detail').textContent;
now += 3000; await read(payloads.unchanged);
assertSameNodes(before);
assert.deepEqual(choices(), {json.dumps(states)});
assert.equal($('detail').textContent, text);
assert.equal(freshness.detail.at, now);
assert.equal(freshness.runs.at, historyStamp);
assert.match($('detail-status').textContent, /刷新正常/);
assert.match($('detail').textContent, /待确认.*离线回放（不代表真机）.*非正式运行.*尚未审核/s);
assert.match($('detail').textContent, /不代表正式用例覆盖/);
if (runScope === 'all') {{
  assert.match($('runs-status').textContent, /手动历史读取.*不代表当前状态/);
  assert.equal(pending.filter(request => request.path === '/api/runs?scope=all').length, 1);
}}
""")


@pytest.mark.parametrize("states", [[True, False], [False, True]])
def test_changed_evidence_and_appended_events_keep_latest_independent_choices(detail_payloads, states):
    check_page(detail_payloads, f"""
await enter(); choose({json.dumps(states)});
const old = snapshot();
// A user can change either choice while this request is still in flight.
const reading = detailTimer.callback(); choose({json.dumps(states[::-1])});
now += 3000; pending.at(-1).resolve(payloads.changed); await reading;
assert.deepEqual(choices(), {json.dumps(states[::-1])});
assert.ok(old.every(node => !snapshot().includes(node)));
assert.match($('detail').textContent, /更新合成取证/);
assert.match($('detail').textContent, /新增合成事件/);
assert.equal(freshness.detail.at, now);
const stable = snapshot(); now += 3000; await read(payloads.changed); assertSameNodes(stable);
""")


@pytest.mark.parametrize("changed", [False, True])
def test_failed_read_retains_content_choices_and_time_then_recovers(detail_payloads, changed):
    check_page(detail_payloads, f"""
await enter('all'); choose([true, false]);
const before = snapshot(), text = $('detail').textContent, stamp = freshness.detail.at;
now += 3000;
const failure = detailTimer.callback(); pending.at(-1).reject(new Error('synthetic offline')); await failure;
assertSameNodes(before); assert.deepEqual(choices(), [true, false]);
assert.equal($('detail').textContent, text); assert.equal(freshness.detail.at, stamp);
assert.match($('detail-status').textContent, /历史快照.*不代表当前状态.*synthetic offline/);
choose([false, true]); now += 3000;
await read(payloads.{'changed' if changed else 'unchanged'});
assert.deepEqual(choices(), [false, true]);
{'assert.match($("detail").textContent, /新增合成事件/);' if changed else 'assertSameNodes(before);'}
assert.equal(freshness.detail.at, now); assert.equal(freshness.detail.error, '');
assert.equal($('detail-status').attributes['data-stale'], 'false');
assert.match($('detail-status').textContent, /刷新正常/);
""")


def test_repeated_selected_card_click_is_serial_and_keeps_the_same_snapshot(detail_payloads):
    check_page(detail_payloads, r"""
await enter(); choose([true, false]); const before = snapshot();
const card = $('runs').children[0].children[0], count = pending.length;
await card.dispatch('click'); await card.dispatch('click');
await detailTimer.callback();
assert.equal(pending.length, count + 1);
assertSameNodes(before); assert.deepEqual(choices(), [true, false]);
now += 3000; pending.at(-1).resolve(payloads.unchanged); await settled();
assertSameNodes(before); assert.equal(freshness.detail.at, now);
await $('runs').children[0].children[0].dispatch('click');
assert.equal(pending.length, count + 2);
pending.at(-1).resolve(payloads.changed); await settled();
assert.deepEqual(choices(), [true, false]);
assert.match($('detail').textContent, /新增合成事件/);
""")


@pytest.mark.parametrize("missing", ["steps", "assertions"])
def test_disappearing_group_does_not_transfer_its_choice_to_other_or_new_group(detail_payloads, missing):
    remaining = "断言前置" if missing == "steps" else "操作条件"
    returning = "操作条件" if missing == "steps" else "断言前置"
    check_page(detail_payloads, f"""
await enter(); choose([true, true]);
now += 3000; await read({{...payloads.changed, {missing}: []}});
assert.equal(panel('{returning}'), undefined);
assert.equal(panel('{remaining}').open, true);
now += 3000; await read(payloads.changed);
assert.equal(panel('{returning}').open, false, 'A newly present group starts closed');
assert.equal(panel('{remaining}').open, true, 'State follows its semantic group, not DOM position');
""")


def test_missing_optional_result_clears_panels_and_reappearance_starts_closed(detail_payloads):
    check_page(detail_payloads, r"""
await enter(); choose([true, true]); now += 3000; await read(payloads.missingResult);
assert.deepEqual(choices(), [undefined, undefined]);
assert.match($('detail').textContent, /新增合成事件/);
assert.doesNotMatch($('detail').textContent, /选支证据|更新合成取证/);
assert.equal(freshness.detail.at, now); assert.equal(freshness.detail.error, '');
now += 3000; await read(payloads.restored);
assert.deepEqual(choices(), [false, false]);
""")


@pytest.mark.parametrize("old_fails", [False, True])
@pytest.mark.parametrize("late_before_current", [False, True])
def test_a_b_a_resets_choices_and_rejects_obsolete_success_and_failure(
        detail_payloads, old_fails, late_before_current):
    check_page(detail_payloads, f"""
await enter(); choose([true, true]);
const originalNodes = snapshot(), first = detailTimer.callback(), oldA = pending.at(-1);
await $('runs').children[1].children[0].dispatch('click'); const oldB = pending.at(-1);
assert.equal(oldA.signal.aborted, true);
assert.deepEqual(choices(), [undefined, undefined]);
assert.equal(freshness.detail.at, null);
await $('runs').children[0].children[0].dispatch('click'); const current = pending.at(-1);
assert.equal(oldB.signal.aborted, true);
assert.deepEqual(choices(), [undefined, undefined]);
assert.equal(freshness.detail.at, null);
async function obsolete() {{
  {'oldA.reject(new Error("obsolete A")); oldB.reject(new Error("obsolete B"));' if old_fails else
      'oldA.resolve({...payloads.changed, title: "obsolete A"}); oldB.resolve(sample("run-B"));'}
  await first; await settled();
}}
if ({json.dumps(late_before_current)}) {{
  const loading = snapshot(); await obsolete(); assertSameNodes(loading);
  assert.equal(freshness.detail.at, null); assert.equal(freshness.detail.error, '');
  const count = pending.length; await detailTimer.callback(); assert.equal(pending.length, count);
}}
now += 3000; current.resolve(payloads.initial); await settled();
assert.deepEqual(choices(), [false, false]);
assert.ok(originalNodes.every(node => !snapshot().includes(node)));
choose([false, true]); const fresh = snapshot();
if (!{json.dumps(late_before_current)}) await obsolete();
assertSameNodes(fresh); assert.deepEqual(choices(), [false, true]);
assert.equal(freshness.detail.at, now); assert.equal(freshness.detail.error, '');
assert.doesNotMatch($('detail').textContent, /obsolete|run-B/);
await read(payloads.unchanged); assertSameNodes(fresh);
""")


def test_recent_history_switch_preserves_selected_detail_and_manual_list_read_time(detail_payloads):
    check_page(detail_payloads, r"""
await enter(); choose([false, true]); const before = snapshot();
now += 3000; const history = $('runs-history').dispatch('click');
pending.at(-1).resolve({runs: [sample('another-run')]}); await history;
assert.equal(selected, payloads.initial.run_id); assertSameNodes(before);
const historyStamp = freshness.runs.at;
now += 3000; await read(payloads.unchanged); assertSameNodes(before);
assert.equal(freshness.runs.at, historyStamp);
const recent = $('runs-recent').dispatch('click');
pending.at(-1).resolve({runs: [payloads.initial]}); await recent;
assertSameNodes(before); assert.deepEqual(choices(), [false, true]);
await $('runs-history').dispatch('click');
assertSameNodes(before); assert.equal(freshness.runs.at, historyStamp);
assert.equal(pending.filter(request => request.path === '/api/runs?scope=all').length, 1);
assert.match($('runs-status').textContent, /手动历史读取.*不代表当前状态/);
now += 3000; await read(payloads.changed);
assert.deepEqual(choices(), [false, true]); assert.equal(freshness.runs.at, historyStamp);
""")


@pytest.mark.parametrize("bad", ["null", "sample('wrong-run')", "{...payloads.changed, steps: [null]}"])
def test_invalid_or_unrenderable_payload_does_not_replace_last_good_snapshot(detail_payloads, bad):
    check_page(detail_payloads, f"""
await enter(); choose([true, false]); const before = snapshot(), stamp = freshness.detail.at;
now += 3000; await read({bad});
assertSameNodes(before); assert.deepEqual(choices(), [true, false]);
assert.equal(freshness.detail.at, stamp); assert.ok(freshness.detail.error);
assert.match($('detail-status').textContent, /历史快照/);
await read(payloads.unchanged); assertSameNodes(before);
assert.equal(freshness.detail.at, now); assert.equal(freshness.detail.error, '');
""")
