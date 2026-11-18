# -*- coding: utf-8 -*-
"""真机调参：名字行怎么读才准 + 徽章检测到底命中了什么"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import segment                                   # noqa: E402
from app.capture import Grabber, pick_wecom_window, set_dpi_aware  # noqa: E402
from app.ocr import Ocr                                   # noqa: E402


def rec(o, img):
    res, _ = o.engine(np.array(img), use_det=False, use_cls=False, use_rec=True)
    return "".join(t for t, _ in (res or [])).strip()


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

    print("=== 徽章检测 ===")
    for m in msgs:
        body = segment.crop(crop, m.body_row, pad_x=2, pad_y=2)
        a = body.astype(np.int16)
        r, gg, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
        sat = np.maximum(np.maximum(r, gg), b) - np.minimum(np.minimum(r, gg), b)
        print(f"  body {body.shape} sat>45 像素数={int((sat > 45).sum())} max_sat={int(sat.max())}")
        boxes = o.find_badges(body)
        print("   find_badges ->", boxes)
        if boxes:
            x1, y1, x2, y2 = boxes[0]
            print("   命中区域像素样本:", body[y1:y2:4, x1:x2:4].reshape(-1, 3)[:6].tolist())

    print("\n=== 名字行识别方案对比 ===")
    for m in msgs:
        if not m.sender_row:
            continue
        name = segment.crop(crop, m.sender_row, pad_x=2, pad_y=2)
        print(f"  原图 {name.shape}")
        base = Image.fromarray(np.ascontiguousarray(name))
        variants = {
            "×4 LANCZOS(现状)": base.resize((base.width * 4, base.height * 4), Image.LANCZOS),
            "×6 LANCZOS": base.resize((base.width * 6, base.height * 6), Image.LANCZOS),
            "×4 NEAREST": base.resize((base.width * 4, base.height * 4), Image.NEAREST),
            "二值化后×4": None,
            "×3 BICUBIC": base.resize((base.width * 3, base.height * 3), Image.BICUBIC),
        }
        arr = np.array(base.convert("L"))
        thr = max(60, int(arr.mean() - arr.std()))
        bw = ((arr > thr) * 255).astype(np.uint8)
        bwimg = Image.fromarray(bw).convert("RGB")
        variants["二值化后×4"] = bwimg.resize((bwimg.width * 4, bwimg.height * 4), Image.LANCZOS)
        for k, v in variants.items():
            print(f"    {k:16} -> {rec(o, v)!r}")
    g.close()


if __name__ == "__main__":
    main()
