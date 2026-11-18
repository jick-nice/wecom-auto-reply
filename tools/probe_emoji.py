# -*- coding: utf-8 -*-
"""验证：彩色 emoji 数字徽章 -> 反色 -> rec-only 能否读出数字"""
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps
from rapidocr_onnxruntime import RapidOCR

HERE = Path(__file__).resolve().parent.parent
src = Image.open(HERE / "samples" / "s3_clean.png").convert("RGB")

# 先切一大块“宜居园 3️⃣”气泡，肉眼确认坐标
src.crop((60, 180, 420, 280)).resize((720, 200), Image.LANCZOS).save(HERE / "probe_bubble.png")

engine = RapidOCR()
out = []

def probe(tag, img):
    arr = np.array(img.convert("RGB"))
    ms_res = engine(arr, use_det=False, use_cls=False, use_rec=True)
    out.append(f"{tag} size={img.size} -> {ms_res[0]}")

# 高饱和色块检测：找出蓝色徽章
arr = np.array(src)
crop = arr[180:280, 60:420].astype(np.int16)
r, g, b = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
# 蓝色徽章：b 明显大于 r
mask = (b - r > 40) & (b > 120)
ys, xs = np.nonzero(mask)
if len(xs):
    x1, x2 = xs.min() + 60, xs.max() + 60
    y1, y2 = ys.min() + 180, ys.max() + 180
    out.append(f"blue badge bbox in full img: x={x1}-{x2} y={y1}-{y2} (w={x2-x1} h={y2-y1}) px={mask.sum()}")
    badge = src.crop((x1 - 2, y1 - 2, x2 + 3, y2 + 3))
    badge.resize((badge.width * 4, badge.height * 4), Image.LANCZOS).save(HERE / "probe_badge.png")
    probe("badge as-is  x4", badge.resize((badge.width * 4, badge.height * 4), Image.LANCZOS))
    inv = ImageOps.invert(badge.convert("RGB"))
    inv.resize((inv.width * 4, inv.height * 4), Image.LANCZOS).save(HERE / "probe_badge_inv.png")
    probe("badge invert x4", inv.resize((inv.width * 4, inv.height * 4), Image.LANCZOS))
    gray = badge.convert("L").resize((badge.width * 4, badge.height * 4), Image.LANCZOS)
    inv2 = ImageOps.invert(gray.convert("RGB"))
    probe("badge gray-invert x4", inv2)
else:
    out.append("no blue badge found")

(HERE / "probe_out.txt").write_text("\n".join(out), encoding="utf-8")
print("ok")

