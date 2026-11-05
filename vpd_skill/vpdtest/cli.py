"""命令行入口：python -m vpdtest <command> ...  输出一律为 JSON（stdout）。

退出码：0 命令完成（无论用例结论）；1 使用了 --fail-on 且结论命中；2 需要现场人工接管（action_required）；
3 工具/参数错误（含命令行用法错误 UsageError）。
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback

from . import commands as c
from .util import dumps
from .workspace import Workspace


class _Parser(argparse.ArgumentParser):
    """用法错误也输出 JSON 并退出 3：退出码 2 只留给"需要人工接管"，拼错参数不能被当成接管请求。"""

    def error(self, message):
        print(json.dumps({"error": "UsageError", "message": message, "usage": self.format_usage().strip()},
                         ensure_ascii=False))
        sys.exit(3)


def _kv(s: str) -> str:
    if not s.partition("=")[0].strip() or "=" not in s:
        raise argparse.ArgumentTypeError(f"需要 key=value，收到 {s!r}")
    return s


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="vpdtest", description="VPD 手机 APP 智能测试执行器（Skill v0.1）")
    p.add_argument("--workspace", help="数据目录（默认 vpd_skill/data，或环境变量 VPD_WORKSPACE）")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="检查依赖与设备前置条件")
    d.add_argument("--device")
    d.add_argument("--app")

    u = sub.add_parser("unlock", help="按设备状态唤醒并解锁（PIN 按本地设备映射读取；--lock-secret 可显式选键名，只输一次）")
    u.add_argument("--device")
    u.add_argument("--lock-secret",
                   help="用哪个 secrets 键的 PIN（只传键名，如 LOCK_PIN_TEST；不接受 PIN 本身）")

    g = sub.add_parser("sysdiag", help="只读整屏诊断：窗口表 + 整屏树 + APP 白名单截图 + 系统弹窗登记比对")
    g.add_argument("--app", default="motor_hm")
    g.add_argument("--device")
    g.add_argument("--dismiss", action="store_true",
                   help="命中已登记且已评审的良性系统弹窗时，用原子能力关闭（默认只诊断不动作）")

    o = sub.add_parser("observe", help="采集一次观测（Agent 单步探索用）")
    o.add_argument("--app", help="省略时沿用会话 APP；新会话默认 motor_hm")
    o.add_argument("--device")
    o.add_argument("--session", default="default")
    o.add_argument("--label")

    a = sub.add_parser("act", help="执行一个受约束的动作并观测结果")
    a.add_argument("action", choices=["tap", "back", "home", "swipe", "launch", "resume", "stop_app", "input", "wait",
                                      "monitor"])
    a.add_argument("--app", help="省略时沿用会话 APP；新会话默认 motor_hm")
    a.add_argument("--device")
    a.add_argument("--session", default="default")
    a.add_argument("--target", help="targets.json 中的目标 ID")
    a.add_argument("--select", help="现场选择器（仅限有 UI 树的页面）：text=<文字> / text~=<包含> / id=<id> / xpath=<表达式>；"
                                    "多匹配/不可见/部分可见/执行前失效都不下发并返回诊断")
    a.add_argument("--within", help="--select 的查找范围：type=<控件类型> / id=<容器 id> / pane=first|last；相对 swipe 另支持 target=<已登记容器目标>")
    a.add_argument("--point", help="仅 tap/input：窗口内整数像素 x,y（px，不是0..1比例/vp）；须同会话先 observe 并给 --basis。"
                                     "受接收者与 policy 校验；匿名 Checkbox 请用已登记目标，不是裸坐标绕过；swipe 不支持")
    a.add_argument("--basis", help="坐标依据说明")
    a.add_argument("--side-effect", default="navigation",
                   help="--select/--point 目标的副作用声明（例如 user_data），须与 --authorize 一致才会放行")
    a.add_argument("--allow-input", action="store_true",
                   help="允许向 --select/--point 定位的输入框输入（非敏感测试数据；值会记入会话，密钥不能这样输入）")
    a.add_argument("--text")
    a.add_argument("--clear", action="store_true", help="input 前先清空输入框（真机 inputText 是追加）")
    a.add_argument("--direction", choices=["up", "down"], help="swipe 方向（默认 up）")
    a.add_argument("--start", help="swipe 起点相对比例 fx,fy（0..1）；须同时给 --end 和唯一已知滚动 --within，可用 target=<已登记容器目标>")
    a.add_argument("--end", help="swipe 终点相对比例 fx,fy（0..1），与 --direction/--point/--duration 互斥")
    a.add_argument("--speed", type=int, help="相对 swipe 速度整数200..40000 px/s（默认2000）；不是时长")
    a.add_argument("--expect", help="期望到达的状态 ID")
    a.add_argument("--expect-text", help="动作后可见文字中应出现的正则（未声明状态的页面用它作后置条件）")
    a.add_argument("--capture-transient", type=float, help="tap前开启短提示帧流，并采集触发后0.05..10秒；未就绪不提交，不自动重试")
    a.add_argument("--timeout", type=float, default=8)
    a.add_argument("--duration", type=float, help="wait/monitor 的时长（秒）")
    a.add_argument("--interval", type=float, default=2, help="monitor 采样间隔（秒）")
    a.add_argument("--watch", action="append", help="monitor 关注的文字 name=正则，如 tor=接管|请注意")
    a.add_argument("--authorize", nargs="*", default=[], help="本步显式授予的 gated/supervised 副作用；VPD进入/算路随任务许可，无需传入")
    a.add_argument("--supervisor", help="在场监护人：授予 vehicle_motion/vehicle_control 时必填")
    a.add_argument("--step-id")

    r = sub.add_parser("run", help="回归执行一条用例")
    r.add_argument("case")
    r.add_argument("--device")
    r.add_argument("--env", action="append", type=_kv, help="声明环境事实 key=value，如 account_state=logged_out")
    r.add_argument("--trial", help="试用一条经验候选（不采纳）")
    r.add_argument("--record-replay", action="store_true", help="把结果记入用例的 replays（晋级依据）")
    r.add_argument("--authorize", nargs="*", default=[],
                   help="本次运行显式授予：gated（account_state/vehicle_selection/user_data）"
                        "或 supervised（vehicle_motion/vehicle_control，须同时 --supervisor）；VPD进入/算路随任务许可；"
                        "不可逆账号变更/升级/支付/清数据不可授予")
    r.add_argument("--supervisor", help="在场监护人（姓名或工号）：授予 vehicle_motion/vehicle_control 时必填，记入报告")
    r.add_argument("--fail-on", nargs="*", default=[], choices=["failed", "error", "blocked", "pending", "not_applicable"])

    s = sub.add_parser("suite", help="执行用例集")
    s.add_argument("suite")
    s.add_argument("--device", action="append")
    s.add_argument("--authorize", nargs="*", default=[],
                   help="同 run：授予的副作用对集合内每条用例生效，但每条用例只拿到自己申请的那部分")
    s.add_argument("--supervisor", help="同 run：授予 vehicle_motion/vehicle_control 时必填")
    s.add_argument("--fail-on", nargs="*", default=[], choices=["failed", "error", "blocked", "pending", "not_applicable"])

    e = sub.add_parser("explore", help="在允许的页面、动作和预算内探索")
    e.add_argument("--app", default="motor_hm")
    e.add_argument("--start", required=True)
    e.add_argument("--device")
    e.add_argument("--budget", type=int, default=30)
    e.add_argument("--goal")
    e.add_argument("--prefer", nargs="*", help="优先尝试包含这些文字的元素（Agent 的路径选择）")

    cs = sub.add_parser("case", help="用例 lint / 探索转候选 / 晋级")
    csub = cs.add_subparsers(dest="case_cmd", required=True)
    x = csub.add_parser("readiness", help="只读列出成熟度、审核差异和现有晋级门槛；不验实机适用性或覆盖")
    x.add_argument("cases", nargs="*", help="预期资产ID；不传则仅枚举当前目录，不推断缺源件")
    x.add_argument("--to", choices=["candidate", "replay_verified", "regression"], help="不传则预检现有定义的下一阶段")
    x.add_argument("--reviewer")
    x.add_argument("--allow-offline", action="store_true")
    x.add_argument("--known-issue")
    x = csub.add_parser("lint")
    x.add_argument("case")
    x = csub.add_parser("from-exploration")
    x.add_argument("--run", required=True)
    x.add_argument("--transitions", nargs="+", required=True)
    x.add_argument("--new-id", required=True)
    x = csub.add_parser("from-session", help="把 observe/act 会话提炼为候选用例（保留到原始事件的关联）")
    x.add_argument("--session", required=True)
    x.add_argument("--new-id", required=True)
    x.add_argument("--app", required=True)
    x.add_argument("--goal", help="原始测试目标（文字）")
    x.add_argument("--title")
    x.add_argument("--drop", action="append", help="排除与目标无关的动作并写明理由：A4=理由 或 A4-A7=理由")
    x = csub.add_parser("review", help="只读查看资产来源、审核状态与当前绑定版本")
    x.add_argument("case")
    x = csub.add_parser("approve", help="人工审核探索资产；不晋级、不确认 Oracle、不授动作权限")
    x.add_argument("case")
    x.add_argument("--reviewer", required=True)
    x.add_argument("--basis", required=True)
    x = csub.add_parser("promote")
    x.add_argument("case")
    x.add_argument("--to", required=True, choices=["candidate", "replay_verified", "regression"])
    x.add_argument("--reviewer")
    x.add_argument("--allow-offline", action="store_true")
    x.add_argument("--known-issue", help="重放稳定复现产品失败时登记的缺陷单/Issue（用例照常判失败，不改预期）")

    ss = sub.add_parser("session", help="查看 observe/act 会话的完整轨迹")
    ss.add_argument("session")

    k = sub.add_parser("knowledge", help="知识条目管理")
    ksub = k.add_subparsers(dest="k_cmd", required=True)
    x = ksub.add_parser("search", help="检索产品要求 / 操作经验 / 现场观察，附来源、适用范围、验证状态、可调用命令")
    x.add_argument("query", nargs="+", help="检索词，空格分隔")
    x.add_argument("--app")
    x.add_argument("--role", nargs="*", choices=["requirement", "operation", "observation", "background"])
    x.add_argument("--app-version")
    x.add_argument("--device-form", choices=["bar", "foldable"])
    x.add_argument("--device", help="从设备只读取得 APP 版本与屏幕形态（需同时给 --app）")
    x.add_argument("--env", action="append", type=_kv, help="其他事实 key=value，如 account_state=logged_in")
    x.add_argument("--limit", type=int, default=8, help="每类最多返回条数")
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
    xs = [xsub.add_parser("list"), xsub.add_parser("ledger")]
    x = xsub.add_parser("validate")
    x.add_argument("id")
    x.add_argument("--suite")
    x.add_argument("--cases", nargs="*")
    x.add_argument("--device", action="append", required=True)
    xs.append(x)
    x = xsub.add_parser("adopt")
    x.add_argument("id")
    x.add_argument("--reviewer")
    xs.append(x)
    x = xsub.add_parser("rollback")
    x.add_argument("--to-version", type=int, required=True)
    x.add_argument("--reason", required=True)
    xs.append(x)
    for x in xs:  # 写在子命令之后也生效；SUPPRESS 保证不写时不覆盖父命令上的 --app
        x.add_argument("--app", default=argparse.SUPPRESS, help="同 experience --app")

    cn = sub.add_parser("console", help="本机控制台：NEXT 投屏、手动操作与运行进度（只监听 127.0.0.1）")
    cn.add_argument("--device", help="hdc:<serial>；省略且未配置设备时只显示运行进度")
    cn.add_argument("--no-device", action="store_true", help="不连接设备，只显示运行进度")
    cn.add_argument("--host", default="127.0.0.1", help="只允许 127.0.0.1 / localhost")
    cn.add_argument("--port", type=int, default=8765, help="0 表示随机端口")
    cn.add_argument("--source", choices=["auto", "stream", "snapshot"], default="auto",
                    help="auto：uitest 帧流（需 hmdriver2），失败回退 snapshot_display 轮询")
    cn.add_argument("--view-only", action="store_true", help="只看不操作")
    cn.add_argument("--max-fps", type=float, default=12.0)
    cn.add_argument("--keep-awake", type=float, metavar="MIN",
                    help="控制台运行期间把熄屏时间覆盖为 MIN 分钟，退出时恢复原值（不解锁）")
    cn.add_argument("--duration", type=float, help="运行 N 秒后自动退出（默认一直运行到 Ctrl+C）")

    t = sub.add_parser("template-crop", help="从观测截图裁剪模板")
    t.add_argument("--app", default="motor_hm")
    t.add_argument("--obs-dir", required=True)
    t.add_argument("--bounds", required=True, help="x1,y1,x2,y2")
    t.add_argument("--name", required=True)
    return p


HANDLERS = {"doctor": c.cmd_doctor, "observe": c.cmd_observe, "act": c.cmd_act, "run": c.cmd_run,
            "suite": c.cmd_suite, "explore": c.cmd_explore, "case": c.cmd_case, "knowledge": c.cmd_knowledge,
            "experience": c.cmd_experience, "template-crop": c.cmd_template_crop, "session": c.cmd_session,
            "unlock": c.cmd_unlock, "sysdiag": c.cmd_sysdiag, "console": c.cmd_console}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        ws = Workspace(args.workspace)
        if getattr(args, "authorize", None):
            ws.policy().check_grants(args.authorize)
        out = HANDLERS[args.cmd](ws, args)
    except (ValueError, PermissionError, FileNotFoundError, FileExistsError) as e:
        print(json.dumps({"error": type(e).__name__, "message": str(e)}, ensure_ascii=False))
        return 3
    except Exception as e:  # 工具缺陷：保留堆栈，不伪装成用例结论
        print(json.dumps({"error": type(e).__name__, "message": str(e), "trace": traceback.format_exc()[-2000:]},
                         ensure_ascii=False))
        return 3
    print(dumps(out))
    if (args.cmd == "case" and args.case_cmd == "readiness"
            and out.get("summary", {}).get("inspection_errors")):
        return 3  # Report all rows, but never hide an unexpected inspection fault behind exit 0.
    res = out.get("results")
    rows = [out] + ([r for r in res if isinstance(r, dict)] if isinstance(res, list) else [])
    if any(r.get("action_required") for r in rows):
        return 2  # 需要现场人工接管：任何调用方都不能把它当成普通结论
    verdicts = [r.get("verdict") for r in rows]
    return 1 if set(getattr(args, "fail_on", []) or []) & set(verdicts) else 0


if __name__ == "__main__":
    sys.exit(main())
