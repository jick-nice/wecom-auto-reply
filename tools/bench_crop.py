# -*- coding: utf-8 -*-
"""裁剪小区域 + rec-only 的速度/准确率测试（关键可行性验证）"""
import io, json, sys, time
from pathlib import Path
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

OUT = Path(__file__).resolve().parent.parent / "bench_out.txt"
buf = []
def p(*a):
    s = " ".join(str(x) for x in a)
    buf.append(s)

engine = RapidOCR()

def run(img, **kw):
    t = time.time()
    res, _ = engine(img, **kw)
    return (time.time() - t) * 1000, res

def show(tag, img, **kw):
    arr = np.array(img.convert("RGB"))
    ms, res = run(arr, **kw)          # warm
    ms, res = run(arr, **kw)
    p(f"\n--- {tag}  size={img.size}  {ms:.0f}ms  kw={kw}")
    for item in (res or []):
        if len(item) == 3:
            box, txt, score = item
        else:
            txt, score = item
        p(f"      {txt!r}  conf={float(score):.2f}")
    return res

src = Image.open(Path(__file__).resolve().parent.parent / "samples" / "s3_clean.png").convert("RGB")
p(f"source {src.size}")

# 1) 整图
show("FULL det+rec", src)
# 2) 单条消息气泡（“宜居园 3️⃣”）
bubble = src.crop((60, 185, 340, 265))
show("bubble 宜居园 (orig)", bubble)
show("bubble 宜居园 (x2)", bubble.resize((bubble.width * 2, bubble.height * 2), Image.LANCZOS))
show("bubble 宜居园 rec-only x2", bubble.resize((bubble.width * 2, bubble.height * 2), Image.LANCZOS),
     use_det=False, use_cls=False, use_rec=True)
# 3) 单个数字气泡 “3”
d3 = src.crop((45, 470, 130, 545))
show("single digit bubble (x3)", d3.resize((d3.width * 3, d3.height * 3), Image.LANCZOS))
show("single digit bubble rec-only x3", d3.resize((d3.width * 3, d3.height * 3), Image.LANCZOS),
     use_det=False, use_cls=False, use_rec=True)
# 4) 底部 4 条消息一起（模拟“只盯最新几条”）
bottom = src.crop((40, 400, 800, 700))
show("bottom strip x1", bottom)
show("bottom strip x1.5", bottom.resize((int(bottom.width * 1.5), int(bottom.height * 1.5)), Image.LANCZOS))

OUT.write_text("\n".join(buf), encoding="utf-8")
print("written", OUT)

