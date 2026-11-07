"""Console 保留运行证据层级；Node 检查实际页面函数的文本渲染，不代表浏览器视觉验收。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from vpdtest.console import progress
from vpdtest.util import append_jsonl, write_json


@pytest.mark.parametrize("verdict", ["passed", "failed", "pending", "error"])
def test_finished_run_context_survives_list_and_detail(tmp_path, verdict):
    path = tmp_path / "run-context"
    context = {"validation_level": "offline_replay", "informal": True,
               "asset_review": {"state": "stale", "valid": False, "required": True,
                                "record": {"reviewer": "合成审核人", "basis": "合成依据"}}}
    write_json(path / "run.json", {"verdict": {"verdict": verdict}, **context})
    append_jsonl(path / "events.jsonl", {"event": "run_start", "case": "TC-SYNTHETIC"})
    append_jsonl(path / "events.jsonl", {"event": "run_end", "verdict": verdict})
    for result in [progress.list_runs(tmp_path)[0], progress.detail(tmp_path, path.name)]:
        assert result["verdict"] == verdict
        assert {key: result[key] for key in context} == context
    assert json.loads((path / "run.json").read_text(encoding="utf-8"))["asset_review"] == context["asset_review"]


def test_legacy_run_does_not_invent_verification_or_review(tmp_path):
    path = tmp_path / "run-legacy"
    write_json(path / "run.json", {"verdict": {"verdict": "passed"}})
    append_jsonl(path / "events.jsonl", {"event": "run_end", "verdict": "passed"})
    result = progress.detail(tmp_path, path.name)
    assert result["verdict"] == "passed"
    assert not {"validation_level", "informal", "asset_review"} & result.keys()


@pytest.mark.parametrize("context,expected", [
    ({"validation_level": "offline_replay", "informal": True, "asset_review": {"state": "pending"}},
     ["离线回放（不代表真机）", "非正式运行", "尚未审核"]),
    ({"validation_level": "real_device", "informal": False, "asset_review": {"state": "approved"}},
     ["真机运行", "回归资产运行", "审核有效"]),
    ({"validation_level": "real_device", "informal": True, "asset_review": {"state": "stale"}},
     ["真机运行", "非正式运行", "审核已失效"]),
    ({"validation_level": "real_device", "informal": True, "asset_review": {"state": "revoked"}},
     ["真机运行", "非正式运行", "审核已撤销"]),
    ({}, ["验证级别未记录", "运行性质未记录", "审核状态未记录"]),
])
def test_actual_page_renders_context_beside_result(context, expected):
    node = shutil.which("node")
    if not node:
        pytest.skip("页面函数文本检查需要 Node.js；不影响 Python 运行依赖")
    page = (Path(progress.__file__).parent / "page.html").read_text(encoding="utf-8")
    constants = page[page.index("  const STATE ="):page.index("  const reduceMotion")]
    element = page[page.index("  function el("):page.index("  const log =")]
    functions = page[page.index("  const hhmmss ="):page.index("  pollStatus(); pollRuns();")]
    payload = {"kind": "run", "kind_label": "用例", "run_id": "run-synthetic", "state": "finished",
               "finished": True, "updated": 0, "verdict": "passed", "tail": [],
               "verdict_detail": {"verdict": "passed", "label": "通过"},
               "coverage": {"verified": ["A1"], "not_applicable": [], "unverified": []},
               "assertions": [{"id": "A1", "verified": True}], **context}
    harness = r"""
const payload = JSON.parse(require('fs').readFileSync(0, 'utf8'));
class Node {
  constructor() { this.children = []; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener() {}
  get textContent() { return this.children.map(x => x instanceof Node ? x.textContent : x).join(' '); }
}
const document = { createElement: () => new Node() };
const detail = new Node(), $ = () => detail, api = async () => payload;
let selected = payload.run_id, lastRuns = '';
"""
    harness += constants + element + functions + """
(async () => {
  const card = runItem(payload).textContent;
  await pollDetail();
  process.stdout.write(JSON.stringify({card, detail: detail.textContent}));
})().catch(e => { console.error(e); process.exit(1); });
"""
    result = subprocess.run([node, "-e", harness], input=json.dumps(payload), encoding="utf-8",
                            capture_output=True, check=True)
    rendered = json.loads(result.stdout)
    for surface in rendered.values():
        assert "通过" in surface
        for text in expected:
            assert text in surface
    assert "本次运行断言" in rendered["detail"]
    assert "不代表正式用例覆盖" in rendered["detail"]
