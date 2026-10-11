"""离线闭环演示：在独立工作区里通过 CLI 走完"判定 → 恢复 → 经验 → 探索 → 候选用例重放"。

全部使用回放替身设备，结论只证明协议与闭环；"确认知识""评审中断"这两步模拟人工评审，只写入演示工作区，
不修改 vpd_skill/data。用法：.venv\\Scripts\\python.exe scripts\\demo_offline.py [输出目录]
"""
import contextlib
import io
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
from vpdtest.cli import main  # noqa: E402
from vpdtest.util import read_json, write_json  # noqa: E402

out_root = Path(sys.argv[1]) if len(sys.argv) > 1 else SKILL / "runs" / f"demo-{datetime.now():%Y%m%d-%H%M%S}"
shutil.copytree(SKILL / "data", out_root / "data")
WS = str(out_root / "data")
rows = []


def cli(label: str, *args) -> dict:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["--workspace", WS, *args])
    out = json.loads(buf.getvalue())
    brief = out.get("label") or out.get("error") or ""
    if "issues" in out:
        brief = f"{out.get('status', '')} 待补齐 {len(out['issues'])} 项：" + "、".join(i["field"] for i in out["issues"])
    elif "coverage" in out:
        c = out["coverage"]
        brief = (f"{out['exit']['reason']}；到达已声明状态 {len(c['visited_declared'])}/{len(c['declared'])}，"
                 f"迁移 {c['transitions']}，策略跳过 {c['skipped_by_policy']}，未声明页面 {len(c['unknown_states'])}")
    elif "version" in out and "items" in out:
        brief = f"overlay v{out['version']} items={out['items']}"
    elif out.get("status") and not brief:
        brief = out["status"] + (f"（{out['reason']}）" if out.get("reason") else "")
    if out.get("error"):
        brief += f"：{out.get('message', '')[:80]}"
    rows.append((label, "vpdtest " + " ".join(args), f"{brief}", out.get("report", ""), code))
    print(f"[{code}] {label}: {brief}")
    return out


B, D = "replay:motor_hm_base.json", "replay:motor_hm_drift.json"
cli("判据未确认", "run", "TC-MOTOR-HM-001", "--device", B)
for k in ("K-MOT-001", "K-MOT-002", "K-MOT-003"):
    cli(f"模拟评审：确认 {k}", "knowledge", "confirm", k, "--reviewer", "demo-reviewer（演示）")
cli("满足预期", "run", "TC-MOTOR-HM-001", "--device", B)
cli("违反已确认文案", "run", "TC-MOTOR-HM-003", "--device", "replay:motor_hm_typo.json")
cli("锁屏", "run", "TC-MOTOR-HM-001", "--device", "replay:motor_hm_locked.json")
cli("环境事实不满足", "run", "TC-MOTOR-HM-003", "--device", B, "--env", "account_state=logged_in")
cli("崩溃", "run", "TC-MOTOR-HM-001", "--device", "replay:motor_hm_crash.json")
cli("未评审弹窗", "run", "TC-MOTOR-HM-001", "--device", "replay:motor_hm_popup.json")
states = out_root / "data" / "apps" / "motor_hm" / "states.json"
s = read_json(states)
s["interrupts"]["update_dialog"]["review"] = "reviewed"
write_json(states, s)
cli("弹窗经评审后恢复", "run", "TC-MOTOR-HM-001", "--device", "replay:motor_hm_popup.json")
cli("小直板（360 vp，「设置」被底部导航遮住）", "run", "TC-MOTOR-HM-001", "--device", "replay:motor_hm_compact.json")
cli("折叠屏展开（733 vp，左右分栏）", "run", "TC-MOTOR-HM-001", "--device", "replay:motor_hm_fold.json")
scoped = cli("新版本 3.0.4.100：判据只覆盖 3.0.3.200（应为待确认）", "run", "TC-MOTOR-HM-001", "--device", D)
kp = out_root / "data" / "knowledge" / "items.json"
kd = read_json(kp)
for it in kd["items"]:
    if it["id"] in ("K-MOT-001", "K-MOT-002"):  # 模拟评审：确认这两条判据对新版本仍适用
        it["applies_to"]["app_version"] = ["3.0.3.200", "3.0.4.100"]
write_json(kp, kd)
drift = cli("页面变化（定位漂移）", "run", "TC-MOTOR-HM-001", "--device", D)
xid = (scoped["experience_candidates"] or drift["experience_candidates"])[0]  # 同一经验第二次出现只追加证据
cli("经验验证（3 用例 × 2 世界）", "experience", "validate", xid, "--suite", "motor_hm_smoke", "--device", B, "--device", D)
cli("经验采纳", "experience", "adopt", xid)
cli("采纳后重跑漂移", "run", "TC-MOTOR-HM-001", "--device", D)
cli("经验回滚", "experience", "rollback", "--to-version", "0", "--reason", "演示回滚")
exp = cli("探索", "explore", "--start", "car_tab", "--budget", "60", "--goal", "我的页导航", "--device", B)
t1 = next(t["id"] for t in exp["transitions"] if (t["from"], t["to"]) == ("car_tab", "my_tab"))
t2 = next(t["id"] for t in exp["transitions"] if (t["from"], t["to"]) == ("my_tab", "settings"))
cli("探索转记录", "case", "from-exploration", "--run", exp["run_id"], "--transitions", t1, t2, "--new-id", "TC-EXP-001")
cli("晋级前检查", "case", "lint", "TC-EXP-001")
cp = out_root / "data" / "cases" / "TC-EXP-001.json"
case = read_json(cp)  # 模拟 Agent 补齐目的/判据/清理，并把内联目标替换为已登记目标
case.update(purpose="从爱车页经我的页进入设置，确认设置页可见退出登录", test_data_none=True,
            cleanup=[{"action": "stop_app"}],
            assertions=[{"id": "A1", "at": "S3", "type": "target_present", "target": "logout_entry",
                         "desc": "设置页可见「退出登录」", "oracle_refs": ["K-MOT-002"]}])
case["steps"][1]["target"], case["steps"][2]["target"] = "tab_my", "settings_entry"
write_json(cp, case)
cli("补齐后晋级候选", "case", "promote", "TC-EXP-001", "--to", "candidate")
cli("新运行重放 #1", "run", "TC-EXP-001", "--device", B, "--record-replay")
cli("新运行重放 #2", "run", "TC-EXP-001", "--device", D, "--record-replay")
cli("晋级重放已验证", "case", "promote", "TC-EXP-001", "--to", "replay_verified")
cli("无真机重放时晋级正式回归（应被拒绝）", "case", "promote", "TC-EXP-001", "--to", "regression", "--reviewer", "demo")

lines = ["# 离线闭环演示（回放替身设备，不代表真实产品行为）", "", f"工作区：`{WS}`", "",
         "| 场景 | 命令 | 结果 | 报告 | 退出码 |", "|---|---|---|---|---|"]
lines += [f"| {a} | `{b}` | {c} | {d} | {e} |" for a, b, c, d, e in rows]
(out_root / "demo_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"\nsummary: {out_root / 'demo_summary.md'}")
