"""系统弹层原子能力：诊断盖在屏幕上的系统窗口，关闭已登记且已评审的良性弹窗。

背景（2026-10-08 真机，hdc:5MT0225B05000904）：USB 重插后「USB 连接方式」系统弹窗盖住被测 APP——
aa dump 仍报 APP 在前台，按 bundle 取的布局树只有全空占位根，截图按当时的隐私策略被扣留，
两次真机重放均在 S1 以 unobservable 阻塞（runs/run-20261008-201507-4990、run-20261008-201709-bec1）；
preflight 只查锁屏/前台/安装，看不见这种弹窗。治理要求（用户 2026-10-08）：
1) 无法确认的屏幕状态尽可能通过截屏取证；2) 系统弹窗用原子能力处理，不靠人工关弹窗。

能力边界（与解锁能力同一模式）：
- 诊断：窗口表（shown_windows）+ 非 bundle 作用域 dumpLayout（dump_screen）→ 识别覆盖物并落证据；
- 关闭：只点 data/system_dialogs.json 中已登记、class=benign 且 review=reviewed 的弹窗声明的
  prefer 按钮（如「取消」），forbid 按钮绝不点击；未登记、未评审、找不到安全按钮 → 保留证据并
  报告状态，不扩大操作范围。点击经 Dispatcher 下发（预算/安全暂停/锁屏前置检查全部生效）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .tree import UiTree
from .util import read_json, write_json

DIALOGS_FILE = "system_dialogs.json"


def load_dialogs(ws) -> dict:
    """已登记的系统弹窗注册表（workspace 根下 system_dialogs.json）；文件不存在 → 空表。
    ws 是 Workspace；也可以直接传 data 根目录（str/Path——注意 Path 自带 .root 属性指盘符根，不能 getattr）。"""
    root = ws if isinstance(ws, (str, Path)) else ws.root
    return read_json(Path(root) / DIALOGS_FILE, {"dialogs": {}}).get("dialogs") or {}


def match_dialog(dialogs: dict, texts: list) -> tuple:
    """按弹窗声明里的 require_any_text 匹配整屏树里出现的文字（声明的变体包含在可见文字里即命中）。"""
    for did, spec in (dialogs or {}).items():
        for variant in spec.get("match", {}).get("require_any_text") or []:
            if any(variant in t for t in texts or []):
                return did, spec
    return None, None


def dismissable(spec: dict) -> bool:
    """是否允许自动关闭：登记为良性且已评审。治理与 app 内 interrupts 的 review 门槛一致。"""
    return spec.get("class") == "benign" and spec.get("review") == "reviewed"


def pick_dismiss(spec: dict, tree: UiTree) -> Optional[dict]:
    """在整屏树里找弹窗声明的安全关闭按钮：按 prefer_text 顺序取第一个可见可点按钮；
    命中 forbid_text 的按钮绝不作为候选（例如「传输文件」会改变 USB 工作模式）。
    按钮的匹配文字取自身 text/description，为空时取子树文字（真机与回放树的 Button 常把文字放子节点）。"""
    prefer = (spec.get("dismiss") or {}).get("prefer_text") or []
    forbid = (spec.get("dismiss") or {}).get("forbid_text") or []
    if not prefer:
        return None
    candidates = []
    for n in tree.nodes:
        if not n.clickable or not n.enabled or not n.visible or not tree.visible_rect(n):
            continue
        label = n.text.strip() or "".join(tree.subtree_texts(n))
        if not label or any(f in label for f in forbid):
            continue
        candidates.append((n, label))
    for want in prefer:
        for n, label in candidates:
            if want in label:
                rect = tree.visible_rect(n)
                return {"text": label, "button": n.text.strip(), "point": ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2),
                        "bounds": list(rect), "prefer": want}
    return None


def _diag_dir(store, seq: int) -> Path:
    d = store.dir / "diag" / str(seq)
    d.mkdir(parents=True, exist_ok=True)
    return d


def diagnose_cover(device, store, d: Path, dialogs: dict) -> dict:
    """只读诊断：窗口表仅记录现场，任何窗口及查询未知均尝试整屏采集。

    整屏原文树落在 d/screen_tree.json；_tree 仅内部用于选择已登记弹窗按钮，
    不代替目标 bundle 树参与业务状态识别或产品断言。"""
    windows = device.shown_windows() if hasattr(device, "shown_windows") else None
    dump_ok, error_class, tree = False, "unsupported", None
    if hasattr(device, "dump_screen"):
        d.mkdir(parents=True, exist_ok=True)
        r = device.dump_screen(d / "screen_tree.json")
        dump_ok, error_class = bool(r.ok), r.error_class
        if dump_ok:
            raw = r.data.get("tree")
            write_json(d / "screen_tree.json", raw)
            tree = UiTree(raw)
    texts = tree.visible_texts()[:60] if dump_ok else []
    did, spec = match_dialog(dialogs, texts) if dump_ok else (None, None)
    out = {"windows": windows, "dump_ok": dump_ok, "dump_error": None if dump_ok else error_class,
           "texts": texts, "matched": did,
           "screen_tree": store.rel(d / "screen_tree.json") if dump_ok else None}
    if did:
        out["dialog"] = {"id": did, "class": spec.get("class"), "review": spec.get("review"),
                         "dismissable": dismissable(spec)}
    if tree is not None:
        out["_tree"] = tree
    return out


def _public(diag: dict) -> dict:
    """诊断结果去掉内部字段（_tree），用于事件与返回值。"""
    return {k: v for k, v in diag.items() if not k.startswith("_")}


def resolve_screen_cover(ws, device, store, dispatcher=None, dialogs=None) -> dict:
    """能力入口：诊断覆盖物；命中已登记良性弹窗时点它的安全按钮并复核已消失。
    状态：dismissed（已关闭并复核）/ no_cover（无覆盖或未命中登记）/ no_safe_button /
    needs_review（命中但未评审/非良性）/ dismiss_failed / indeterminate（采集失败）。"""
    from .dispatch import Dispatcher
    dispatcher = dispatcher or Dispatcher(device, store)
    dialogs = load_dialogs(ws) if dialogs is None else dialogs
    store.event("capability_start", capability="system_dialog", device=device.spec)
    seq = 0

    def next_diag():
        nonlocal seq
        seq += 1
        return diagnose_cover(device, store, _diag_dir(store, seq), dialogs)

    diag = next_diag()
    did, tree = diag.get("matched"), diag.get("_tree")
    res = {"device": device.spec, "diagnosis": _public(diag)}
    if not diag["dump_ok"]:
        status, reason = "indeterminate", f"整屏树采集失败（{diag['dump_error']}），不猜"
    elif did is None:
        status = "no_cover"
        reason = "整屏树未见已登记弹窗" + (f"；覆盖物文字：{diag['texts'][:5]}" if diag["texts"] else "（整屏树无文字）")
    elif not dismissable(dialogs.get(did) or {}):
        status, reason = "needs_review", f"弹窗 {did} 已登记但未评审/非良性，不自动关闭"
    else:
        btn = pick_dismiss(dialogs[did], tree) if tree is not None else None
        if btn is None:
            status, reason = "no_safe_button", f"弹窗 {did} 树里找不到声明的安全关闭按钮（prefer）"
        else:
            # 点击前复核：采集树到下发之间弹窗可能已自行消失/变化——按陈旧坐标盲点会误中底下的
            # 控件（可能是带副作用的）。重新诊断并重选按钮；弹窗没了/换了就不点。
            fresh = next_diag()
            fdid, ftree = fresh.get("matched"), fresh.get("_tree")
            fbtn = pick_dismiss(dialogs[fdid], ftree) if (fdid and ftree is not None) else None
            if not fresh["dump_ok"]:
                status, reason = "indeterminate", f"点击前复核采集失败（{fresh['dump_error']}），不按旧坐标盲点"
            elif fdid is None:
                status, reason = "no_cover", f"弹窗 {did} 在点击前已自行消失，无需关闭"
            elif fdid != did:
                status, reason = "dismiss_failed", f"点击前覆盖物已从 {did} 变为 {fdid}，登记的关闭方案不再适用"
            elif fbtn is None:
                status, reason = "no_safe_button", f"弹窗 {did} 点击前复核未再见安全关闭按钮（prefer）"
            else:
                btn = fbtn  # 用复核后的新坐标下发
                r = dispatcher.send("tap", *btn["point"], preconditions={"screen_locked": False},
                                    att={"action": "dismiss_system_dialog", "step_id": "system_dialog",
                                         "dialog": did, "button_text": btn["text"]})
                res["dismiss"] = {"button": btn["text"], "sent": bool(r.sent), "ok": bool(r.ok),
                                  "error_class": r.error_class}
                if not r.ok:
                    status, reason = "dismiss_failed", f"关闭按钮点击未完成：{r.error_class}"
                else:
                    _sleep(device, 0.8)
                    again = next_diag()
                    res["recheck"] = {k: again.get(k) for k in ("windows", "dump_ok", "matched", "texts")}
                    if not again["dump_ok"]:
                        # 复核采集失败：点击效果未知，不谎报“已关闭并复核消失”
                        status, reason = "indeterminate", f"点击后复核采集失败（{again['dump_error']}），关闭效果未知"
                    elif again.get("matched") == did:
                        status, reason = "dismiss_failed", f"点击后弹窗 {did} 仍在显示"
                    else:
                        status, reason = "dismissed", f"已关闭弹窗 {did}（按钮「{btn['text']}」）并复核消失"
    res.update(status=status, reason=reason)
    store.event("capability_result", capability="system_dialog", **{k: v for k, v in res.items()})
    return res


def preflight_dialog_check(ws, device, store, dispatcher=None, dialogs=None) -> dict:
    """preflight 快查：窗口表里出现已登记弹窗的窗口名才做整屏诊断并关闭。
    未命中/查询失败不阻塞（preflight 全绿≠屏幕没被盖住），动作后的 unobservable 恢复会兜底。"""
    windows = device.shown_windows() if hasattr(device, "shown_windows") else None
    if windows is None:
        return {"status": "skipped", "reason": "窗口查询失败，交给动作后的恢复诊断兜底", "windows": None}
    dialogs = load_dialogs(ws) if dialogs is None else dialogs
    # 真机窗口名带递增后缀（SCBSysDialogDefault2 / SCBScreenLock12…）：按登记名前缀匹配
    hits = [did for did, spec in dialogs.items()
            if spec.get("window_names")
            and any(w.startswith(n) for n in spec["window_names"] for w in windows)]
    if not hits:
        return {"status": "no_cover", "windows": windows}
    return resolve_screen_cover(ws, device, store, dispatcher=dispatcher, dialogs=dialogs)


def _sleep(device, seconds: float) -> None:
    if getattr(device, "poll_interval", 0) > 0:
        import time
        time.sleep(seconds)
    # 回放设备不真等待
