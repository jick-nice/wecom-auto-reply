# -*- coding: utf-8 -*-
"""冷启动成本：新派单那种短行（含 emoji 徽章）一次要花多少毫秒"""
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import segment                                   # noqa: E402
from app.ocr import Ocr                                   # noqa: E402

IMG = np.array(Image.open(ROOT / "samples" / "s3_clean.png").convert("RGB"))


def cold(o, crop, label, n=3):
    ts = []
    outs = []
    for _ in range(n):
        o._cache.clear()
        o._row_cache.clear()
        t = time.perf_counter()
        txt = o.read_row(crop)
        ts.append((time.perf_counter() - t) * 1000)
        outs.append(txt)
    print(f"  {label:26} {min(ts):7.1f}ms   -> {outs[-1]!r}")


def main():
    o = Ocr()
    o.warmup()

    # s3_clean.png 的实测行
    cases = [
        ("测试项目 3️⃣（带徽章）", IMG[208:245, 80:600]),
        ("「4」单字", IMG[355:385, 80:200]),
        ("「3」单字", IMG[500:530, 80:200]),
        ("「@测试用户C」", IMG[785:820, 80:400]),
        ("名字「测试用户D」", IMG[150:185, 60:200]),
        ("名字「测试用户B」", IMG[295:330, 60:200]),
    ]
    print("冷启动（清空缓存）:")
    for label, crop in cases:
        if crop.size:
            cold(o, crop, label)

    print("\n连续两帧（第二帧应命中缓存）:")
    crop = IMG[208:245, 80:600]
    o._cache.clear()
    o._row_cache.clear()
    for i in range(3):
        t = time.perf_counter()
        txt = o.read_row(crop)
        print(f"  #{i} {(time.perf_counter() - t) * 1000:7.2f}ms -> {txt!r}")

    print("\n徽章检测（应只命中真 emoji）:")
    for label, crop in cases:
        if crop.size:
            print(f"  {label:22} -> {o.find_badges(crop)}")


if __name__ == "__main__":
    main()
