# -*- coding: utf-8 -*-
"""量一下：点击输入框后要等多久，键盘输入才真的落进去。

做法：点击 → 等 N ms → 敲 x → 截输入框 OCR → 退格清掉。对 N 扫描。
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate                                   # noqa: E402
from app.capture import (Grabber, client_rect_screen, force_foreground,  # noqa: E402
                         pick_wecom_window, restore, set_dpi_aware)
from app.ocr import Ocr                                  # noqa: E402
from app.sender import Sender, _key, INPUT               # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)


def tap(vk):
    ev = (_key(vk) if False else None)
    from app.sender import _key as K
    arr = (INPUT * 2)(K(vk), K(vk, up=True))
    user32.SendInput(2, arr, ctypes.sizeof(INPUT))


def main():
    set_dpi_aware()
    hwnd = pick_wecom_window()
    restore(hwnd)
    force_foreground(hwnd)
    time.sleep(0.4)
    g = Grabber()
    o = Ocr()
    o.warmup()
    full = np.array(g.grab_window_client(hwnd, copy=True))
    area = locate.locate(full)
    ox, oy = client_rect_screen(hwnd)[:2]
    s = Sender((ox + area.input_center[0], oy + area.input_center[1]))
    print("输入框屏幕坐标 =", s.input_pos, " 聊天区 =", area)

    # 输入框区域（窗口坐标 → 全区截图坐标）
    ix1, iy1, ix2, iy2 = area.input_x1, area.input_y1 - 14, area.input_x2, area.input_y2 + 4
    print(f"输入框取样区 = ({ix1},{iy1})-({ix2},{iy2})")

    def read_input():
        full = np.array(g.grab_window_client(hwnd, copy=True))
        patch = full[max(0, iy1):iy2, max(0, ix1):ix2]
        return o.line(patch)

    for delay in (0.010, 0.030, 0.060, 0.100, 0.150):
        tap(0x08)                     # 先退格，清空
        time.sleep(0.05)
        s.click_input()
        time.sleep(delay)
        tap(0x58)                     # X 键
        time.sleep(0.25)
        got = read_input()
        print(f"  点击后等 {delay*1000:5.0f}ms → 输入框内容 {got!r}  "
              f"{'✅ 成功' if 'x' in got.lower() else '❌ 丢字'}")
        tap(0x08)
        time.sleep(0.05)
        tap(0x08)                     # 清干净

    g.close()


if __name__ == "__main__":
    main()
