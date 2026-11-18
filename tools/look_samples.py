# -*- coding: utf-8 -*-
"""看一眼 samples 里三张真实截图的识别结果（作为鲁棒性测试的基准）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate, segment                      # noqa: E402
from app.ocr import Ocr                              # noqa: E402
from app.numparse import reply_code                  # noqa: E402

o = Ocr()
o.warmup()


def run(name):
    img = np.array(Image.open(ROOT / "samples" / name).convert("RGB"))[:, :, ::-1]
    print(f"\n=== {name} {img.shape} ===")
    a = locate.locate(img)
    print("locate:", a, "found=", a.found)
    crop = img[a.y1:a.y2, a.x1:a.x2] if a.found else img
    lay = segment.Layout()
    rows = segment.analyze(crop, lay)
    if rows:
        lay.learn(rows)
        rows = segment.analyze(crop, lay)
    print("行数:", len(rows))
    for m in segment.pair_messages(rows):
        who = ""
        if m.sender_row is not None:
            who = o.read_name(segment.crop(crop, m.sender_row, pad_x=3, pad_y=6))
        txt = o.read_row(segment.crop(crop, m.body_row, pad_x=2, pad_y=2))
        print(f"  [{who or '(无名字)'}] {txt!r}  -> code={reply_code(txt)}")


for n in ("s3_clean.png", "s1_photo.jpg", "s2_photo.jpg"):
    run(n)
