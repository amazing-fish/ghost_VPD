"""外置参数引用、生成资产输入分类与 gated 授权。现场证据按原文保留。"""
import json

from vpdtest.params import Params
from vpdtest.policy import Policy
from vpdtest.tree import UiTree
from vpdtest.util import read_json

SECRET = "not-a-real-password-42"


def _tree(text, extra=None):
    kids = [{"attributes": {"type": "Button", "bounds": "[0,0][100,50]", "clickable": "true"},
             "children": [{"attributes": {"type": "Text", "text": text, "bounds": "[10,10][90,40]"}, "children": []}]}]
    return UiTree({"attributes": {"type": "root", "bounds": "[0,0][100,100]"}, "children": kids + (extra or [])})


def test_secret_comes_from_env_with_reference_and_classification(monkeypatch):
    monkeypatch.setenv("VPD_SECRET_DEMO_PW", SECRET)
    p = Params({"pw": {"secret": "DEMO_PW"}, "name": {"template": "VPD测试-{run}"}, "tail": "7885"}, {}, "run-x-ab12")
    assert p.vars["name"] == "VPD测试-ab12" and p.public()["pw"] == "<secret:pw>"
    assert p.is_secret_ref("${var:pw}") and not p.is_secret_ref("${var:tail}")
    assert p.subst("末位${var:tail}") == "末位7885"
    assert p.is_sensitive(f"x{SECRET}y") and not hasattr(p, "redact")
    missing = Params({"pw": {"secret": "NOT_SET_ANYWHERE"}}, {}, "run-x-1")
    assert missing.missing == ["NOT_SET_ANYWHERE"] and "pw" not in missing.vars


def test_secret_input_rules(ws):
    pol = ws.policy()
    secret_box = {"allow_input": True, "secret": True}
    assert not pol.check_input("plain", secret_box)["allowed"]           # 敏感框不接受明文
    assert pol.check_input("${var:pw}", secret_box, True)["allowed"]
    assert not pol.check_input("${var:pw}", {"allow_input": True}, True)["allowed"]  # 密钥不能写进普通框


def test_gated_needs_run_authorization_and_matching_target(ws):
    pol = ws.policy()
    tree = _tree("登录")
    login = {"side_effect": "account_state"}
    assert not pol.check_tap(tree, (50, 25), "tree", login)["allowed"]                    # 未授权
    assert not pol.check_tap(tree, (50, 25), "tree", None, None, {"account_state"})["allowed"]  # 目标未声明
    r = pol.check_tap(tree, (50, 25), "tree", login, None, {"account_state"})
    assert r["allowed"] and r["authorized_by"] == "account_state"


def test_vehicle_motion_needs_explicit_grant_and_supervisor(ws):
    """控车/运动不是禁止项，但只有"本次授权 + 目标声明 vehicle_motion + 在场监护人"才放行。"""
    pol = ws.policy()
    for text in ("激活", "开始泊车", "召唤"):
        tree = _tree(text)
        # 授权了别的类、或目标声明成别的副作用：都不能借道点运动按钮
        for effect in ("vpd_entry", "route_planning", "navigation"):
            r = pol.check_tap(tree, (50, 25), "tree", {"side_effect": effect}, None, {"vpd_entry", "route_planning"})
            assert not r["allowed"] and r["deny"]["supervised"], (text, effect)
        assert not pol.check_tap(tree, (50, 25), "tree", {"side_effect": "vehicle_motion"}, None, set())["allowed"]
        r = pol.check_tap(tree, (50, 25), "tree", {"side_effect": "vehicle_motion"}, None, {"vehicle_motion"})
        assert r["allowed"] and r["authorized_by"] == "vehicle_motion", text
    a = pol.check_authorizations(["vehicle_motion"], {"vehicle_motion"})
    assert not a["ok"] and a["missing_supervisor"] == ["vehicle_motion"] and a["missing_safety"] == ["vehicle_motion"]
    a = pol.check_authorizations(["vehicle_motion"], {"vehicle_motion"}, "张工", {"safe_stop": [{"action": "tap"}]})
    assert a["ok"]
    # 车控只要监护人，不要求安全停止序列；不可逆账号变更等仍不可授予
    assert pol.check_authorizations(["vehicle_control"], {"vehicle_control"}, "张工")["ok"]
    assert pol.check_authorizations(["account_destructive"], {"account_destructive"}, "张工")["not_grantable"] == [
        "account_destructive"]
    # 同一个可点击卡片里既有"算路"又有"激活"：只授权了算路时不能点（会连带触发运动）
    card = UiTree({"attributes": {"type": "root", "bounds": "[0,0][100,100]"}, "children": [
        {"attributes": {"type": "Column", "bounds": "[0,0][100,100]", "clickable": "true"}, "children": [
            {"attributes": {"type": "Text", "text": "算路", "bounds": "[10,10][90,40]"}, "children": []},
            {"attributes": {"type": "Text", "text": "激活", "bounds": "[10,60][90,90]"}, "children": []}]}]})
    r = pol.check_tap(card, (50, 25), "tree", {"side_effect": "route_planning"}, None, {"route_planning"})
    assert not r["allowed"] and r["deny"]["class"] == "vehicle_motion"


def test_safe_stop_is_always_allowed_but_must_really_be_a_stop(ws):
    pol = ws.policy()
    stop = {"side_effect": "safe_stop"}
    for text in ("暂停", "退出泊车", "结束代驾"):
        r = pol.check_tap(_tree(text), (50, 25), "tree", stop, ["泊车"], set())  # 不需授权，也不受页面级禁止
        assert r["allowed"] and r["safe_stop"], text
    assert not pol.check_tap(_tree("确定"), (50, 25), "tree", stop, None, set())["allowed"]
    # 同一容器里同时有"继续"：点击效果不确定，拒绝
    mixed = UiTree({"attributes": {"type": "root", "bounds": "[0,0][100,100]"}, "children": [
        {"attributes": {"type": "Button", "bounds": "[0,0][100,100]", "clickable": "true"}, "children": [
            {"attributes": {"type": "Text", "text": "退出", "bounds": "[10,10][90,40]"}, "children": []},
            {"attributes": {"type": "Text", "text": "继续", "bounds": "[10,60][90,90]"}, "children": []}]}]})
    r = pol.check_tap(mixed, (50, 25), "tree", stop, None, set())
    assert not r["allowed"] and r["deny"]["mixed_with_safe_stop"]
    # 带停止字样的账号类操作（退出登录）不能借 safe_stop 绕过授权
    assert not pol.check_tap(_tree("退出登录"), (50, 25), "tree", stop, None, set())["allowed"]


def test_unauthorized_case_is_blocked_before_any_step(ws, monkeypatch):
    monkeypatch.setenv("VPD_SECRET_DEMO_PW", SECRET)
    case = ws.load_case("TC-MOTOR-HM-002")
    case.update(id="TC-AUTH", authorizations=[{"effect": "account_state", "why": "登录"}],
                test_data={"pw": {"secret": "DEMO_PW"}, "missing": {"secret": "NOT_SET_ANYWHERE"}})
    from conftest import world
    from vpdtest.runner import run_case
    run = run_case(ws, case=case, device_spec=world("motor_hm_base.json"), propose=False)
    assert run["verdict"]["verdict"] == "blocked" and run["verdict"]["attribution"] == "environment"
    keys = {u["key"] for u in run["env"]["unmet"]}
    assert keys == {"secret.NOT_SET_ANYWHERE", "authorize.account_state"}
    assert all(s["outcome"] == "not_run" for s in run["steps"])
    dump = (ws.runs_dir / run["run_id"] / "run.json").read_text("utf-8") + \
        (ws.runs_dir / run["run_id"] / "events.jsonl").read_text("utf-8")
    assert SECRET not in dump and "<secret:pw>" in dump


def test_random_swipe_is_reproducible(ws):
    from conftest import world
    from vpdtest.execute import Executor
    from vpdtest.experience import Overlay
    from vpdtest.observe import RunStore
    from vpdtest.workspace import RunContext
    app = ws.app("motor_hm")
    trails = []
    for i in range(2):
        store = RunStore(ws.runs_dir, f"session-rs{i}", kind="session")
        dev = ws.device(world("motor_hm_base.json"), state_path=store.dir / "replay_state.json")
        dev.launch(app.bundle, app.ability)
        ctx = RunContext(ws, app, dev, store, Overlay([]), mode="regression")
        ctx.seed = "fixed-seed"
        res = Executor(ctx).run_step({"id": "M1", "action": "swipe", "random": True, "retry": 0}, "c")
        trails.append(res["attempts"][0]["swipe"])
    assert trails[0]["from"] == trails[1]["from"] and trails[0]["to"] == trails[1]["to"]
    assert trails[0]["seed"] == "fixed-seed" and len(trails[0]["fractions"]) == 4
