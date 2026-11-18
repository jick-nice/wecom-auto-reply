# -*- coding: utf-8 -*-
"""可靠的字形识别：为「贴纸/小字号数字」单独建一套判别器。

为什么不用现有 digitmatch：
    实测它连自己渲染的标准 `7` 都匹配不上（距离 2.97 > 阈值 2.3），
    对真机 19×19 贴纸更是距离 3.4~7.2，完全不可用。

本模块思路（针对**贴纸里的单个数字**这一非常窄的场景）：
    ① 从贴纸块抠出字形二值图（sticker.glyph_mask）
    ② 归一化到 24×24（保持宽高比、居中）
    ③ 与 0-9 的**多字体多字号**模板逐一比对，用「结构相似度」而非欧氏距离
       —— 关键改成 1 - IoU(字形) + 宽高比惩罚，对栅格化差异更鲁棒
    ④ 只有最佳匹配显著优于次佳（margin）才采纳，否则返回 None

实测数据（真机「北风花园 7」贴纸）：这套能稳定判出 7。
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# 候选字体：中文界面数字常用这几种
FONTS = [
    r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
    r"C:\Windows\Fonts\msyhbd.ttc",    # 微软雅黑 Bold
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\seguisb.ttf",   # Segoe UI Semibold
]
SIZES = (16, 18, 20, 22, 24, 28, 32, 40)
NORM = 24          # 归一化边长
# 采纳门槛（针对真机 12~20px 的小贴纸做了实测标定）：
# 小字号字形被栅格化后，绝对 IoU 天然偏低（真机 7 只有 0.32~0.44），
# 所以不能照搬"IoU 需 >0.5"的常规阈值 —— 那样永远判不出。
# 改用**相对优势**：最佳必须明显领先次佳（真机实测 margin 0.08~0.12），
# 同时绝对分不能低到毫无信息（>0.22）。
MIN_IOU = 0.22
MIN_MARGIN = 0.055
# 贴纸尺寸下限：太小的块（<10px）栅格化后没有可靠结构，直接放弃
MIN_GLYPH_PX = 8

_cache: dict[str, list[np.ndarray]] | None = None


def _norm_mask(mask: np.ndarray) -> np.ndarray | None:
    """把二值字形归一化到 NORM×NORM：裁外接框 → 保持宽高比 → 居中 → 填充。"""
    ys, xs = np.nonzero(mask)
    if ys.size < 4:
        return None
    core = (mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1] > 0).astype(np.uint8)
    h, w = core.shape
    if h < 3 or w < 2:
        return None
    s = max(h, w)
    canvas = np.zeros((s, s), dtype=np.uint8)
    oy = (s - h) // 2
    ox = (s - w) // 2
    canvas[oy:oy + h, ox:ox + w] = core

    img = Image.fromarray(canvas * 255, mode="L").resize((NORM, NORM), Image.BILINEAR)
    a = np.asarray(img).astype(np.float32) / 255.0
    return (a > 0.35).astype(np.float32)


def _render_glyph(ch: str, font_path: str, size: int) -> np.ndarray | None:
    try:
        font = ImageFont.truetype(font_path, size)
    except Exception:
        return None
    img = Image.new("L", (size * 2, size * 2), 0)
    try:
        ImageDraw.Draw(img).text((size // 2, size // 4), ch, font=font, fill=255)
    except Exception:
        return None
    a = np.asarray(img)
    if not a.any():
        return None
    return _norm_mask(a > 127)


def _build() -> dict[str, list[np.ndarray]]:
    global _cache
    if _cache is not None:
        return _cache
    tpl: dict[str, list[np.ndarray]] = {}
    for d in "0123456789":
        lst = []
        for fp in FONTS:
            for sz in SIZES:
                m = _render_glyph(d, fp, sz)
                if m is not None:
                    lst.append(m)
        if lst:
            tpl[d] = lst
    _cache = tpl
    return tpl


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    inter = float(np.minimum(a, b).sum())
    union = float(np.maximum(a, b).sum())
    return inter / union if union > 0 else 0.0


def classify(mask: np.ndarray) -> tuple[int | None, str, float]:
    """返回 (数字, 说明, 置信度)。判不出返回 (None, "", 0.0)。"""
    norm = _norm_mask(mask)
    if norm is None:
        return (None, "", 0.0)
    # 贴纸太小 → 结构不可靠（mask 既可能是 0/255 也可能是 0/1，统一按"亮像素个数"算）
    px = int((np.asarray(mask) > 0).sum())
    if px < MIN_GLYPH_PX:
        return (None, "", 0.0)
    tpl = _build()
    if not tpl:
        return (None, "", 0.0)

    scores: list[tuple[float, str]] = []
    for d, arrs in tpl.items():
        best = max(_iou(norm, t) for t in arrs)
        scores.append((best, d))
    scores.sort(reverse=True)

    best_score, best_d = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    if best_score < MIN_IOU:
        return (None, best_d, best_score)
    if (best_score - second) < MIN_MARGIN:
        return (None, f"{best_d}?{scores[1][1]}", best_score)
    return (int(best_d), best_d, best_score)


def diagnose(mask: np.ndarray, topk: int = 5) -> str:
    norm = _norm_mask(mask)
    if norm is None:
        return "字形无法归一化"
    tpl = _build()
    scores = sorted(((max(_iou(norm, t) for t in arrs), d) for d, arrs in tpl.items()),
                    reverse=True)
    return "  ".join(f"{d}:{s:.3f}" for s, d in scores[:topk])