# -*- coding: utf-8 -*-
"""诊断：在企业微信合成测试图上验证「气泡底色 + 行投影」切分方案"""
from pathlib import Path
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent.parent
img = Image.open(HERE / "samples" / "s3_clean.png").convert("RGB")
A = np.array(img).astype(np.int32)
H, W, _ = A.shape
out = []
def p(*a): out.append(" ".join(str(x) for x in a))

r, g, b = A[:, :, 0], A[:, :, 1], A[:, :, 2]
gray = (r * 299 + g * 587 + b * 114) // 1000
neutral = (np.abs(r - g) < 8) & (np.abs(g - b) < 8)

# 1) 页面背景 vs 气泡底色
from collections import Counter
cnt = Counter(map(tuple, A.reshape(-1, 3)[::37]))
p("TOP20 颜色:")
for c, n in cnt.most_common(20):
    p(f"   rgb{c}  {n}")

# 2) 气泡底色 mask：浅灰中性色
bubble = neutral & (gray >= 222) & (gray <= 250)
p(f"\nbubble mask 覆盖: {bubble.mean()*100:.1f}%")

rows_cov = bubble.mean(axis=1)
p("\n逐行气泡覆盖(>25% 视为气泡行)，打印连续段:")
inb = rows_cov > 0.25
segs, s = [], None
for y in range(H):
    if inb[y] and s is None: s = y
    elif not inb[y] and s is not None: segs.append((s, y)); s = None
if s is not None: segs.append((s, H))
for (y1, y2) in segs:
    band = bubble[y1:y2]
    xs = np.nonzero(band.any(axis=0))[0]
    p(f"   y={y1:4d}-{y2:4d} h={y2-y1:3d}  x={xs[0] if len(xs) else -1}-{xs[-1] if len(xs) else -1}")

# 3) 深色文字行投影（用于找名字行）
dark = gray < 150
rows_dark = dark.sum(axis=1)
p("\n深色文字行段(>0 像素):")
segs2, s = [], None
for y in range(H):
    if rows_dark[y] > 0 and s is None: s = y
    elif rows_dark[y] == 0 and s is not None:
        if y - s >= 6: segs2.append((s, y))
        s = None
if s is not None: segs2.append((s, H))
for (y1, y2) in segs2:
    band = dark[y1:y2]
    xs = np.nonzero(band.any(axis=0))[0]
    p(f"   y={y1:4d}-{y2:4d} h={y2-y1:3d}  x={xs[0] if len(xs) else -1}-{xs[-1] if len(xs) else -1}  "
      f"居中={abs((xs[0]+xs[-1])/2 - W/2) < W*0.1 if len(xs) else '?'}")

(HERE / "diag_out.txt").write_text("\n".join(out), encoding="utf-8")
print("ok")


