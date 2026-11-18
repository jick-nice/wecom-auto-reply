# -*- coding: utf-8 -*-
"""OCR：只跑 rec 分支（det 是 250~1000ms 的元凶，必须绕开）。

实测：单行 rec-only ≈ 4~10ms，det+rec ≈ 250~1000ms。
emoji 数字（3️⃣ 这种 keycap）OCR 读不出来，用「高饱和色块 → 反色 → rec」抠出来读。
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageOps


def _to_pil(rgb: np.ndarray) -> Image.Image:
    # 截屏给的是零拷贝视图（含 4 通道跨距），PIL 要求连续内存 → 只在小裁剪上拷一次
    return Image.fromarray(np.ascontiguousarray(rgb), mode="RGB")


def upscale(img: Image.Image, min_h: int = 40) -> Image.Image:
    if img.height < min_h:
        f = min_h / max(1, img.height)
        img = img.resize((max(1, int(img.width * f)), min_h), Image.LANCZOS)
    return img


class Ocr:
    """rec-only OCR + 结果缓存。"""

    def __init__(self, cache_size: int = 512):
        from rapidocr_onnxruntime import RapidOCR
        self.engine = RapidOCR()
        self._cache: OrderedDict[bytes, str] = OrderedDict()
        self._row_cache: OrderedDict[bytes, str] = OrderedDict()
        self._cache_size = cache_size

    # ------------------------------------------------------------ 单行文字
    def line(self, rgb: np.ndarray, min_h: int = 40) -> str:
        key = hashlib.blake2b(rgb.tobytes(), digest_size=16).digest()
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        img = upscale(_to_pil(rgb), min_h)
        res, _ = self.engine(np.array(img), use_det=False, use_cls=False, use_rec=True)
        text = "".join(t for t, _ in (res or [])).strip()
        self._cache[key] = text
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return text

    # ------------------------------------------------------- emoji 数字徽章
    @staticmethod
    def find_badges(rgb: np.ndarray, min_px: int = 90) -> list[tuple[int, int, int, int]]:
        """找 keycap emoji 那种**实心彩色方块**。

        注意不能只判单像素饱和度：ClearType 会在文字边缘留下彩色描边
        （实测 235,231,163 这种），单一阈值会把整行文字都判成徽章。
        这里先做 4×4 池化，要求块内 50% 以上是强饱和像素，才算实心色块。
        """
        a = rgb.astype(np.int16)
        r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
        sat = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
        m = sat > 90
        if int(m.sum()) < min_px:
            return []
        h, w = m.shape
        bh, bw = (h // 4) * 4, (w // 4) * 4
        if bh < 4 or bw < 4:
            return []
        pooled = m[:bh, :bw].reshape(bh // 4, 4, bw // 4, 4).mean(axis=(1, 3))
        solid = pooled >= 0.5
        if int(solid.sum()) < 2:
            return []
        ys, xs = np.nonzero(solid)
        y1, y2 = int(ys.min()) * 4, min(h, int(ys.max() + 1) * 4)
        x1, x2 = int(xs.min()) * 4, min(w, int(xs.max() + 1) * 4)
        if not (8 <= y2 - y1 <= 56 and 8 <= x2 - x1 <= 56):
            return []
        if (x2 - x1) / max(1, y2 - y1) < 0.5:
            return []
        if int(m[y1:y2, x1:x2].sum()) < min_px:
            return []
        return [(x1, y1, x2, y2)]

    def read_badge(self, rgb: np.ndarray, box) -> str:
        x1, y1, x2, y2 = box
        patch = rgb[y1:y2, x1:x2]
        img = _to_pil(patch)
        f = 4
        img = img.resize((img.width * f, img.height * f), Image.LANCZOS)
        best = ""
        for variant in (img, ImageOps.invert(img.convert("RGB"))):
            res, _ = self.engine(np.array(variant), use_det=False, use_cls=False, use_rec=True)
            txt = "".join(t for t, _ in (res or [])).strip()
            if txt and not best:
                best = txt
        return best

    # ---------------------------------------------------- 整行（文字 + 徽章）
    def read_row(self, rgb: np.ndarray) -> str:
        """先抠掉彩色徽章再读文字，徽章单独反色读，避免 emoji 干扰文字识别。

        整行做一次结果缓存：同一帧里没变化的消息不该被重复算（徽章那条要跑两次 rec）。
        """
        key = hashlib.blake2b(rgb.tobytes(), digest_size=16).digest()
        hit = self._row_cache.get(key)
        if hit is not None:
            self._row_cache.move_to_end(key)
            return hit

        boxes = self.find_badges(rgb)
        blanked = rgb
        if boxes:
            blanked = rgb.copy()
            for x1, y1, x2, y2 in boxes:
                blanked[y1:y2, x1:x2] = 255
        text = self.line(blanked)
        for box in boxes:
            b = self.read_badge(rgb, box)
            if b and b not in text:
                text = f"{text} {b}".strip()

        self._row_cache[key] = text
        if len(self._row_cache) > self._cache_size:
            self._row_cache.popitem(last=False)
        return text

    def read_name(self, rgb: np.ndarray) -> str:
        """名字行：字形小、灰度低，切分容易只切到笔画中段 —— 调用方务必给足上下边距。"""
        return self.line(rgb)

    def warmup(self) -> None:
        """把 onnxruntime 的首次调用开销（~240ms）挪到开始抢单之前。"""
        from PIL import ImageDraw
        img = Image.new("RGB", (420, 52), "white")
        d = ImageDraw.Draw(img)
        d.text((6, 10), "测试项目 3", fill="black")
        arr = np.array(img)
        for _ in range(3):
            self.line(arr)
        badge = np.full((34, 34, 3), 60, dtype=np.uint8)
        badge[8:28, 8:28] = 245
        self.read_badge(badge, (0, 0, 34, 34))
