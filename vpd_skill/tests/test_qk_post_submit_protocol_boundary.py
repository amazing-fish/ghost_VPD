"""Regression for the documented, not-yet-implemented post-submit protocol path.

The UI is synthetic; work-machine dialog ancestry has not been supplied.
"""
from pathlib import Path

import pytest

from test_issue64_login_paths import _password_flow, _password_world, _run_world, login_rig
from vpdtest.util import read_json, write_json


@pytest.mark.parametrize("foreign_label", ["同意", "取消"])
def test_password_form_without_checkbox_stops_before_submit(login_rig, foreign_label):
    spec = _password_world(login_rig)
    path = Path(spec.split(':', 1)[1])
    data = read_json(path)
    children = data['screens']['password']['root']['children']
    data['screens']['password']['root']['children'] = [
        node for node in children if node.get('text') != 'userProtocol'
    ]
    # A same-named control outside the actual dialog is not evidence of consent.
    data['screens']['password']['root']['children'].append({
        't': 'Button', 'text': foreign_label, 'b': [30, 660, 330, 700],
        'click': True, 'go': 'my',
    })
    write_json(path, data)
    run = _run_world(login_rig, spec)
    flow = _password_flow(run)
    assert flow['outcome'] not in ('passed', 'passed_after_recovery')
    assert all(step['action'] != 'tap' or step.get('target') != 'qk_login_btn'
               for step in flow['sub_steps'])
    assert all(step['outcome'] == 'not_run' for step in run['steps'][2:])
