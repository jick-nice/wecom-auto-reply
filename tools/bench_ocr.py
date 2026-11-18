# -*- coding: utf-8 -*-
"""对比 EasyOCR / RapidOCR 在同一张企业微信截图上的速度与识别结果"""
import sys, time, json
from pathlib import Path

import numpy as np
from PIL import Image

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def bench_rapid(img_path, rounds=3):
    from rapidocr_onnxruntime import RapidOCR
    t0 = time.time()
    engine = RapidOCR()
    init = time.time() - t0
    img = np.array(Image.open(img_path).convert("RGB"))
    # warmup
    engine(img)
    ts = []
    res = None
    for _ in range(rounds):
        t = time.time()
        res, _ = engine(img)
        ts.append(time.time() - t)
    return init, min(ts), sum(ts) / len(ts), res


def bench_easyocr(img_path, rounds=2):
    import easyocr
    t0 = time.time()
    reader = easyocr.Reader(["ch_sim", "en"], gpu=False, quantize=True,
                            model_storage_directory=str(Path(__file__).resolve().parent.parent / "models"),
                            download_enabled=True)
    init = time.time() - t0
    arr = np.array(Image.open(img_path).convert("RGB"))
    reader.readtext(arr)  # warmup
    ts = []
    out = None
    for _ in range(rounds):
        t = time.time()
        out = reader.readtext(arr, detail=1, paragraph=False,
                              text_threshold=0.3, low_text=0.2, link_threshold=0.4)
        ts.append(time.time() - t)
    return init, min(ts), sum(ts) / len(ts), out


if __name__ == "__main__":
    for name in ["s3_clean.png", "s2_photo.jpg", "s1_photo.jpg"]:
        p = SAMPLES / name
        if not p.exists():
            continue
        w, h = Image.open(p).size
        print("=" * 78)
        print(f"[{name}] {w}x{h}")
        try:
            init, tmin, tavg, res = bench_rapid(p)
            print(f"  RapidOCR  init={init:.2f}s  frame={tmin*1000:.0f}ms(min)/{tavg*1000:.0f}ms(avg)")
            for box, txt, score in (res or [])[:40]:
                x1, y1 = box[0]
                print(f"    y={y1:6.0f} x={x1:6.0f} conf={score:.2f}  {txt!r}")
        except Exception as e:
            print("  RapidOCR FAILED:", e)
        if "--easy" in sys.argv and name == "s3_clean.png":
            try:
                init, tmin, tavg, res = bench_easyocr(p)
                print(f"  EasyOCR   init={init:.2f}s  frame={tmin*1000:.0f}ms(min)/{tavg*1000:.0f}ms(avg)")
                for box, txt, score in res[:40]:
                    print(f"    y={box[0][1]:6.0f} x={box[0][0]:6.0f} conf={score:.2f}  {txt!r}")
            except Exception as e:
                print("  EasyOCR FAILED:", e)

