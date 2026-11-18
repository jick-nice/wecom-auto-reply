# -*- coding: utf-8 -*-
"""诊断：从真机窗口截图里自动找出「聊天消息区」（不靠手工框选）"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
A = np.array(Image.open(ROOT / "samples" / "live_win.png").convert("RGB")).astype(np.int32)
H, W, _ = A.shape
luma = (A[:, :, 0] * 299 + A[:, :, 1] * 587 + A[:, :, 2] * 114) // 1000
out = []
def p(*a): out.append(" ".join(str(x) for x in a))

p(f"窗口截图 {W}x{H}")
p(f"整体亮度中位数 {np.median(luma):.0f}")

# ---------- 1. 竖向分隔线：整列都是浅灰、且方差极小 ----------
lightline = ((luma >= 205) & (luma <= 248)).mean(axis=0)     # 每列"浅灰像素"占比
colstd = luma.std(axis=0)
cand = np.flatnonzero((lightline > 0.90) & (colstd < 14))
p(f"\n候选分隔列({len(cand)}): {cand.tolist()[:40]}")
# 合并相邻列
groups = []
for x in cand:
    if groups and x - groups[-1][-1] <= 2:
        groups[-1].append(int(x))
    else:
        groups.append([int(x)])
p("分隔线位置:")
for g in groups:
    p(f"   x={g[0]}~{g[-1]}  (line={lightline[g[0]]:.2f}, std={colstd[g[0]]:.1f})")

# ---------- 2. 横向分隔线 / 输入框上边 ----------
rowline = ((luma >= 205) & (luma <= 248)).mean(axis=1)
rowstd = luma.std(axis=1)
rcand = np.flatnonzero((rowline > 0.55) & (rowstd < 40))
p(f"\n候选横线/空白行({len(rcand)}) 前 40: {rcand.tolist()[:40]}")

# ---------- 3. 气泡底色（浅灰）区域 ----------
bubble = (np.abs(A[:, :, 0] - A[:, :, 1]) < 6) & (np.abs(A[:, :, 1] - A[:, :, 2]) < 6) \
         & (luma >= 228) & (luma <= 246)
p(f"\n气泡色像素占比 {bubble.mean()*100:.1f}%")
rows = np.flatnonzero(bubble.mean(axis=1) > 0.02)
if rows.size:
    p(f"气泡行范围: y={rows.min()}~{rows.max()}")

# ---------- 4. 深色文字块（判断聊天区左边界） ----------
dark = luma < 150
dcols = dark.mean(axis=0)
p(f"\n深色文字列分布（每 40 列取均值）:")
for x0 in range(0, W, 40):
    seg = dcols[x0:x0 + 40]
    p(f"   x={x0:4d}-{min(x0+40,W):4d}  {seg.mean()*100:5.1f}%")
drows = dark.mean(axis=1)
p(f"深色文字行分布（每 20 行取均值）:")
for y0 in range(0, H, 20):
    seg = drows[y0:y0 + 20]
    p(f"   y={y0:4d}-{min(y0+20,H):4d}  {seg.mean()*100:5.1f}%")

(ROOT / "samples" / "locate_diag.txt").write_text("\n".join(out), encoding="utf-8")
print("\n".join(out))
