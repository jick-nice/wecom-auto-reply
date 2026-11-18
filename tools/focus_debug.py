# -*- coding: utf-8 -*-
"""点击后敲字到底去了哪？把整窗存图，肉眼确认。"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate                                   # noqa: E402
from app.capture import (Grabber, client_rect_screen, force_foreground,  # noqa: E402
                         is_foreground, pick_wecom_window, restore, set_dpi_aware)
from app.ocr import Ocr                                  # noqa: E402
from app.sender import INPUT, Sender, _key               # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
ART = ROOT / "_artifacts"


def tap(vk):
    arr = (INPUT * 2)(_key(vk), _key(vk, up=True))
    user32.SendInput(2, arr, ctypes.sizeof(INPUT))


def main():
    set_dpi_aware()
    hwnd = pick_wecom_window()
    restore(hwnd)
    force_foreground(hwnd)
    time.sleep(0.4)
    g = Grabber()
    full = np.array(g.grab_window_client(hwnd, copy=True))
    area = locate.locate(full)
    ox, oy = client_rect_screen(hwnd)[:2]
    s = Sender((ox + area.input_center[0], oy + area.input_center[1]))
    print("input_pos =", s.input_pos, "area =", area, "前台 =", is_foreground(hwnd))

    def snap(tag):
        f = np.array(g.grab_window_client(hwnd, copy=True))
        Image.fromarray(np.ascontiguousarray(f[:, :, ::-1])).save(ART / f"{tag}.png")
        return f

    # 1) 不点击，直接敲
    tap(0x58)
    time.sleep(0.3)
    snap("dbg_typed_noclick")
    print("1) 不点击直接敲 x —— 已存 dbg_typed_noclick.png，前台 =", is_foreground(hwnd))
    tap(0x08)
    time.sleep(0.2)

    # 2) 点击后敲
    s.click_input()
    time.sleep(0.4)
    print("2) 点击后前台 =", is_foreground(hwnd), "光标 =", user32.GetCursorPos(ctypes.byref(ctypes.wintypes.POINT())) if False else "n/a")
    tap(0x58)
    time.sleep(0.4)
    snap("dbg_typed_after_click")
    print("   已存 dbg_typed_after_click.png")
    tap(0x08)
    time.sleep(0.2)

    # 3) 只把鼠标移过去（不点击）再敲
    user32.SetCursorPos(*s.input_pos)
    time.sleep(0.3)
    tap(0x58)
    time.sleep(0.4)
    snap("dbg_typed_moveonly")
    print("3) 只移动鼠标不点击再敲 —— 已存 dbg_typed_moveonly.png")
    tap(0x08)

    g.close()


if __name__ == "__main__":
    main()
