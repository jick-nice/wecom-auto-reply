# -*- coding: utf-8 -*-
"""表情表识别 v2：颜色分组 + 形状指纹匹配。

实测数据（真机 4 个样本，深色背景）：
    e0 = 👌 OK     16x17  sat=142  val=245  RGB(210,161,74)   顶部峰=3
    e1 = ✌️ V      12x16  sat=146  val=235  RGB(210,158,73)   顶部峰=2
    e2 = 🖐 手掌    13x14  sat=61   val=253  RGB(225,189,170)  顶部峰=1
    e3 = ✌️ 小V    11x14  sat=61   val=251  RGB(220,184,165)  顶部峰=1

两组颜色几乎正交：高饱和(黄色系 sat~145) vs 低饱和(浅肤 sat~61)。
组内靠形状：12x12 归一化指纹 + 顶部峰数 + 宽高比 + 四象限填充。

注意：e1 与 e3 指纹差异只有 0.104，**但它们的颜色完全不同**（sat 146 vs 61），
所以用"先颜色分组、再形状细判"两级策略，整体区分度是够的。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

# ---------------------------------------------------------------- 参数
MIN_W, MAX_W = 9, 34
MIN_H, MAX_H = 9, 32
NORM = 12

# 两级判据的门槛
SAT_SPLIT = 100.0        # 饱和度 >= 这个值 → 高饱和组（黄色）；否则低饱和组（浅肤）
SAT_TOL = 45.0           # 同组内容忍的饱和度偏差
SHAPE_MIN_IOU = 0.55     # 形状指纹的最低相似度
SHAPE_MARGIN = 0.06      # 最佳必须比次佳好这么多

_FEAT_CACHE: list | None = None


def _gray_or_rgb(rgb: np.ndarray) -> np.ndarray:
    return np.asarray(rgb) if rgb.ndim == 3 else np.stack([rgb] * 3, axis=2)


def _bg_lum(rgb: np.ndarray) -> float:
    """估计背景亮度：用四角中位数。"""
    h, w = rgb.shape[:2]
    k = max(1, min(3, h // 4, w // 4))
    corners = np.concatenate([
        rgb[:k, :k].reshape(-1, 3), rgb[:k, -k:].reshape(-1, 3),
        rgb[-k:, :k].reshape(-1, 3), rgb[-k:, -k:].reshape(-1, 3)])
    return float(np.median(corners.mean(axis=1)))


def foreground(patch: np.ndarray) -> np.ndarray:
    """Extract emoji pixels relative to the local background, regardless of hue.

    Saturation alone is not foreground: colored chat themes (notably green) can
    be more saturated than the emoji itself and otherwise fill the whole mask.
    """
    a = _gray_or_rgb(np.asarray(patch)).astype(np.int16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    # Skin/yellow and blue hands only. In particular, green chat bubbles and
    # neutral text/outlines are not seeds. Never estimate background from a
    # tight crop, where skin itself can be the most frequent color.
    seed = ((r > g + 9) & (g > b + 6) & (r - b > 22)) | (
        (b > g + 8) & (g > r + 6) & (b - r > 22))
    return seed



def find_boxes(rgb: np.ndarray, right_zone: float = 0.0) -> list[tuple[int, int, int, int]]:
    """找出行内所有像表情的块。

    难点：一行里可能有**多个**表情紧挨着（实测 4 个手势间距只有 3~6px），
    按"连通列段"会粘成一块。所以这里改为：
      ① 用较小的列阈值找出候选列
      ② 对过宽的段，按「列计数低谷」递归切开
      ③ 每段再套尺寸/填充率约束
    """
    if rgb.ndim != 3:
        rgb = _gray_or_rgb(rgb)
    h, w = rgb.shape[:2]
    if h < MIN_H or w < MIN_W:
        return []
    fg = foreground(rgb)
    if int(fg.sum()) < 20:
        return []

    colcnt = fg.sum(axis=0).astype(np.float32)
    # 列阈值要低：单个 10~16px 高的表情，某一列最多只有 10 来个前景像素，
    # 而两个表情之间的空隙只有 2~4 个像素。阈值取 max(1, h*0.08)。
    thr = max(1, int(h * 0.08))
    hit = colcnt >= thr

    start = int(w * max(0.0, min(right_zone, 0.95)))
    out: list[tuple[int, int, int, int]] = []
    s = None
    for x in range(start, w + 1):
        on = x < w and hit[x]
        if on and s is None:
            s = x
        elif not on and s is not None:
            out.extend(_split_and_fit(fg, colcnt, s, x))
            s = None
    if s is not None:
        out.extend(_split_and_fit(fg, colcnt, s, w))
    return out


def _split_and_fit(fg: np.ndarray, colcnt: np.ndarray, x1: int, x2: int,
                   depth: int = 0) -> list[tuple[int, int, int, int]]:
    """把一段列切开并逐个拟合框。"""
    bw = x2 - x1
    if bw <= 0:
        return []

    # 尺寸合适 → 直接拟合
    if MIN_W <= bw <= MAX_W:
        b = _fit_box(fg, x1, x2)
        return [b] if b else []

    if bw < MIN_W:
        return []

    # 太宽：找低谷切开
    if depth >= 4:
        return []
    seg = colcnt[x1:x2]
    mid_lo = int(len(seg) * 0.22)
    mid_hi = int(len(seg) * 0.78)
    if mid_hi <= mid_lo:
        return []
    inner = seg[mid_lo:mid_hi]
    if inner.size == 0:
        return []
    cut = mid_lo + int(np.argmin(inner))
    cut_abs = x1 + cut
    if cut_abs <= x1 or cut_abs >= x2:
        return []

    res = []
    res.extend(_split_and_fit(fg, colcnt, x1, cut_abs, depth + 1))
    res.extend(_split_and_fit(fg, colcnt, cut_abs, x2, depth + 1))
    return res


def _fit_box(fg: np.ndarray, x1: int, x2: int):
    bw = x2 - x1
    if bw < MIN_W or bw > MAX_W:
        return None
    seg = fg[:, x1:x2]
    if seg.size == 0:
        return None
    rowcnt = seg.sum(axis=1)
    # 行阈值放低：表情边缘只有 1~2 个前景像素
    ys = np.flatnonzero(rowcnt >= 1)
    if ys.size == 0:
        return None
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    bh = y2 - y1
    if bh < MIN_H or bh > MAX_H:
        return None
    # 填充率门槛放低：细长的 V 手势在 9px 宽里填充率只有 0.3 左右
    fill = float(seg[y1:y2].mean())
    if fill < 0.18:
        return None
    return (x1, y1, x2, y2)


def features(patch: np.ndarray) -> dict | None:
    """把一个表情块量化成可比较的特征。"""
    if patch.size == 0:
        return None
    fg = foreground(patch)
    n = int(fg.sum())
    if n < 20:
        return None
    h, w = patch.shape[:2]

    a = patch.astype(np.int16)
    mx = np.max(a, axis=2)
    mn = np.min(a, axis=2)
    sat = np.where(mx > 1, (mx - mn), 0)

    fg_sat = float(np.median(sat[fg]))
    fg_val = float(np.median(mx[fg]))
    fg_rgb = patch[fg].mean(axis=0)
    rgb_list = [int(v) for v in fg_rgb.round(0)]
    family = color_family(rgb_list)

    # ★ 文字过滤：三通道接近的灰阶 → 不是表情
    #   真机实测文字 rgb≈[157,152,150]（spread=7），表情一定有明显色偏
    if family == "gray":
        return None

    # 色相（人工配置用）
    r, g, b = [float(v) for v in fg_rgb]
    hue = _hue_of(r, g, b)

    # 归一化形状指纹
    m = Image.fromarray((fg * 255).astype(np.uint8), mode="L")
    small = np.asarray(m.resize((NORM, NORM), Image.BILINEAR)).astype(np.float32) / 255.0
    shape = (small > 0.4).astype(np.float32)

    # 顶部峰数（V 手势 = 2 个峰，手掌 = 1）
    top = fg[:max(1, h // 3)]
    prof = top.sum(axis=0).astype(np.float32)
    peaks = 0
    inpk = False
    lim = max(1.0, prof.max() * 0.5) if prof.size else 1.0
    for v in prof:
        if v >= lim:
            if not inpk:
                peaks += 1
                inpk = True
        else:
            inpk = False

    # 四象限填充
    hh, ww = max(1, h // 2), max(1, w // 2)
    quads = []
    for yy in range(2):
        for xx in range(2):
            blk = fg[yy * hh:(yy + 1) * hh, xx * ww:(xx + 1) * ww]
            quads.append(round(float(blk.mean()), 3) if blk.size else 0.0)

    # OK 手势的中心空洞（e0 有，其它没有）
    cy, cx = h // 2, w // 2
    hole = 0
    if h >= 10 and w >= 10:
        c = fg[cy - 2:cy + 3, cx - 2:cx + 3]
        hole = int((~c).sum())

    return {
        "w": w, "h": h,
        "aspect": round(w / max(1, h), 2),
        "fill": round(float(fg.mean()), 3),
        "sat": round(fg_sat, 1),
        "val": round(fg_val, 1),
        "rgb": rgb_list,
        "family": family,
        "hue": round(hue, 1),
        "hue_group": hue_group(hue),
        "peaks": peaks,
        "quads": quads,
        "hole": hole,
        "shape": shape,
    }


def _hue_of(r: float, g: float, b: float) -> float:
    mx = max(r, g, b)
    mn = min(r, g, b)
    d = mx - mn
    if d < 1e-6:
        return 0.0
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = ((b - r) / d) + 2
    else:
        h = ((r - g) / d) + 4
    return h * 60.0


def color_family(rgb: list[int] | tuple) -> str:
    """按"主通道 + 冷暖"把颜色分成大类。

    真机实测（企业微信聊天区）三类元素：
        文字         rgb≈[157,152,150]  → 三通道接近、很灰      → "gray"
        棕色数字贴纸  rgb≈[175,150,123]  → R>G>B（暖）           → "warm"
        蓝色手势      rgb≈[187,215,244]  → B>G>R（冷）           → "cool"
        黄色手势      rgb≈[210,161,74]   → R>G>B 且 R-B 很大     → "warm"

    用"哪个通道最大"比用饱和度可靠得多：
        棕色贴纸 sat=56，浅蓝手势 sat=56 —— 饱和度完全一样，但冷/暖相反。
    """
    r, g, b = [float(v) for v in rgb]
    mx, mn = max(r, g, b), min(r, g, b)
    spread = mx - mn
    if spread < 18:
        return "gray"                     # 文字/灰阶
    if b > r + 12:
        return "cool"                     # 蓝系
    if r > b + 12:
        return "warm"                     # 黄/橙/棕
    if g > b + 12 and g > r:
        return "cool"                     # 偏绿也算冷
    return "gray"


def hue_group(hue: float) -> str:
    """粗色相分组（保留给人工配置用）。"""
    if hue < 25 or hue >= 340:
        return "red"
    if hue < 50:
        return "orange"
    if hue < 75:
        return "yellow"
    if hue < 170:
        return "green"
    if hue < 260:
        return "blue"
    return "purple"


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = float(np.minimum(a, b).sum())
    union = float(np.maximum(a, b).sum())
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------- 表情表
def table_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "AutoGrab" / "emoji_map.json"


def load_table() -> list[dict]:
    global _FEAT_CACHE
    if _FEAT_CACHE is not None:
        return _FEAT_CACHE
    items: list[dict] = []
    import sys
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets" / "gesture_templates.json"
    for p in (bundled, table_path(),
              Path(os.environ.get("APPDATA", ".")) / "AutoGrab" / "config.json"):
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if p.name == "config.json":
            data = data.get("emoji_map")
        if isinstance(data, dict):
            data = data.get("emoji") or data.get("items") or []
        if isinstance(data, list) and data:
            items = data
            break
    _FEAT_CACHE = items
    return items


def reload_table() -> None:
    global _FEAT_CACHE
    _FEAT_CACHE = None


def classify(patch: np.ndarray) -> tuple[str | None, dict, str]:
    """识别单个表情块 → (号码, 特征, 说明)。只认表里有的。"""
    feat = features(patch)
    if feat is None:
        return (None, {}, "特征不足（可能是文字，已忽略）")

    table = load_table()
    if not table:
        return (None, feat, "表情表为空")

    cands = []
    for item in table:
        # ---- 第一级：颜色大类必须一致（warm / cool）----
        #   真机实测：棕色数字贴纸(warm) 和 浅蓝手势(cool) 饱和度都是 56，
        #   用饱和度分不开，用"冷暖"一秒分开。
        calibrated = item.get("calibrated", False)
        want_family = item.get("family")
        if not calibrated and want_family and want_family != feat["family"]:
            continue
        # ---- 二级：饱和度接近（同族内的深浅差异）----
        want_sat = item.get("sat")
        if want_sat is not None:
            if abs(feat["sat"] - float(want_sat)) > SAT_TOL * 1.6:
                continue
        # ---- 三级：形状指纹 ----
        shape = item.get("shape")
        iou = 0.0
        if shape:
            arr = np.asarray(shape, dtype=np.float32)
            if arr.size == NORM * NORM:
                iou = _iou(feat["shape"], arr.reshape(NORM, NORM))
        # 峰数
        peaks = item.get("peaks")
        bonus = 0.0
        if peaks is not None:
            bonus += 0.15 if int(peaks) == int(feat["peaks"]) else -0.15
        # 宽高比
        aspect = item.get("aspect")
        if isinstance(aspect, (list, tuple)) and len(aspect) == 2:
            lo, hi = float(aspect[0]), float(aspect[1])
            bonus += 0.10 if (lo <= feat["aspect"] <= hi) else -0.20

        if calibrated:
            if want_sat is not None and abs(feat["sat"] - float(want_sat)) > 38:
                continue
            if isinstance(aspect, (list, tuple)) and not (lo <= feat["aspect"] <= hi):
                continue
            score = iou
        else:
            score = iou + bonus
        cands.append((score, iou, item))

    if not cands:
        return (None, feat, f"没有同色系的表项 (sat={feat['sat']:.0f} "
                            f"hue={feat['hue_group']})")

    cands.sort(key=lambda t: t[0], reverse=True)
    best_score, best_iou, best_item = cands[0]
    best_code = str(best_item.get("code") or best_item.get("value") or "")
    second = max((score for score, _, item in cands[1:]
                  if str(item.get("code") or item.get("value") or "") != best_code), default=-9)
    min_iou = 0.84 if best_item.get("calibrated") else SHAPE_MIN_IOU
    if best_iou < min_iou:
        return (None, feat, f"形状不够像(iou={best_iou:.2f})")
    if (best_score - second) < SHAPE_MARGIN:
        return (None, feat, f"歧义(最佳{best_score:.2f} 次佳{second:.2f})")

    code = str(best_item.get("code") or best_item.get("value") or "").strip()
    if not code:
        return (None, feat, "表项缺 code")
    return (code, feat, f"命中 {best_item.get('name') or code} "
                        f"(iou={best_iou:.2f} score={best_score:.2f})")


def classify_row(rgb: np.ndarray) -> tuple[str | None, dict, str]:
    """在一整行里找表情并识别。只处理最靠右的那一个（表情总在句末）。"""
    boxes = find_boxes(rgb, right_zone=0.35)
    if not boxes:
        return (None, {}, "行内没找到表情")
    x1, y1, x2, y2 = boxes[-1]
    return classify(np.ascontiguousarray(rgb[y1:y2, x1:x2]))


def build_table(samples: list[tuple[str, str, np.ndarray]]) -> list[dict]:
    """用样本建表。samples = [(code, name, patch), ...]。"""
    out = []
    for code, name, patch in samples:
        f = features(patch)
        if not f:
            continue
        out.append({
            "code": code,
            "name": name,
            "sat": f["sat"],
            "family": f["family"],
            "hue_group": f["hue_group"],
            "peaks": f["peaks"],
            "aspect": [round(f["aspect"] * 0.80, 2), round(f["aspect"] * 1.25, 2)],
            "shape": [round(float(v), 3) for v in f["shape"].ravel()],
        })
    return out