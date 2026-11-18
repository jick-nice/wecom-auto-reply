# -*- coding: utf-8 -*-
"""名字行：裁剪边距 / 对比度拉伸 / 放大倍数 的对比实验"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import segment                                   # noqa: E402
from app.capture import Grabber, pick_wecom_window, set_dpi_aware  # noqa: E402
from app.ocr import Ocr                                   # noqa: E402


def rec(o, img):
    res, _ = o.engine(np.array(img), use_det=False, use_cls=False, use_rec=True)
    return "".join(t for t, _ in (res or [])).strip()


def stretch(rgb: np.ndarray) -> np.ndarray:
    a = rgb.astype(np.float32)
    lo, hi = np.percentile(a, 2), np.percentile(a, 98)
    if hi - lo < 8:
        return rgb
    return np.clip((a - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)


def main():
    set_dpi_aware()
    g = Grabber()
    o = Ocr()
    o.warmup()
    full = g.grab_window_client(pick_wecom_window())
    crop = full[64:604, 310:919]
    lay = segment.Layout()
    rows = segment.analyze(crop, lay)
    lay.learn(rows)
    rows = segment.analyze(crop, lay)
    msgs = segment.pair_messages(rows)

    for m in msgs:
        if not m.sender_row:
            continue
        r = m.sender_row
        print(f"名字行原始 y={r.y1}..{r.y2} h={r.y2 - r.y1} x={r.x1}..{r.x2}")
        for pad_y in (2, 3, 4, 6):
            for scale in (4, 6, 8):
                piece = segment.crop(crop, r, pad_x=3, pad_y=pad_y)
                base = Image.fromarray(np.ascontiguousarray(piece))
                a = base.resize((base.width * scale, base.height * scale), Image.LANCZOS)
                b = Image.fromarray(np.ascontiguousarray(stretch(piece)))
                b = b.resize((b.width * scale, b.height * scale), Image.LANCZOS)
                print(f"  pad_y={pad_y} ×{scale}: 原样={rec(o, a)!r:14} 拉伸={rec(o, b)!r}")
    g.close()


if __name__ == "__main__":
    main()
