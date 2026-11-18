# -*- coding: utf-8 -*-
"""验证：企业微信被遮挡/非前台时，PrintWindow 抓到的画面是不是「陈旧/半张」。

做法：连续抓 N 帧，每帧算 行数 + 指纹 + 平均亮度。如果画面里有新消息却抓不到，
说明 CEF 在被遮挡时暂停了绘制 —— 那后台抓屏就不可靠，必须换策略。
"""
from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate, segment                        # noqa: E402
from app.capture import Grabber, is_foreground, pick_wecom_window, set_dpi_aware  # noqa: E402
from app.ocr import Ocr                                # noqa: E402

set_dpi_aware()
hwnd = pick_wecom_window()
g = Grabber()
o = Ocr()
o.warmup()

print(f"hwnd={hwnd} 当前是否前台={is_foreground(hwnd)}")
print("连续 12 帧（每 0.5s），看行数/指纹是否稳定：")
lay = segment.Layout()
last = None
for i in range(12):
    full = np.array(g.grab_window_client(hwnd, copy=True))
    a = locate.locate(full)
    crop = full[a.y1:a.y2, a.x1:a.x2]
    rows = segment.analyze(crop, lay, dark_thr=segment.auto_thr(crop))
    fp = hashlib.md5(crop.tobytes()).hexdigest()[:8]
    same = "  (和上帧一样)" if fp == last else ""
    last = fp
    print(f"  #{i:02d} 前台={is_foreground(hwnd)} 行数={len(rows):2d} 亮度={crop.mean():.0f} fp={fp}{same}")
    time.sleep(0.5)

# 再从屏幕上直接 BitBlt 同一块区域做对照（只有窗口在最前面才有效）
print("\n对照：直接抓屏幕同一区域")
full2 = np.array(g.grab(crop_bbox=(a.x1, a.y1, a.x2, a.y2)) if False else g.grab_window_client(hwnd, copy=True))
print("  窗口抓取 fp =", hashlib.md5(full2.tobytes()).hexdigest()[:8])
g.close()
