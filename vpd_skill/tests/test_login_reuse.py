"""Production login recipe and one consumer, in explicitly synthetic UI worlds."""
from copy import deepcopy
from pathlib import Path

import pytest

from vpdtest import cases
from vpdtest.devices.replay import ReplayDevice
from vpdtest.knowledge import KnowledgeStore
from vpdtest.runner import run_case
from vpdtest.util import read_json, read_jsonl, write_json


@pytest.fixture
def login_rig(ws, tmp_path, monkeypatch):
    # Only the isolated fixture gets valid metadata for its synthetic review.
    # Production declarations remain untouched; unknown does not confirm an Oracle.
    case = ws.load_case("TC-QK-223")
    for declaration in case["knowledge_used"]:
        declaration["applicability"] = "unknown"
    write_json(ws.case_path(case["id"]), case)
    # Synthetic selectors for otherwise layout-specific production targets.
    p = ws.root / "apps/qiankun_hm/targets.json"
    targets = read_json(p)
    for ref, label, kind in [("qk_personal_settings", "设置入口", "text"),
                             ("qk_sms_code_input", "sms", "id"), ("qk_verify_login", "提交验证码", "text")]:
        targets["targets"][ref]["strategies"] = [{"kind": kind, "value": label}]
    write_json(p, targets)
    def node(text, y, go=None, **kw):
        return {"t": "Button" if go else "Text", "text": text, "b": [30, y, 330, y+35], **({"go": go} if go else {}), **kw}
    def screen(kids):
        return {"root": {"t": "root", "b": [0, 0, 400, 900], "children": kids}}
    my = screen([node("社区", 30), node("ADS", 80), node("我的", 130), node("我的车辆", 180), node("设置入口", 230, "settings")])
    password = screen([node("欢迎登录华为乾崑", 30), node("验证码登录", 80),
                       node("", 130, t="TextInput", click=True, bind="phone", attrs={"hint": "请输入手机号"}),
                       node("", 180, t="TextInput", click=True, bind="pw", attrs={"hint": "请输入密码"}),
                       node("userProtocol", 230, t="Checkbox", attrs={"checked": "true"}),
                       node("我已阅读并同意用户协议", 270),
                       node("登录", 300, "my")])
    sms = screen([node("身份验证", 30), node("验证码已发至151****3183", 80),
                  node("", 130, t="TextInput", id="sms", click=True, bind="code"), node("提交验证码", 230, "my")])
    identity = screen([node("输入验证码", 30), node("为了确保你的账号安全，请先进行手机号验证", 80), node("151****3183", 130)])
    screens = {"my": my, "password": password, "sms": sms, "identity": identity,
               "logged_out": screen([node("我的车辆", 30), node("登录/注册", 80, "quick")]),
               "quick": screen([node("本机号码一键登录", 30), node("其他登录方式", 80, "method")]),
               "method": screen([node("欢迎登录华为乾崑", 30), node("密码登录", 80, "password"),
                                 node("", 130, t="TextInput", attrs={"hint":"请输入手机号"})]),
               "settings": screen([node("设置", 30), node("个人信息", 80), node("乾崑账号", 130, "account")]),
               "account": screen([node("乾崑账号", 30), node("账号注销", 80), node("密码管理", 130, "identity")]),
               "unknown": screen([node("未登记页面", 30)]),
               "car": screen([node("爱车", 30, t="Text", sel=True), node("ADS", 130, "vpd")]),
               "vpd": screen([node("搜索车位号设为目的地", 30), node("华为练秋湖研发中心", 80)])}
    # A navigation tab in the synthetic logged-in page; the production target is unchanged.
    my["root"]["children"].append(node("爱车", 300, "car"))
    original_key = ReplayDevice.key
    def key(dev, name):
        if name == "back" and dev.current_screen() == "password":
            return dev._res("key_back", raw="synthetic secure keyboard dismissed")
        return original_key(dev, name)
    monkeypatch.setattr(ReplayDevice, "key", key)
    def build(start="my", *, missing_phone=False, load_error=False):
        data = {"bundle": "com.yinwang.qiankunapp.hm", "app_version": "1.0.26.100", "screen": [400,900],
                "display": {"density": 1, "device_form": "bar", "fold_status": "unknown"},
                "initial": {"installed": True, "foreground": "com.ohos.sceneboard"}, "launch_screen": start,
                "screens": deepcopy(screens)}
        for page in ("quick", "method", "password", "sms"):
            data["screens"][page]["root"]["children"].extend([
                node("我的车辆", 500), node("登录/注册", 550), node("社区", 600)])
        if missing_phone:
            data["screens"]["identity"]["root"]["children"][-1]["text"] = "199****0000"
        if load_error:
            data["screens"]["vpd"]["root"]["children"].append(node("停车场信息加载失败", 130))
        path = tmp_path / (start + str(missing_phone) + str(load_error) + ".json")
        write_json(path, data)
        return "replay:" + str(path)
    return ws, build


def execute(rig, start="my", **kw):
    ws, build = rig
    return run_case(ws, "TC-QK-223", device_spec=build(start, **kw),
                    env={"network":"on", "vpd_task_state":"none", "parking_area":"华为练秋湖研发中心"},
                    authorize=["account_state"], propose=False)


def events(run):
    return read_jsonl(Path(run["dir"]) / "events.jsonl")


def test_logged_in_without_credentials_reuses_and_runs_independent_consumer(login_rig):
    run = execute(login_rig)
    assert all(s["outcome"].startswith("passed") for s in run["steps"]), run["steps"]
    assert not [e for e in events(run) if e["event"] == "dispatch" and e.get("op") in ("input_text", "clear_text")]
    assert run["verdict"]["verdict"] == "pending"  # Production Oracle still unconfirmed.
    assert run["asset_review"]["required"] and not run["asset_review"]["valid"]
    assert all(s["outcome"].startswith("passed") for s in run["cleanup"])


@pytest.mark.parametrize("start", ["logged_out", "quick", "method", "password"])
def test_password_login_does_not_require_untriggered_sms(login_rig, start):
    ws, _ = login_rig
    ws.config["secrets"] = {"QIANKUN_PHONE": "synthetic-phone", "QIANKUN_PASSWORD": "synthetic-password"}
    run = execute(login_rig, start)
    assert all(s["outcome"].startswith("passed") for s in run["steps"]), run["steps"]
    sms = run["steps"][1]["sub_steps"][7]
    assert sms["outcome"] == "skipped" and not sms["attempts"]


def test_actual_sms_without_code_hands_over_without_input_business_or_cleanup(login_rig):
    run = execute(login_rig, "sms")
    assert run["verdict"]["verdict"] == "blocked" and run["verdict"]["action_required"]
    assert all(s["outcome"] == "not_run" for s in run["steps"][2:])
    dispatches = [e for e in events(run) if e["event"] == "dispatch" and e.get("sent") is not False]
    assert {e["op"] for e in dispatches} == {"launch"}
    assert read_json(Path(run["dir"])/"replay_state.json")["stack"][-1] == "sms"


def test_code_reentry_observes_current_page_instead_of_resuming_step_number(login_rig):
    ws, _ = login_rig
    ws.config["secrets"] = {"QIANKUN_SMS_CODE": "123456"}
    run = execute(login_rig, "sms")
    assert all(s["outcome"].startswith("passed") for s in run["steps"]), run["steps"]
    moved = execute(login_rig, "unknown")
    assert moved["steps"][1]["outcome"] == "pending"
    assert not [e for e in events(moved) if e["event"] == "dispatch" and e.get("op") == "input_text"]


def test_missing_terminal_identity_stops_before_business(login_rig):
    run = execute(login_rig, missing_phone=True)
    assert run["steps"][1]["outcome"] not in ("passed", "passed_after_recovery")
    assert all(s["outcome"] == "not_run" for s in run["steps"][2:])


@pytest.mark.parametrize("start", ["settings", "account"])
def test_reentry_from_known_account_page_still_confirms_account(login_rig, start):
    ws, build = login_rig
    case = deepcopy(ws.load_case("TC-QK-223"))
    case.update(steps=case["steps"][:2], assertions=[], cleanup=[])
    run = run_case(ws, case=case, device_spec=build(start),
                   env={"network":"on", "vpd_task_state":"none", "parking_area":"华为练秋湖研发中心"},
                   authorize=["account_state"], propose=False)
    assert run["steps"][1]["outcome"].startswith("passed")
    assert not [e for e in events(run) if e["event"] == "dispatch" and e.get("op") == "input_text"]


def test_consumer_requires_current_review_and_recipe_changes_invalidate_it(login_rig):
    ws, build = login_rig
    with pytest.raises(PermissionError):
        run_case(ws,"TC-QK-223", device_spec=build(), require_asset_review=True)
    # Approve only the isolated test copy; never touches production assets or knowledge.
    cases.approve(ws,"TC-QK-223",KnowledgeStore(ws.root),reviewer="pytest-human",basis="synthetic review only")
    assert cases.review_status(ws,ws.load_case("TC-QK-223"))["valid"]
    app = ws.app("qiankun_hm")
    before = cases.dependency_hash(ws.load_case("TC-QK-223"),app,KnowledgeStore(ws.root))
    path = app.dir / "states.json"
    data=read_json(path)
    navigation = next(step for step in data["recipes"]["qk_target_account_ready"]
                      if step.get("target") == "qk_login_other_method")
    navigation["timeout_s"] += 1
    write_json(path,data)
    assert not cases.review_status(ws,ws.load_case("TC-QK-223"))["valid"]
    assert cases.dependency_hash(ws.load_case("TC-QK-223"),ws.app("qiankun_hm"),KnowledgeStore(ws.root)) != before


def test_selected_inline_navigation_failure_stops_before_next_action(login_rig):
    ws, _ = login_rig
    path = ws.app("qiankun_hm").dir / "targets.json"
    data = read_json(path)
    data["targets"]["qk_personal_settings"]["strategies"] = [{"kind":"text", "value":"missing-settings"}]
    write_json(path, data)
    run = execute(login_rig)
    failed = run["steps"][1]["sub_steps"][-1]
    while failed.get("sub_steps"):
        failed = failed["sub_steps"][-1]
    assert failed["action"] == "tap" and failed["critical"] is True
    assert not failed["outcome"].startswith("passed")
    assert all(step["outcome"] == "not_run" for step in run["steps"][2:])
def test_sms_code_is_not_sent_to_another_accounts_challenge(login_rig):
    ws, build = login_rig
    ws.config["secrets"] = {"QIANKUN_SMS_CODE": "123456"}
    spec = build("sms")
    path = Path(spec.split(":",1)[1])
    data = read_json(path)
    data["screens"]["sms"]["root"]["children"][1]["text"] = "验证码已发至199****0000"
    write_json(path,data)
    run = run_case(ws,"TC-QK-223",device_spec=spec,env={"network":"on","vpd_task_state":"none","parking_area":"华为练秋湖研发中心"},authorize=["account_state"],propose=False)
    assert run["steps"][1]["outcome"] == "pending"
    assert all(s["outcome"] == "not_run" for s in run["steps"][2:])
    assert not [e for e in events(run) if e["event"] == "dispatch" and e.get("op") == "input_text"]


def test_sms_handover_stops_reviewed_suite_successor(login_rig, monkeypatch, capsys):
    import json
    from vpdtest import workspace as workspace_mod
    from vpdtest.cli import main
    ws, build = login_rig
    cases.approve(ws,"TC-QK-223",KnowledgeStore(ws.root),reviewer="pytest-human",basis="synthetic review only")
    env = {"network":"on","vpd_task_state":"none","parking_area":"华为练秋湖研发中心"}
    write_json(ws.root/"suites/login-handover.json",{"id":"login-handover","cases":[{"id":"TC-QK-223","env":env},{"id":"TC-QK-223","env":env}]})
    monkeypatch.setattr(workspace_mod,"load_config",lambda:{})
    code = main(["--workspace",str(ws.root),"suite","login-handover","--device",build("sms"),"--authorize","account_state"])
    out=json.loads(capsys.readouterr().out)
    assert code == 2 and len(out["results"]) == 1
    assert len(out["not_run"]) == 1 and out["action_required"]


def test_hidden_password_cache_does_not_select_password_branch(login_rig):
    ws, build = login_rig
    spec=build()
    path=Path(spec.split(":",1)[1])
    data=read_json(path)
    hidden={"t":"Column","attrs":{"visible":"false"},"b":[0,0,400,400],"children":data["screens"]["password"]["root"]["children"]}
    data["screens"]["my"]["root"]["children"].append(hidden)
    write_json(path,data)
    run=run_case(ws,"TC-QK-223",device_spec=spec,env={"network":"on","vpd_task_state":"none","parking_area":"华为练秋湖研发中心"},authorize=["account_state"],propose=False)
    assert run["steps"][1]["sub_steps"][4]["outcome"] == "skipped"
    assert not [e for e in events(run) if e["event"] == "dispatch" and e.get("op") == "input_text"]


def test_hidden_unchecked_protocol_does_not_block_current_checked_form(login_rig):
    ws, build = login_rig
    ws.config["secrets"] = {"QIANKUN_PHONE":"synthetic-phone", "QIANKUN_PASSWORD":"synthetic-password"}
    spec = build("password")
    path = Path(spec.split(":", 1)[1])
    data = read_json(path)
    data["screens"]["password"]["root"]["children"].append(
        {"t":"Column", "attrs":{"visible":"false"}, "b":[0,0,400,400], "children":[
            {"t":"Checkbox", "text":"userProtocol", "attrs":{"checked":"false"}, "b":[30,230,330,265]}]})
    write_json(path, data)
    run = run_case(ws, "TC-QK-223", device_spec=spec,
                   env={"network":"on", "vpd_task_state":"none", "parking_area":"华为练秋湖研发中心"},
                   authorize=["account_state"], propose=False)
    assert all(step["outcome"].startswith("passed") for step in run["steps"])


@pytest.mark.parametrize("start",["password","sms"])
def test_submission_waits_for_delayed_next_stage(login_rig,start):
    ws,build=login_rig
    ws.config["secrets"]={"QIANKUN_PHONE":"synthetic-phone","QIANKUN_PASSWORD":"synthetic-password","QIANKUN_SMS_CODE":"123456"}
    spec=build(start)
    path=Path(spec.split(":",1)[1])
    data=read_json(path)
    data["screens"]["pending"]=deepcopy(data["screens"][start])
    data["screens"]["pending"]["after_obs"]={"n":12,"go":"my","replace":True}
    next(n for n in data["screens"][start]["root"]["children"] if n.get("text") in ("登录", "提交验证码"))["go"]="pending"
    write_json(path,data)
    run=run_case(ws,"TC-QK-223",device_spec=spec,env={"network":"on","vpd_task_state":"none","parking_area":"华为练秋湖研发中心"},authorize=["account_state"],propose=False)
    assert all(s["outcome"].startswith("passed") for s in run["steps"]),run["steps"]
    monitors=[s for s in run["steps"][1]["sub_steps"] if s["action"]=="monitor"]
    assert any(s["monitor"]["samples"]>1 for s in monitors)


def test_submission_without_next_stage_times_out_before_business(login_rig):
    ws,build=login_rig
    ws.config["secrets"]={"QIANKUN_PHONE":"synthetic-phone","QIANKUN_PASSWORD":"synthetic-password"}
    spec=build("password")
    path=Path(spec.split(":",1)[1])
    data=read_json(path)
    submit = next(n for n in data["screens"]["password"]["root"]["children"] if n.get("text") == "登录")
    submit.pop("go")
    submit["click"]=True
    write_json(path,data)
    run=run_case(ws,"TC-QK-223",device_spec=spec,env={"network":"on","vpd_task_state":"none","parking_area":"华为练秋湖研发中心"},authorize=["account_state"],propose=False)
    assert run["steps"][1]["outcome"]=="fail_candidate"
    assert all(s["outcome"]=="not_run" for s in run["steps"][2:])
