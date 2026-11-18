# -*- coding: utf-8 -*-
"""表情表识别：按「颜色 + 形状指纹」区分固定几种表情。

设计前提（真机实测）：
    企业微信表情只有 ~22×15 像素，**无法**靠形状区分"手势是几根手指"。
    但不同表情的**主色调**差异明显（蓝/黄/橙/粉/灰），颜色在 15px 高度下
    仍然稳定（每块有 100+ 个彩色像素，颜色均值方差很小）。

所以策略是「颜色为主 + 形状为辅 + 白名单约束」：
    ① 用颜色直方图和模板比对 → 主色相同才继续
    ② 用高饱和像素的分布形状（宽高比、填充率、中心位置）做二次区分
    ③ **只认表里有的**，认不出就返回 None（宁可不回，也不错回）
    ④ 尺寸/位置异常直接放弃

表从 %APPDATA%\\AutoGrab\\emoji_map.json 读，格式：
    {
      "emoji": [
        {"code": "5", "name": "蓝色OK", "hue": "blue", "ink_ratio": [0.30, 0.55]},
        {"code": "2", "name": "黄色V",  "hue": "yellow", ...}
      ]
    }
也可以直接放进 config.json 的 emoji_map 字段。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np

# 表情区尺寸范围（真机实测 19~28 宽、11~19 高）
MIN_W = 12
MAX_W = 42
MIN_H = 9
MAX_H = 30
# 高饱和像素占比下限（纯文字 ≈ 0~10%，表情 ≈ 20~40%）
MIN_SAT_RATIO = 0.16

# 色相桶
HUE_BUCKETS = {
    "red":    (330, 15),
    "orange": (15, 45),
    "yellow": (45, 70),
    "green":  (70, 160),
    "cyan":   (160, 200),
    "blue":   (200, 260),
    "purple": (260, 300),
    "pink":   (300, 330),
}


def _hsv_hue(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (hue 0-360, sat 0-1, val 0-1)。"""
    a = rgb.astype(np.float32) / 255.0
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx = np.max(a, axis=2)
    mn = np.min(a, axis=2)
    d = mx - mn
    h = np.zeros_like(mx)
    mask = d > 1e-6
    rmax = (mx == r) & mask
    gmax = (mx == g) & mask & ~rmax
    bmax = (mx == b) & mask & ~rmax & ~gmax
    h[rmax] = ((g[rmax] - b[rmax]) / d[rmax]) % 6
    h[gmax] = ((b[gmax] - r[gmax]) / d[gmax]) + 2
    h[bmax] = ((r[bmax] - g[bmax]) / d[bmax]) + 4
    h = h * 60.0
    s = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0)
    return (h, s, mx)


def hue_name(hue_deg: float) -> str:
    for name, (lo, hi) in HUE_BUCKETS.items():
        if lo < hi:
            if lo <= hue_deg < hi:
                return name
        else:                        # red 跨 0
            if hue_deg >= lo or hue_deg < hi:
                return name
    return "unknown"


def find_emoji_box(rgb: np.ndarray, right_zone: float = 0.45) -> tuple[int, int, int, int] | None:
    """在一行消息里找表情块（高饱和、尺寸像表情）。

    right_zone：只在画面右侧这个比例之后找（默认 0.45 = 右半部分）。
                传 0.0 表示全图搜索（用于已经把表情单独裁出来的场景，
                比如离线验证、模板样本）。

    与最早的实现差别：不要求"纯色块"，而是找**高饱和像素的密集列段**，
    允许里面混着图形的白色部分（表情的轮廓是白的）。
    """
    if rgb.ndim != 3 or rgb.shape[0] < MIN_H or rgb.shape[1] < MIN_W:
        return None
    h, w = rgb.shape[:2]
    hue, sat, val = _hsv_hue(rgb)
    colorful = (sat > 0.30) & (val > 0.25)
    if int(colorful.sum()) < 8:
        return None

    colcnt = colorful.sum(axis=0)
    thr_col = max(2, int(h * 0.30))
    hit = colcnt >= thr_col

    start = int(w * max(0.0, min(right_zone, 0.95)))
    best = None
    run_start = None
    for x in range(start, w + 1):
        on = x < w and hit[x]
        if on and run_start is None:
            run_start = x
        elif not on and run_start is not None:
            cand = _try_seg(colorful, run_start, x)
            if cand and (best is None or cand[4] > best[4]):
                best = cand
            run_start = None

    # 处理一直延伸到右边的段
    if run_start is not None:
        cand = _try_seg(colorful, run_start, w)
        if cand and (best is None or cand[4] > best[4]):
            best = cand

    if best is None:
        return None
    x1, y1, x2, y2, _fill = best
    # 向外扩 2px 把抗锯齿边缘和白边包进来（否则不同表情的框会切掉不同部分）
    x1 = max(0, x1 - 2)
    y1 = max(0, y1 - 2)
    x2 = min(w, x2 + 2)
    y2 = min(h, y2 + 2)
    if (x2 - x1) > MAX_W + 4 or (y2 - y1) > MAX_H + 4:
        # 扩完超限说明原来就是误框，退回不扩
        x1 = max(0, best[0])
        y1 = max(0, best[1])
        x2 = best[2]
        y2 = best[3]
    return (x1, y1, x2, y2)


def _try_seg(colorful: np.ndarray, x1: int, x2: int):
    """判断某一段列是否像一个表情，返回 (x1,y1,x2,y2,fill) 或 None。"""
    bw = x2 - x1
    if bw < MIN_W or bw > MAX_W:
        return None
    seg = colorful[:, x1:x2]
    if seg.size == 0:
        return None
    rowcnt = seg.sum(axis=1)
    ys = np.flatnonzero(rowcnt >= max(1, int(bw * 0.20)))
    if ys.size == 0:
        return None
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    bh = y2 - y1
    if bh < MIN_H or bh > MAX_H:
        return None
    fill = float(seg[y1:y2].mean())
    if fill < MIN_SAT_RATIO * 0.6:
        return None
    return (x1, y1, x2, y2, fill)


def describe(rgb: np.ndarray, box) -> dict:
    """把一个表情块量化成可比较的特征。"""
    x1, y1, x2, y2 = box
    patch = rgb[y1:y2, x1:x2]
    if patch.size == 0:
        return {}
    h, w = patch.shape[:2]
    hue, sat, val = _hsv_hue(patch)
    colorful = (sat > 0.30) & (val > 0.25)
    ratio = float(colorful.mean())
    if int(colorful.sum()) < 8:
        return {}

    hs = hue[colorful]
    ss = sat[colorful]
    vs = val[colorful]

    # 主色相：用中位数更稳（抗噪）
    main_hue = float(np.median(hs))
    # 蓝色/青色需要特殊处理：企业微信的"蓝色手势"hue 在 190-215
    return {
        "w": w, "h": h,
        "ratio": round(ratio, 3),
        "aspect": round(w / max(1, h), 2),
        "hue": round(main_hue, 1),
        "hue_name": hue_name(main_hue),
        "sat": round(float(np.median(ss)), 3),
        "val": round(float(np.median(vs)), 3),
        # 色相分布：主色占多少
        "hue_conc": round(float((np.abs(hs - main_hue) < 25).mean()), 3),
        # 形状：彩色像素的填充率 / 上下重心
        "cy": round(float(np.argwhere(colorful)[:, 0].mean()) / max(1, h), 3),
    }


# ---------------------------------------------------------------- 表情表
def _load_table() -> list[dict]:
    """读表情表：优先 config.json 的 emoji_map，其次 emoji_map.json。"""
    cands = []
    base = os.environ.get("APPDATA")
    if base:
        cands.append(Path(base) / "AutoGrab" / "emoji_map.json")
        cands.append(Path(base) / "AutoGrab" / "config.json")
    for p in cands:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if p.name == "config.json":
            data = data.get("emoji_map")
        if isinstance(data, dict):
            items = data.get("emoji") or data.get("items") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        if items:
            return items
    return []


def classify(rgb: np.ndarray, right_zone: float = 0.45) -> tuple[str | None, dict, str]:
    """识别表情 → (号码, 特征, 说明)。

    只认表情表里有的。表为空时一律返回 None（不猜）。
    """
    box = find_emoji_box(rgb, right_zone=right_zone)
    if box is None:
        return (None, {}, "没找到表情块")

    feat = describe(rgb, box)
    if not feat:
        return (None, {}, "表情块特征不足")

    table = _load_table()
    if not table:
        return (None, feat, "表情表为空（需配置 emoji_map）")

    best = None
    for item in table:
        score = 0.0
        # 色相必须接近
        want_hue = item.get("hue_deg")
        if want_hue is not None:
            diff = abs(((feat["hue"] - float(want_hue) + 180) % 360) - 180)
            if diff > 40:
                continue
            score += (40 - diff) / 40
        # 色相名必须一致（若表里给了）
        want_name = item.get("hue")
        if want_name and feat["hue_name"] != want_name:
            continue
        score += 0.5
        # 填充率区间
        rng = item.get("ink_ratio")
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            lo, hi = float(rng[0]), float(rng[1])
            if not (lo <= feat["ratio"] <= hi):
                continue
            score += 0.3
        # 宽高比区间
        arng = item.get("aspect")
        if isinstance(arng, (list, tuple)) and len(arng) == 2:
            lo, hi = float(arng[0]), float(arng[1])
            if not (lo <= feat["aspect"] <= hi):
                continue
            score += 0.2
        if best is None or score > best[0]:
            best = (score, item)

    if best is None:
        return (None, feat, "不在表情表里")
    item = best[1]
    code = str(item.get("code") or item.get("value") or "").strip()
    if not code:
        return (None, feat, "表项缺 code")
    return (code, feat, f"命中 {item.get('name') or code} (score={best[0]:.2f})")