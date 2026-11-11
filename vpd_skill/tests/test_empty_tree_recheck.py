"""Pure read reacquisition before expensive cover diagnosis; no device access."""
import pytest
from test_dispatch_regression import _ctx
from test_goal_loop import BUNDLE, _lab_world
from vpdtest.devices import DeviceResult
from vpdtest.execute import Executor
from vpdtest.util import read_json


def setup(ws, tmp_path, monkeypatch, mode="transient", first_cost=1):
    ctx = _ctx(ws, _lab_world(tmp_path))
    original = ctx.device.dump_tree
    clock = [0.0]
    calls = {"dump": 0, "cover": 0}
    fg = [BUNDLE]
    monkeypatch.setattr("vpdtest.observe.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(ctx.device, "foreground", lambda: fg[0])
    def dump(*args):
        calls["dump"] += 1
        clock[0] += first_cost if calls["dump"] == 1 else 1
        if calls["dump"] == 1 or mode == "persistent":
            return DeviceResult("dump", True, True, data={"tree": {}})
        if mode == "foreign":
            fg[0] = "foreign.app"
        if mode == "failed":
            return DeviceResult("dump", False, False, "tree_collection_error", "fixture")
        return original(*args)
    def cover(_):
        calls["cover"] += 1
        clock[0] += 25
        return {"dump_ok": True, "matched": None, "windows": [], "texts": []}
    monkeypatch.setattr(ctx.device, "dump_tree", dump)
    monkeypatch.setattr(ctx.observer, "_cover_evidence", cover)
    return ctx, clock, calls


def test_transient_empty_tree_skips_expensive_cover_and_retains_both_trees(ws, tmp_path, monkeypatch):
    ctx, clock, calls = setup(ws, tmp_path, monkeypatch)
    obs, tree = ctx.observe("after", deadline=15)
    assert calls == {"dump": 2, "cover": 0}
    assert clock[0] == 2 and tree.status == "ok" and obs["fresh"]
    assert obs["tree_recheck"]["initial_tree"]["status"] == "empty"
    assert read_json(ctx.store.dir / obs["tree_recheck"]["initial_tree"]["path"]) == {}
    assert (ctx.store.dir / obs["tree"]["path"]).is_file()


@pytest.mark.parametrize("mode,first_cost,count", [("persistent", 1, 2), ("transient", 16, 1)])
def test_persistent_or_expired_read_does_not_extend_budget(ws, tmp_path, monkeypatch, mode, first_cost, count):
    ctx, clock, calls = setup(ws, tmp_path, monkeypatch, mode, first_cost)
    obs, tree = ctx.observe("after", deadline=15)
    assert calls == {"dump": count, "cover": 1}
    assert obs["page"]["state"] == "undetermined"
    # Existing one full observation may overrun. No extra deadline / read is added.
    assert clock[0] == first_cost + (count - 1) + 25


@pytest.mark.parametrize("mode", ["foreign", "failed"])
def test_recheck_never_accepts_stale_or_failed_tree(ws, tmp_path, monkeypatch, mode):
    ctx, _, calls = setup(ws, tmp_path, monkeypatch, mode)
    obs, tree = ctx.observe("after", deadline=15)
    assert calls == {"dump": 2, "cover": 0}
    assert tree is None
    assert obs["tree"]["status"] == ("stale" if mode == "foreign" else "error")


def test_no_deadline_keeps_non_post_observation_unchanged(ws, tmp_path, monkeypatch):
    ctx, _, calls = setup(ws, tmp_path, monkeypatch)
    ctx.observe("before")
    assert calls == {"dump": 1, "cover": 1}


def test_fresh_same_post_no_cover_is_not_diagnosed_or_dispatched_again(ws, tmp_path, monkeypatch):
    ctx, _, calls = setup(ws, tmp_path, monkeypatch, "persistent")
    obs, _ = ctx.observe("after", deadline=15)
    monkeypatch.setattr("vpdtest.sysdialog.resolve_screen_cover", lambda *a, **k: pytest.fail("duplicate diagnosis"))
    monkeypatch.setattr(ctx.dispatcher, "send", lambda *a, **k: pytest.fail("must not dispatch"))
    att = {"status": "unobservable", "post": {"obs_id": obs["obs_id"]},
           "dispatch": {"sent": True, "executor_ok": True}}
    rec = Executor(ctx)._recover({"id": "S1", "action": "launch"}, att, "fixture")
    assert rec["performed"] is False and rec["retry_original"] is False
    assert rec["diagnosis_obs"] == obs["obs_id"] and calls["cover"] == 1


@pytest.mark.parametrize("change", ["stale", "other_obs", "matched", "error", "failed"])
def test_negative_diagnosis_reuse_requires_current_reliable_evidence(ws, tmp_path, monkeypatch, change):
    ctx, _, _ = setup(ws, tmp_path, monkeypatch, "persistent")
    obs, _ = ctx.observe("after", deadline=15)
    if change == "stale": obs["fresh"] = False
    if change == "matched": obs["screen_cover"]["matched"] = "dialog"
    if change == "error": obs["screen_cover"]["execution_error"] = {"error_class": "timeout"}
    if change == "failed": obs["screen_cover"]["dump_ok"] = False
    called = []
    monkeypatch.setattr("vpdtest.sysdialog.resolve_screen_cover", lambda *a, **k: called.append(True) or
                        {"diagnosis": {"matched": None}, "status": "no_cover", "reason": "fixture"})
    att = {"status": "unobservable", "post": {"obs_id": "different" if change == "other_obs" else obs["obs_id"]}}
    rec = Executor(ctx)._recover({"id": "S1", "action": "launch"}, att, "fixture")
    assert called == [True] and rec["retry_original"] is False

@pytest.mark.parametrize("mode", ["transient", "persistent"])
def test_post_receives_original_deadline_and_never_replays_launch(ws, tmp_path, monkeypatch, mode):
    ctx, clock, calls = setup(ws, tmp_path, monkeypatch, mode)
    ctx.device.poll_interval = 1
    launch = ctx.device.launch
    launches = []
    def once(*args):
        launches.append(args)
        calls["dump"] = 0  # Only count post-dispatch reads.
        clock[0] = 0
        return launch(*args)
    monkeypatch.setattr(ctx.device, "launch", once)
    result = Executor(ctx).run_step({"id": "S1", "action": "launch", "expect_state": "@in_app",
                                     "timeout_s": 15, "retry": 1}, "fixture")
    assert len(launches) == 1
    post = result["attempts"][0]["post"]
    assert post["timeout_s"] == 15 and post["polls"] == 1
    if mode == "transient":
        assert post["status"] == "ok" and post["elapsed_s"] == 2
    else:
        assert post["status"] == "unobservable" and result["action_required"]
        assert result["recoveries"][0]["retry_original"] is False

@pytest.mark.parametrize("mode", ["switch_back", "deadline"])
def test_recheck_foreground_guard_cannot_be_bypassed(ws, tmp_path, monkeypatch, mode):
    ctx, clock, calls = setup(ws, tmp_path, monkeypatch)
    queries = []
    def fg():
        queries.append(True)
        if len(queries) == 2:
            if mode == "switch_back":
                return "foreign.app"
            clock[0] = 15
        return BUNDLE
    monkeypatch.setattr(ctx.device, "foreground", fg)
    obs, tree = ctx.observe("after", deadline=15)
    assert calls["dump"] == 1
    assert obs["tree_recheck"]["performed"] is False
    if mode == "switch_back":
        assert tree is None and obs["fresh"] is False and calls["cover"] == 0
    else:
        assert obs["page"]["state"] == "undetermined" and calls["cover"] == 1
