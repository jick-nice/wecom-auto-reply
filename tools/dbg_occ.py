# -*- coding: utf-8 -*-
"""决定性实验：企业微信被完全盖住时，抓到的画面会不会停更？请求重绘能不能救？

用 HWND_TOPMOST + SWP_NOACTIVATE 把记事本糊在企业微信上（不抢焦点、不影响输入），
然后手机发一条唯一字符串，看 PrintWindow 什么时候能看见。
"""
from __future__ import annotations

import ctypes
import io
import subprocess
import sys
import time
import ctypes.wintypes as wt
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate, segment                        # noqa: E402
from app.capture import Grabber, client_rect_screen, is_foreground, pick_wecom_window, set_dpi_aware  # noqa: E402
from app.ocr import Ocr                                # noqa: E402

ADB = r"D:\Android\Sdk\platform-tools\adb.exe"
u32 = ctypes.windll.user32
TOKEN = sys.argv[1] if len(sys.argv) > 1 else "KJ7788"

set_dpi_aware()
hwnd = pick_wecom_window()
ox, oy, cw, ch = client_rect_screen(hwnd)
g = Grabber()
o = Ocr()
o.warmup()


def rows_now(tag):
    full = np.array(g.grab_window_client(hwnd, copy=True))
    a = locate.locate(full)
    crop = full[a.y1:a.y2, a.x1:a.x2]
    lay = segment.Layout()
    rows = segment.analyze(crop, lay, dark_thr=segment.auto_thr(crop))
    if rows:
        lay.learn(rows)
        rows = segment.analyze(crop, lay, dark_thr=segment.auto_thr(crop))
    texts = [o.read_row(segment.crop(crop, m.body_row, pad_x=2, pad_y=2))
             for m in segment.pair_messages(rows)]
    hit = any(TOKEN in t for t in texts)
    print(f"  [{tag}] 前台={is_foreground(hwnd)} 行数={len(rows)} 命中={hit} 尾部={texts[-2:]}")
    return hit


print("1) 遮挡前")
rows_now("before")

print("2) 记事本置顶盖住企业微信（不抢焦点）")
p = subprocess.Popen(["notepad.exe"])
time.sleep(1.0)
h_np = u32.FindWindowW("Notepad", None)
SWP_NOMOVE, SWP_NOSIZE = 0x0002, 0x0001
SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
u32.SetWindowPos(h_np, -1, ox - 10, oy - 40, cw + 40, ch + 80,
                 SWP_NOACTIVATE | SWP_SHOWWINDOW)
time.sleep(0.8)
print(f"   记事本={h_np} 企业微信前台={is_foreground(hwnd)}（前台仍是它，但被盖住了）")

print(f"3) 手机发 {TOKEN}")
subprocess.run([ADB, "shell", "input", "motionevent", "DOWN", "483", "2255"])
subprocess.run([ADB, "shell", "input", "motionevent", "UP", "483", "2255"])
time.sleep(0.5)
subprocess.run([ADB, "shell", "input", "text", TOKEN])
time.sleep(0.5)
raw = subprocess.run([ADB, "exec-out", "screencap", "-p"], capture_output=True).stdout
img = np.array(Image.open(io.BytesIO(raw)).convert("RGB")).astype(int)
r, gg, b = img[:, :, 0], img[:, :, 1], img[:, :, 2]
m = (b > 170) & (b - r > 55) & (b - gg > 35) & (gg > 80)
ys, xs = np.nonzero(m)
if len(xs) > 500:
    bx, by = int(np.median(xs)), int(np.median(ys))
    subprocess.run([ADB, "shell", "input", "motionevent", "DOWN", str(bx), str(by)])
    subprocess.run([ADB, "shell", "input", "motionevent", "UP", str(bx), str(by)])
time.sleep(3.0)

print("4) 被盖住状态抓（3s / 6s）")
h1 = rows_now("covered-3s")
time.sleep(3.0)
h2 = rows_now("covered-6s")

print("5) 请求企业微信重绘（InvalidateRect+UpdateWindow，不发输入、不抢焦点）")
for _ in range(3):
    u32.InvalidateRect(hwnd, None, True)
    u32.UpdateWindow(hwnd)
    time.sleep(0.3)
time.sleep(1.0)
h3 = rows_now("after-nudge")

print("6) 关记事本")
subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True)
time.sleep(1.5)
h4 = rows_now("restored")

print(f"\n结论：盖住3s={h1} 盖住6s={h2} 重绘后={h3} 恢复后={h4}")
print("  → 若 covered 全 False 而 after-nudge True，说明「请求重绘」能救回后台抓屏")
g.close()
