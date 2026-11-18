# -*- coding: utf-8 -*-
"""诊断：变换后行切分/锚点到底发生了什么。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import segment                      # noqa: E402

base = Image.open(ROOT / "samples" / "s3_clean.png").convert("RGB")


def probe(tag, im):
    img = np.array(im)[:, :, ::-1]
    img = segment.normalize(img)
    med = float(np.median(img))
    thr = segment.auto_thr(img, 150)
    lay = segment.Layout()
    rows = segment.analyze(img, lay, dark_thr=thr)
    print(f"\n--- {tag} 中位亮度={med:.0f} thr={thr} 行数={len(rows)}")
    for r in rows[:8]:
        print(f"    {r}")
    if rows:
        lay.learn(rows)
        print("    学到锚点 name_x=", lay.name_x, "content_x=", lay.content_x)
        rows = segment.analyze(img, lay, dark_thr=thr)
        kinds = [r.kind for r in rows]
        print("    重算后 kinds=", kinds[:10])


probe("原图", base)
probe("scale=2.0", base.resize((base.width * 2, base.height * 2), Image.LANCZOS))
probe("bright=1.3", ImageEnhance.Brightness(base).enhance(1.3))
probe("contrast=0.75", ImageEnhance.Contrast(base).enhance(0.75))
