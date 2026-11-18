# -*- coding: utf-8 -*-
"""受控实验：企业微信被别的窗口盖住时，PrintWindow 能不能抓到「新消息」。

步骤：开一个最大化的记事本盖住企业微信 → 用手机发一条唯一字符串 →
      抓 PrintWindow → OCR 里找这个字符串。找到 = 后台抓屏可靠。
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate, segment                        # noqa: E402
from app.capture import Grabber, is_foreground, pick_wecom_window, set_dpi_aware  # noqa: E402
from app.ocr import Ocr                                # noqa: E402

ADB = r"D:\Android\Sdk\platform-tools\adb.exe"
u32 = ctypes.windll.user32
TOKEN = sys.argv[1] if len(sys.argv) > 1 else "7788"

set_dpi_aware()
hwnd = pick_wecom_window()
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
    texts = []
    for m in segment.pair_messages(rows):
        texts.append(o.read_row(segment.crop(crop, m.body_row, pad_x=2, pad_y=2)))
    hit = any(TOKEN in t for t in texts)
    print(f"  [{tag}] 前台={is_foreground(hwnd)} 行数={len(rows)} 命中{TOKEN}={hit}")
    print(f"        最后几条: {texts[-4:]}")
    return hit


print("1) 先确认盖住之前能看到当前内容")
rows_now("遮挡前")

print("2) 开记事本并最大化盖住")
p = subprocess.Popen(["notepad.exe"])
time.sleep(1.2)
h_notepad = u32.FindWindowW("Notepad", None)
if h_notepad:
    u32.ShowWindow(h_notepad, 3)          # SW_MAXIMIZE
    u32.SetForegroundWindow(h_notepad)
time.sleep(1.0)
print(f"   记事本 hwnd={h_notepad} 企业微信前台={is_foreground(hwnd)}")

print(f"3) 手机发一条 {TOKEN}")
subprocess.run([ADB, "shell", "input", "motionevent", "DOWN", "483", "2255"])
subprocess.run([ADB, "shell", "input", "motionevent", "UP", "483", "2255"])
time.sleep(0.5)
subprocess.run([ADB, "shell", "input", "text", TOKEN])
time.sleep(0.5)
# 找蓝色发送键（键盘弹起时在 y≈1073 / 收起时 y≈2276）
raw = subprocess.run([ADB, "exec-out", "screencap", "-p"], capture_output=True).stdout
import io                                                  # noqa: E402
from PIL import Image                                      # noqa: E402
img = np.array(Image.open(io.BytesIO(raw)).convert("RGB")).astype(int)
r, gg, b = img[:, :, 0], img[:, :, 1], img[:, :, 2]
m = (b > 170) & (b - r > 55) & (b - gg > 35) & (gg > 80)
ys, xs = np.nonzero(m)
if len(xs) > 500:
    bx, by = int(np.median(xs)), int(np.median(ys))
    subprocess.run([ADB, "shell", "input", "motionevent", "DOWN", str(bx), str(by)])
    subprocess.run([ADB, "shell", "input", "motionevent", "UP", str(bx), str(by)])
    print(f"   点了发送键 ({bx},{by})")
time.sleep(3.0)

print("4) 在被遮挡状态下抓 PrintWindow，看能不能看到这条")
hit1 = rows_now("遮挡中-3s")
time.sleep(4.0)
hit2 = rows_now("遮挡中-7s")

print("5) 关掉记事本，恢复后再看")
subprocess.run(["taskkill", "/PID", str(p.pid), "/F"], capture_output=True)
time.sleep(2.0)
hit3 = rows_now("恢复后")

print(f"\n结论：遮挡3s命中={hit1} 遮挡7s命中={hit2} 恢复后命中={hit3}")
g.close()
