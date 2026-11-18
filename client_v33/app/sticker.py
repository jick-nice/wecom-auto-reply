# -*- coding: utf-8 -*-
"""贴纸徽章识别补强：把「棕色/彩色圆角方块 + 白色数字」这类表情贴纸读出来。

为什么要单独做（v3.3 的 ocr.find_badges 为什么漏）：
    find_badges 的门槛是 `饱和度 > 90`。而真机企业微信的**数字贴纸**是
    棕色圆角块（RGB≈180,162,144，饱和度只有 ~43），远达不到 90，
    于是被判成"不是色块"，整行 OCR 又把这个色块当成图形跳过 →
    屏幕上明明是「北风花园 7」，OCR 只读出「北风花园」→ 解析不出号码 → 不回。

本模块的做法（不依赖 OCR 的 det，纯像素 + 反色 rec）：
    ① 用「亮度明显低于背景」找出所有实心色块的外接框（不靠饱和度）
       —— 圆角块即使饱和度低，它相对聊天背景也是明显的一块暗区
    ② 对每个候选块，把块内**亮于中位数**的像素当字形（白色数字），
       其余当背景，二值化后反色成"白底黑字"，喂给 rec-only OCR
    ③ 只在块尺寸像单个字符（宽高 8~40px、接近方块/竖长）时才这么做
    ④ 读出来的文本交给 numparse 归一化

这样「白字贴纸」和「深色底浅字贴纸」都能覆盖，且不会影响普通文字行的识别。
"""
from __future__ import annotations

import numpy as np

# 单个贴纸的尺寸范围（真机实测 19×29 ~ 34×34 之间）
MIN_SIDE = 10
MAX_SIDE = 46
# 贴纸相对背景至少要暗这么多才算"一块色块"（不是文字笔画）
MIN_DARK_DELTA = 10
# 块内"字形"判定：亮于块内中位数这么多
MIN_GLYPH_DELTA = 18
# 块内暗像素占比下限（防止把整行文字当成块）
MIN_FILL = 0.45

# ★ 区分「贴纸」和「文字笔画」的关键指标：
#   贴纸是一个**实心圆角/方形块** —— 它内部只有一层浅色字，剩余全是同一个底色，
#   所以块内的颜色种类极少（底色 + 字形色），且底色占比很高。
#   汉字笔画是**稀疏的黑色笔划**压在背景上，块内大部分是背景色，
#   暗像素占比低且分布零散。
# 实测：真机棕色贴纸 内部底色占比 ~0.62、暗区填充 ~0.80；
#       汉字笔画块 内部底色占比 ~0.35、暗区填充 ~0.50。
MIN_BLOCK_FILL = 0.62      # 块内"非背景"的占比下限（贴纸更实心）
MIN_BOTTOM_RATIO = 0.50    # 块内最大单一颜色的占比下限（贴纸有一整片底色）


def _gray(rgb: np.ndarray) -> np.ndarray:
    a = rgb.astype(np.int32)
    return (a[:, :, 0] * 299 + a[:, :, 1] * 587 + a[:, :, 2] * 114) // 1000


def find_solid_boxes(rgb: np.ndarray) -> list[tuple[int, int, int, int]]:
    """找出行内所有「实心色块」（贴纸/徽章）的外接框。

    与 ocr.find_badges 的区别：**不用饱和度**，改用"相对背景的暗度 + 填充率"。
    棕色/浅色贴纸饱和度很低（实测 43），用饱和度必然漏。
    """
    if rgb.ndim != 3 or rgb.shape[0] < MIN_SIDE or rgb.shape[1] < MIN_SIDE:
        return []
    g = _gray(rgb)
    h, w = g.shape
    bg = float(np.median(g))

    dark = g < (bg - MIN_DARK_DELTA)
    if not dark.any():
        return []

    # 按列分组：贴纸是一段连续的"暗列"
    col_hit = dark.mean(axis=0) >= 0.25
    runs = []
    start = None
    for x in range(w):
        if col_hit[x]:
            if start is None:
                start = x
        else:
            if start is not None:
                runs.append((start, x))
                start = None
    if start is not None:
        runs.append((start, w))

    boxes: list[tuple[int, int, int, int]] = []
    for x1, x2 in runs:
        if (x2 - x1) < MIN_SIDE or (x2 - x1) > MAX_SIDE:
            continue
        sub = dark[:, x1:x2]
        row_hit = sub.mean(axis=1) >= 0.4
        ys = np.flatnonzero(row_hit)
        if ys.size == 0:
            continue
        y1, y2 = int(ys.min()), int(ys.max()) + 1
        if (y2 - y1) < MIN_SIDE or (y2 - y1) > MAX_SIDE:
            continue
        fill = float(sub[y1:y2].mean())
        if fill < MIN_FILL:
            continue
        boxes.append((x1, y1, x2, y2))
    return boxes


def is_solid_block(patch: np.ndarray) -> bool:
    """判断一块裁剪图是不是「实心贴纸」而不是「文字笔画」。

    判据（都来自真机实测标定）：
      ① 块内最大单一灰度层的占比 >= MIN_BOTTOM_RATIO
         —— 贴纸有一整片均匀底色；笔画块没有
      ② 块内"非背景"像素占比 >= MIN_BLOCK_FILL
         —— 贴纸几乎铺满，笔画只占一部分
      ③ 块内必须有明显的两层（底色 + 浅色字形），且浅色层占比适中
         —— 这正好是 sticker 的形态；纯笔画只有一层
    """
    if patch.size == 0:
        return False
    g = _gray(patch)
    h, w = g.shape
    if h < MIN_SIDE or w < MIN_SIDE:
        return False
    total = float(h * w)
    if total <= 0:
        return False

    # ① 最大单一灰度层占比（量化成 8 级，避免抗锯齿把一层拆散）
    q = (g // 32).astype(np.int32)
    counts = np.bincount(q.ravel(), minlength=8)
    bottom_ratio = float(counts.max()) / total
    if bottom_ratio < MIN_BOTTOM_RATIO:
        return False

    # ② 非背景占比（用块自身中位数当背景）
    med = float(np.median(g))
    non_bg = float((np.abs(g - med) > 8).mean())
    if non_bg < (1.0 - MIN_BOTTOM_RATIO) * 0.5:
        return False

    # ③ 两层结构：浅层（字）占比 6%~60%，且深层（底）也在
    hi = float((g > med + MIN_GLYPH_DELTA).mean())
    lo = float((g < med - MIN_GLYPH_DELTA).mean())
    if not (0.05 <= hi <= 0.6):
        return False
    if lo < 0.10:
        return False
    return True


def glyph_mask(patch: np.ndarray) -> np.ndarray | None:
    """从贴纸块里抠出"字"（白字/浅字）→ 返回 uint8 二值图（255=字，0=底）。

    关键：贴纸通常是「实心底 + 浅色字」，所以字是块内**偏亮**的部分。
    """
    if patch.size == 0:
        return None
    g = _gray(patch)
    med = float(np.median(g))
    hi = g > (med + MIN_GLYPH_DELTA)
    lo = g < (med - MIN_GLYPH_DELTA)
    # 必须有明显的"两层"（有亮有暗），否则不是贴纸字
    if hi.mean() < 0.06 or hi.mean() > 0.75:
        return None
    if lo.mean() < 0.12:
        return None
    out = np.zeros(g.shape, dtype=np.uint8)
    out[hi] = 255
    return out


def read_box_text(ocr, rgb: np.ndarray, box) -> str:
    """把一个贴纸块读成文本。

    识别顺序（**模板优先，OCR 兜底**）：
      ① stickerdigit 图形模板匹配 —— 对 12~20px 的小字形最可靠，
         真机实测「北风花园 7」稳定判出 7（IoU 0.44，次佳 0.34）
      ② OCR rec-only —— 模板判不出时才用。实测它会把 7 读成 `T`、把 5 读成 `S`，
         所以只能当兜底，且结果要再过一遍 OCR_CONFUSION 纠错。

    踩过的坑（真机）：
      · 不裁外接框时，圆角块的四个角会留下白色残留（圆角处是背景色），
        放大后 rec 把它当成字形的一部分，把 `7` 读成 `T`。
      · 放大倍数不够（19×19 原样喂进去）也会读成 T。
    """
    from PIL import Image
    from . import stickerdigit

    x1, y1, x2, y2 = box
    patch = rgb[y1:y2, x1:x2]
    mask = glyph_mask(patch)
    if mask is None:
        return ""

    # ① 模板优先
    try:
        num, desc, score = stickerdigit.classify(mask)
        if num is not None:
            return str(num)
    except Exception:
        pass

    # ② OCR 兜底
    ys, xs = np.nonzero(mask)
    if ys.size < 4:
        return ""
    gy1, gy2 = int(ys.min()), int(ys.max()) + 1
    gx1, gx2 = int(xs.min()), int(xs.max()) + 1
    core = mask[gy1:gy2, gx1:gx2]
    if core.size == 0 or core.shape[0] < 4 or core.shape[1] < 2:
        return ""

    # ② 去掉孤立的 1px 噪点（4 邻域里没有同伴的像素）
    c = (core > 0).astype(np.uint8)
    nb = np.zeros_like(c, dtype=np.int16)
    nb[1:, :] += c[:-1, :]
    nb[:-1, :] += c[1:, :]
    nb[:, 1:] += c[:, :-1]
    nb[:, :-1] += c[:, 1:]
    core = np.where((c == 1) & (nb >= 1), 255, 0).astype(np.uint8)
    if int((core > 0).sum()) < 6:
        return ""

    # ③ 白底黑字 + 补边 + 放大到足够高
    img = Image.fromarray(255 - core, mode="L")
    pad = 6
    canvas = Image.new("L", (img.width + pad * 2, img.height + pad * 2), 255)
    canvas.paste(img, (pad, pad))
    f = max(3, int(64 / max(1, canvas.height)))
    canvas = canvas.resize((canvas.width * f, canvas.height * f), Image.LANCZOS)
    arr = np.stack([np.array(canvas)] * 3, axis=2)

    try:
        text = ocr.line(arr)
    except Exception:
        return ""
    return (text or "").strip()


def scan(ocr, rgb: np.ndarray) -> list[tuple[tuple[int, int, int, int], str]]:
    """扫描整行，返回 [(box, text), ...]。text 可能为空，调用方自行过滤。

    只返回**通过 is_solid_block 判定**的贴纸块，汉字笔画块会被过滤掉。
    """
    out = []
    for box in find_solid_boxes(rgb):
        x1, y1, x2, y2 = box
        patch = rgb[y1:y2, x1:x2]
        if not is_solid_block(patch):
            continue
        t = read_box_text(ocr, rgb, box)
        out.append((box, t))
    return out