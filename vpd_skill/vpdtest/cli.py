"""命令行入口：python -m vpdtest <command> ...  输出一律为 JSON（stdout）。

退出码：0 命令完成（无论用例结论）；1 使用了 --fail-on 且结论命中；3 工具/参数错误。
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback

from . import commands as c
from .util import dumps
from .workspace import Workspace


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vpdtest", description="VPD 手机 APP 智能测试执行器（Skill v0.1）")
    p.add_argument("--workspace", help="数据目录（默认 vpd_skill/data，或环境变量 VPD_WORKSPACE）")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="检查依赖与设备前置条件")
    d.add_argument("--device")
    d.add_argument("--app")

    o = sub.add_parser("observe", help="采集一次观测（Agent 单步探索用）")
    o.add_argument("--app", default="motor_hm")
    o.add_argument("--device")
    o.add_argument("--session", default="default")
    o.add_argument("--label")

    a = sub.add_parser("act", help="执行一个受约束的动作并观测结果")
    a.add_argument("action", choices=["tap", "back", "home", "swipe", "launch", "stop_app", "input", "wait"])
    a.add_argument("--app", default="motor_hm")
    a.add_argument("--device")
    a.add_argument("--session", default="default")
    a.add_argument("--target", help="targets.json 中的目标 ID")
    a.add_argument("--point", help="x,y：Agent 根据截图提议的坐标（须同时给 --basis，受 policy 约束）")
    a.add_argument("--basis", help="坐标依据说明")
    a.add_argument("--text")
    a.add_argument("--expect", help="期望到达的状态 ID")
    a.add_argument("--timeout", type=float, default=8)
    a.add_argument("--step-id")

    r = sub.add_parser("run", help="回归执行一条用例")
    r.add_argument("case")
    r.add_argument("--device")
    r.add_argument("--env", action="append", help="声明环境事实 key=value，如 account_state=logged_out")
    r.add_argument("--trial", help="试用一条经验候选（不采纳）")
    r.add_argument("--record-replay", action="store_true", help="把结果记入用例的 replays（晋级依据）")
    r.add_argument("--fail-on", nargs="*", default=[], choices=["failed", "blocked", "pending"])

    s = sub.add_parser("suite", help="执行用例集")
    s.add_argument("suite")
    s.add_argument("--device", action="append")
    s.add_argument("--fail-on", nargs="*", default=[], choices=["failed", "blocked", "pending"])

    e = sub.add_parser("explore", help="在允许的页面、动作和预算内探索")
    e.add_argument("--app", default="motor_hm")
    e.add_argument("--start", required=True)
    e.add_argument("--device")
    e.add_argument("--budget", type=int, default=30)
    e.add_argument("--goal")
    e.add_argument("--prefer", nargs="*", help="优先尝试包含这些文字的元素（Agent 的路径选择）")

    cs = sub.add_parser("case", help="用例 lint / 探索转候选 / 晋级")
    csub = cs.add_subparsers(dest="case_cmd", required=True)
    x = csub.add_parser("lint")
    x.add_argument("case")
    x = csub.add_parser("from-exploration")
    x.add_argument("--run", required=True)
    x.add_argument("--transitions", nargs="+", required=True)
    x.add_argument("--new-id", required=True)
    x = csub.add_parser("promote")
    x.add_argument("case")
    x.add_argument("--to", required=True, choices=["candidate", "replay_verified", "regression"])
    x.add_argument("--reviewer")
    x.add_argument("--allow-offline", action="store_true")

    k = sub.add_parser("knowledge", help="知识条目管理")
    ksub = k.add_subparsers(dest="k_cmd", required=True)
    ksub.add_parser("list")
    x = ksub.add_parser("show")
    x.add_argument("id")
    x = ksub.add_parser("add")
    x.add_argument("file")
    x = ksub.add_parser("confirm")
    x.add_argument("id")
    x.add_argument("--reviewer", required=True)
    x.add_argument("--basis")
    x = ksub.add_parser("resolve")
    x.add_argument("--subject", required=True)
    x.add_argument("--keep", required=True)
    x.add_argument("--reviewer", required=True)
    x.add_argument("--reason", required=True)
    x = ksub.add_parser("import-repo")
    x.add_argument("--repo", required=True)
    x.add_argument("--file", required=True)
    x.add_argument("--var", required=True)

    xp = sub.add_parser("experience", help="经验候选：验证 / 采纳 / 回滚")
    xp.add_argument("--app", default="motor_hm")
    xsub = xp.add_subparsers(dest="x_cmd", required=True)
    xsub.add_parser("list")
    xsub.add_parser("ledger")
    x = xsub.add_parser("validate")
    x.add_argument("id")
    x.add_argument("--suite")
    x.add_argument("--cases", nargs="*")
    x.add_argument("--device", action="append", required=True)
    x = xsub.add_parser("adopt")
    x.add_argument("id")
    x.add_argument("--reviewer")
    x = xsub.add_parser("rollback")
    x.add_argument("--to-version", type=int, required=True)
    x.add_argument("--reason", required=True)

    t = sub.add_parser("template-crop", help="从观测截图裁剪模板")
    t.add_argument("--app", default="motor_hm")
    t.add_argument("--obs-dir", required=True)
    t.add_argument("--bounds", required=True, help="x1,y1,x2,y2")
    t.add_argument("--name", required=True)
    return p


HANDLERS = {"doctor": c.cmd_doctor, "observe": c.cmd_observe, "act": c.cmd_act, "run": c.cmd_run,
            "suite": c.cmd_suite, "explore": c.cmd_explore, "case": c.cmd_case, "knowledge": c.cmd_knowledge,
            "experience": c.cmd_experience, "template-crop": c.cmd_template_crop}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        out = HANDLERS[args.cmd](Workspace(args.workspace), args)
    except (ValueError, PermissionError, FileNotFoundError, FileExistsError) as e:
        print(json.dumps({"error": type(e).__name__, "message": str(e)}, ensure_ascii=False))
        return 3
    except Exception as e:  # 工具缺陷：保留堆栈，不伪装成用例结论
        print(json.dumps({"error": type(e).__name__, "message": str(e), "trace": traceback.format_exc()[-2000:]},
                         ensure_ascii=False))
        return 3
    print(dumps(out))
    verdicts = [out.get("verdict")] + [r.get("verdict") for r in out.get("results", [])]
    return 1 if set(getattr(args, "fail_on", []) or []) & set(verdicts) else 0


if __name__ == "__main__":
    sys.exit(main())
