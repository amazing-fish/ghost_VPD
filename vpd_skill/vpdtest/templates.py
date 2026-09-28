"""截图模板定位：用于无 UI 树或 UI 树信息不足的界面。

复用 app/drivers/img_parser/img_parser.py 的多尺度 TM_CCOEFF_NORMED 思路，并修正三点：
  1. 原实现返回匹配区域左上角并直接点击，这里返回中心点；
  2. 原实现不返回得分，这里返回 score/scale，供证据与经验记录；
  3. 原实现阈值判断在 break 与最终判定处不一致（>= 与 <=），这里统一为 score >= threshold，
     并在次优匹配接近最优时报告 ambiguous，不盲点。
不依赖 app 包，因此不会触发原工程的 Flask/MySQL/Redis 导入副作用。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def _imread(path, gray: bool = True):
    import cv2
    import numpy as np
    data = np.fromfile(str(path), dtype=np.uint8)  # 兼容中文 Windows 路径
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"无法读取图片: {path}")
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if gray else img


def match_template(screenshot_path, template_path, threshold: float = 0.85, region: Optional[tuple] = None,
                   scales: tuple = (0.5, 2.0, 31), ambiguity_margin: float = 0.03) -> dict:
    """返回 {status: found|not_found|ambiguous|error, point, bounds, score, scale, second_score}。"""
    import cv2
    import numpy as np
    try:
        shot, tmpl = _imread(screenshot_path), _imread(template_path)
    except (FileNotFoundError, OSError) as e:
        return {"status": "error", "error": str(e)}
    ox, oy = 0, 0
    if region:
        x1, y1, x2, y2 = [int(v) for v in region]
        shot, ox, oy = shot[y1:y2, x1:x2], x1, y1
    best = {"score": -1.0}
    th, tw = tmpl.shape[:2]
    for scale in np.linspace(scales[0], scales[1], int(scales[2])):
        w, h = int(tw * scale), int(th * scale)
        if w < 4 or h < 4 or h > shot.shape[0] or w > shot.shape[1]:
            continue
        res = cv2.matchTemplate(shot, cv2.resize(tmpl, (w, h)), cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if score > best["score"]:
            best = {"score": float(score), "loc": loc, "w": w, "h": h, "scale": float(scale), "res": res}
        if best["score"] >= 0.99:
            break
    if best["score"] < 0:
        return {"status": "not_found", "score": None, "reason": "模板比截图区域大"}
    x, y = best["loc"]
    out = {"score": round(best["score"], 4), "scale": round(best["scale"], 3),
           "bounds": [x + ox, y + oy, x + ox + best["w"], y + oy + best["h"]],
           "point": [x + ox + best["w"] // 2, y + oy + best["h"] // 2]}
    res = best["res"].copy()
    res[max(0, y - best["h"] // 2):y + best["h"] // 2 + 1, max(0, x - best["w"] // 2):x + best["w"] // 2 + 1] = -1
    second = float(res.max()) if res.size else -1.0
    out["second_score"] = round(second, 4)
    if best["score"] < threshold:
        out["status"] = "not_found"
    elif second >= threshold and best["score"] - second < ambiguity_margin:
        out["status"] = "ambiguous"
    else:
        out["status"] = "found"
    return out


def crop(screenshot_path, bounds, out_path) -> Path:
    """从一次观测的截图裁剪模板；来源截图路径应记录到 targets.json 的 source 字段。"""
    import cv2
    img = _imread(screenshot_path, gray=False)
    x1, y1, x2, y2 = [int(v) for v in bounds]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", img[y1:y2, x1:x2])[1].tofile(str(out))
    return out
