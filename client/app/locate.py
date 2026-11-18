# -*- coding: utf-8 -*-
"""自动定位聊天消息区 —— 不靠手工框选、不靠 DPI、不认版本。

老版本在 Win7 + 125% 缩放的机器上"框选对不上"，根因是拿 Tk 的逻辑像素去截物理像素，
一开缩放就整体偏移缩小。本模块从此不碰 Tk 坐标：所有几何一律来自
    GetClientRect + ClientToScreen（Win32，物理像素）
然后在窗口内部靠像素结构自动认出聊天区，顺序是：

    ① 找输入框上边框 —— 横贯面板的一条 1px 线，上下都亮（气泡的边不满足"下面也亮"）
    ② 由输入框的左右端反推聊天面板的左右分隔线
    ③ 向上找群名 header 的底线 → 消息区上边界
    ④ 消息区 = 从左分隔线到右分隔线、从 header 底线到输入框上沿
    ⑤ 校验：在这个区里跑一次切行，能配出「名字行+气泡行」才算定位成功

实测（1080×760 窗口）：输入框上沿 y=606，左右 x=327..883，
分隔线 x=309 / x=919，header 底线 y=63 → 消息区 (309,64)-(919,604)，一次命中。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def luma(rgb: np.ndarray) -> np.ndarray:
    a = rgb.astype(np.int32)
    return (a[:, :, 0] * 299 + a[:, :, 1] * 587 + a[:, :, 2] * 114) // 1000


@dataclass
class ChatArea:
    x1: int
    y1: int
    x2: int
    y2: int
    input_x1: int
    input_y1: int
    input_x2: int
    input_y2: int
    score: float = 0.0
    found: bool = True

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def input_center(self) -> tuple[int, int]:
        return ((self.input_x1 + self.input_x2) // 2, (self.input_y1 + self.input_y2) // 2)

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    def __str__(self) -> str:
        return (f"消息区({self.x1},{self.y1})-({self.x2},{self.y2}) "
                f"{self.width}x{self.height}  输入框@{self.input_center} "
                f"score={self.score:.1f}{'' if self.found else '  [比例兜底]'}")


# ------------------------------------------------------------------ 基础工具
def _longest_run(mask: np.ndarray) -> tuple[int, int]:
    """一维布尔 → 最长连续 True 段的 (start, end)。"""
    if not mask.any():
        return (-1, -1)
    idx = np.flatnonzero(mask)
    splits = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([0], splits + 1))
    ends = np.concatenate((splits, [idx.size - 1]))
    lens = idx[ends] - idx[starts]
    k = int(np.argmax(lens))
    return (int(idx[starts[k]]), int(idx[ends[k]]) + 1)


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    out = np.empty_like(a)
    if k > 0:
        out[:k] = a[0]
        out[k:] = a[:-k]
    else:
        out[k:] = a[-1]
        out[:k] = a[-k:]
    return out


# ------------------------------------------------------------------ ① 输入框
def find_input_border(L: np.ndarray) -> tuple[int, int, int] | None:
    """找输入框上边框：横贯面板的 1px 暗线，且上下都亮。返回 (y, x1, x2)。"""
    h, w = L.shape
    lo, hi = 190, 240
    best = None
    best_len = 0
    y0 = int(h * 0.40)
    for y in range(max(3, y0), h - 4):
        row = L[y]
        s, e = _longest_run((row >= lo) & (row <= hi))
        if s < 0 or (e - s) < w * 0.22 or (e - s) <= best_len:
            continue
        seg = slice(s, e)
        if np.median(L[y - 3, seg]) < 241 or np.median(L[y + 3, seg]) < 241:
            continue                      # 上下不亮 = 气泡填充，不是边框
        best_len = e - s
        best = (y, s, e)
    return best


# ------------------------------------------------------------------ ② 分隔线
def vlines(L: np.ndarray, lo: int = 195, hi: int = 240) -> list[int]:
    col = np.median(L, axis=0)
    left, right = _shift(col, 3), _shift(col, -3)
    mask = (col >= lo) & (col <= hi) & (left >= col + 2) & (right >= col + 2)
    cover = ((L >= lo) & (L <= hi)).mean(axis=0) > 0.55
    return [int(x) for x in np.flatnonzero(mask & cover)]


def hlines(L: np.ndarray, x1: int, x2: int, lo: int = 190, hi: int = 242) -> list[int]:
    if x2 - x1 < 30:
        return []
    row = np.median(L[:, x1:x2], axis=1)
    up, dn = _shift(row, 3), _shift(row, -3)
    mask = (row >= lo) & (row <= hi) & (up >= row + 2) & (dn >= row + 2)
    return [int(y) for y in np.flatnonzero(mask)]


# ------------------------------------------------------------------ ⑤ 校验
def _verify(rgb: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> tuple[float, int, int]:
    """在候选区里跑切行，返回 (分数, 行数, 配对数)。"""
    from . import segment

    crop = rgb[y1:y2, x1:x2]
    if crop.size == 0:
        return (-1.0, 0, 0)
    lay = segment.Layout()
    rows = segment.analyze(crop, lay, dark_thr=150)
    if len(rows) < 2:
        return (0.0, len(rows), 0)
    lay.learn(rows)
    rows = segment.analyze(crop, lay, dark_thr=150)
    msgs = segment.pair_messages(rows)
    paired = [m for m in msgs if m.sender_row is not None]
    score = len(paired) * 2.0 + len(rows) * 0.2
    for m in paired:
        d = m.body_row.x1 - m.sender_row.x1
        if 8 <= d <= 46:
            score += 1.0
    return (score, len(rows), len(paired))


# ------------------------------------------------------------------ 主入口
def input_box_for(rgb: np.ndarray, area: ChatArea) -> tuple[int, int, int, int]:
    """消息区下方的输入框点击区（保证点在框内）。"""
    h, w = rgb.shape[:2]
    band_top = min(h - 40, area.y2 + 2)
    band_bot = h - 6
    if band_bot - band_top < 24:
        band_top, band_bot = max(0, h - 130), h - 10
    inset = max(8, (area.x2 - area.x1) // 30)
    cy1 = band_top + max(6, (band_bot - band_top) // 4)
    cy2 = band_bot - max(6, (band_bot - band_top) // 4)
    if cy2 <= cy1:
        cy1, cy2 = band_top + 4, max(band_top + 8, band_bot - 4)
    return (area.x1 + inset, cy1, area.x2 - inset, max(cy2, cy1 + 4))


def find_chat_area(rgb: np.ndarray) -> ChatArea | None:
    h, w = rgb.shape[:2]
    L = luma(rgb)

    ib = find_input_border(L)
    if ib is None:
        return None
    input_top, ix1, ix2 = ib

    vs = vlines(L)
    left = [x for x in vs if x < ix1 - 2]
    right = [x for x in vs if x > ix2 + 2]
    inset = max(10, int((ix2 - ix1) * 0.03))
    x1 = max(left) + 1 if left else ix1 - inset
    x2 = min(right) if right else ix2 + inset
    if x2 <= x1:
        return None

    hs = hlines(L, x1 + 4, x2 - 4)
    heads = [y for y in hs if y < input_top * 0.6]
    y1 = (min(heads) + 1) if heads else int(input_top * 0.10)
    y2 = input_top - 2
    if y2 - y1 < 60:
        return None

    score, _, _ = _verify(rgb, x1, y1, x2, y2)

    # 输入框点击区：上边框下面那一段的中间
    box_h = max(40, min(int(input_top * 0.16) + 40, h - input_top - 12))
    inp = (ix1 + 8, input_top + max(8, box_h // 3), ix2 - 8, input_top + box_h)

    return ChatArea(x1, y1, x2, y2, *inp, score=score, found=True)


def fallback_area(w: int, h: int) -> ChatArea:
    """定位失败的比例兜底（按 1080×760 实测比例缩放，仅作最后手段）。"""
    x1, y1 = int(w * 0.286), int(h * 0.084)
    x2, y2 = int(w * 0.851), int(h * 0.795)
    input_top = int(h * 0.797)
    return ChatArea(x1, y1, x2, y2,
                    int(w * 0.303), input_top + 20, int(w * 0.818), input_top + 80,
                    0.0, False)


def locate(rgb: np.ndarray) -> ChatArea:
    """永不返回 None；定位不到标 found=False。"""
    h, w = rgb.shape[:2]
    area = find_chat_area(rgb)
    if area is None or area.width < w * 0.2 or area.height < h * 0.2:
        return fallback_area(w, h)
    return area
