# -*- coding: utf-8 -*-
"""行切分：纯 numpy 像素投影，不依赖 OCR det，一帧 <2ms。

企业微信聊天区的固定版式（实测）：
    名字行   x1 ≈ 气泡左边距 - 20px
    气泡内容 x1 ≈ 气泡左边距
    时间戳   居中、灰色、高 < 26
所以「x1 落在左锚点」= 名字行，「x1 落在左锚点+20」= 内容行，
再把紧邻上方的名字行配给内容行，就能精确得到 (发送人, 内容)。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

NAME = "name"
CONTENT = "content"
TS = "ts"
OWN = "own"


@dataclass
class Row:
    kind: str
    y1: int
    y2: int
    x1: int
    x2: int

    @property
    def h(self) -> int:
        return self.y2 - self.y1

    def __repr__(self) -> str:
        return f"<{self.kind} y={self.y1}-{self.y2} x={self.x1}-{self.x2}>"


@dataclass
class Msg:
    sender_row: Row | None
    body_row: Row

    @property
    def is_own(self) -> bool:
        return self.sender_row is None


def to_gray(rgb: np.ndarray) -> np.ndarray:
    a = rgb.astype(np.int32)
    return (a[:, :, 0] * 299 + a[:, :, 1] * 587 + a[:, :, 2] * 114) // 1000


def normalize(rgb: np.ndarray, invert_below: int = 110) -> np.ndarray:
    """把画面统一成「浅底深字」。

    深色模式 / 高对比度主题下背景是深色、字是浅色，投影切行和 OCR 都会失效，
    所以整帧先反色（实测：深色模式不反色时一行都切不出来）。
    """
    if rgb.ndim == 3 and rgb.shape[2] >= 3:
        med = float(np.median(rgb))
    else:
        med = float(np.median(rgb))
    if med < invert_below:
        return (255 - rgb).astype(np.uint8)
    return rgb


def auto_thr(rgb: np.ndarray, base: int = 150) -> int:
    """跟着背景亮度走的阈值。

    绝对阈值（150）在两种真实场景下会失效：
      · 屏幕调亮/对比度调低 → 浅灰的名字行（#999）变成 #C7，全部落在 150 以上 → 名字行整行消失
      · 屏幕调暗 → 正文也可能压到 150 附近
    所以改成「相对背景中位数」：比背景暗 30 就算墨迹，同时不超过 base+75，
    避免把气泡边框、时间分割线的抗锯齿也算成内容。
    """
    med = float(np.median(rgb))
    return int(min(base + 75, max(60, med - 30)))


def _runs(flags: np.ndarray, min_len: int = 1, gap: int = 2) -> list[tuple[int, int]]:
    """一维布尔 → 连续段列表，允许 gap 像素的断裂。"""
    idx = np.flatnonzero(flags)
    if idx.size == 0:
        return []
    out = []
    start = prev = idx[0]
    for y in idx[1:]:
        if y - prev <= gap:
            prev = y
            continue
        if prev - start + 1 >= min_len:
            out.append((int(start), int(prev + 1)))
        start = prev = y
    if prev - start + 1 >= min_len:
        out.append((int(start), int(prev + 1)))
    return out


class Layout:
    """记住名字行 / 内容行的左锚点，可自动学习。"""

    def __init__(self, name_x: int | None = None, content_x: int | None = None):
        self.name_x = name_x
        self.content_x = content_x

    def learn(self, rows: list[Row]) -> None:
        """从一堆行里挑出两个左边界当作锚点（名字行 / 气泡内容行）。"""
        if not rows:
            return
        counts: dict[int, int] = {}
        for r in rows:
            counts[r.x1] = counts.get(r.x1, 0) + 1
        distinct = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        top = [x for x, _ in distinct[:2]]
        if len(top) >= 2:
            self.name_x, self.content_x = min(top), max(top)
        else:
            # 只看到一列：当作名字列，按企业微信固定 20px 内边距反推气泡列
            self.name_x = top[0]
            self.content_x = top[0] + 20

    def classify(self, row: Row, width: int) -> str:
        if row.h >= 26 and abs((row.x1 + row.x2) / 2 - width / 2) < width * 0.12:
            return TS
        if self.content_x is None:
            return CONTENT
        if row.x2 > width - 30 and row.x1 > width * 0.45:
            return OWN                       # 自己发的：右对齐、无名字行
        # 名字行比内容行更靠左（企业微信固定 20px 气泡内边距）
        return NAME if row.x1 <= (self.name_x + self.content_x) / 2 else CONTENT


def analyze(rgb: np.ndarray, layout: Layout, dark_thr: int = 150,
            min_h: int = 6, gap: int = 2) -> list[Row]:
    """一帧 → 行列表（自上而下）。

    用 uint8 位移算近似亮度（0.25R+0.5G+0.25B，最大 253 不溢出），
    比 astype(uint16).sum(axis=2) 快 3~4 倍。
    """
    h, w = rgb.shape[:2]
    luma = (rgb[:, :, 0] >> 2) + (rgb[:, :, 1] >> 1) + (rgb[:, :, 2] >> 2)
    thr = min(255, int(dark_thr * 0.87))          # 近似亮度与灰度阈值对齐
    dark = luma < thr
    rows: list[Row] = []
    for y1, y2 in _runs(dark.any(axis=1), min_len=min_h, gap=gap):
        band = dark[y1:y2]
        cols = np.flatnonzero(band.any(axis=0))
        if cols.size == 0:
            continue
        r = Row(CONTENT, y1, y2, int(cols[0]), int(cols[-1]) + 1)
        r.kind = layout.classify(r, w)
        rows.append(r)
    return rows


def pair_messages(rows: list[Row], max_gap: int = 50) -> list[Msg]:
    """把紧邻内容行上方的名字行配上去（实测名字行间距 28~40px）。

    max_gap 必须跟着画面尺度走：整屏缩放 150%/200% 时行高和间距一起翻倍，
    固定 50px 会让名字行配不上气泡 → 所有人都变成"无名字"，直接把派单人过滤掉。
    """
    if rows:
        hs = sorted(r.h for r in rows)
        med_h = hs[len(hs) // 2]
        max_gap = max(max_gap, int(med_h * 2.4))
    msgs: list[Msg] = []
    for i, r in enumerate(rows):
        if r.kind != CONTENT:
            continue
        sender = None
        for j in range(i - 1, -1, -1):
            prev = rows[j]
            if prev.kind == TS:
                break
            if prev.kind == NAME:
                if 0 <= r.y1 - prev.y2 <= max_gap:
                    sender = prev
                break
            if prev.kind == CONTENT:
                # 兜底：分类把名字行也判成了内容行 —— 左边界更靠左 12px 以上就是名字行
                if 0 <= r.y1 - prev.y2 <= max_gap and prev.x1 <= r.x1 - 12:
                    sender = prev
                break
        msgs.append(Msg(sender, r))

    used = {id(m.sender_row) for m in msgs if m.sender_row is not None}
    return [m for m in msgs if id(m.body_row) not in used]


def crop(rgb: np.ndarray, row: Row, pad_x: int = 2, pad_y: int = 2) -> np.ndarray:
    h, w = rgb.shape[:2]
    y1 = max(0, row.y1 - pad_y)
    y2 = min(h, row.y2 + pad_y)
    x1 = max(0, row.x1 - pad_x)
    x2 = min(w, row.x2 + pad_x)
    return rgb[y1:y2, x1:x2]
