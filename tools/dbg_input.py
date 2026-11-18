# -*- coding: utf-8 -*-
"""定位「字到底有没有进输入框」：点一下、打一个 7、把输入框整块存图。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate                                     # noqa: E402
from app.capture import (Grabber, client_rect_screen, force_foreground,  # noqa: E402
                         pick_wecom_window, restore, set_dpi_aware)
from app.ocr import Ocr                                    # noqa: E402
from app.sender import INPUT, Sender, _key                  # noqa: E402

ART = ROOT / "_artifacts"


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
    ox, oy, cw, ch = client_rect_screen(hwnd)
    print("area =", area, " origin =", (ox, oy))

    def snap(tag):
        f = np.array(g.grab_window_client(hwnd, copy=True))
        Image.fromarray(np.ascontiguousarray(f[:, :, ::-1])).save(ART / f"{tag}.png")
        return f

    f0 = snap("dbg_before")
    print("输入框行 y 范围（client）:", area.input_y1, area.input_y2,
          " x:", area.input_x1, area.input_x2)

    # 点输入框（用 locate 给的中心）
    s = Sender((ox + area.input_center[0], oy + area.input_center[1]))
    print("点击", s.input_pos)
    s.click_input()
    time.sleep(0.25)

    # 打一个 7（不带回车）
    arr = (INPUT * 2)(_key(0x37), _key(0x37, up=True))
    import ctypes
    ctypes.windll.user32.SendInput(2, arr, ctypes.sizeof(INPUT))
    time.sleep(0.35)

    f1 = snap("dbg_after_type")
    # 对比：输入框那块有没有变化
    box0 = f0[area.input_y1:area.input_y2, area.input_x1:area.input_x2]
    box1 = f1[area.input_y1:area.input_y2, area.input_x1:area.input_x2]
    d = int((np.abs(box0.astype(int) - box1.astype(int)).sum(axis=2) > 25).sum())
    print("输入框变化像素 =", d)

    # 多种裁剪都读一遍
    for name, (y1, y2, x1, x2) in {
        "整框": (area.input_y1, area.input_y2, area.input_x1, area.input_x2),
        "上左200x36": (area.input_y1 + 4, area.input_y1 + 40, area.input_x1 + 4, area.input_x1 + 210),
        "上左400x40": (area.input_y1 + 2, area.input_y1 + 44, area.input_x1 + 2, area.input_x1 + 400),
    }.items():
        piece = f1[y1:y2, x1:x2]
        print(f"  {name:12} {piece.shape} -> {o.line(np.ascontiguousarray(piece))!r}")

    # 清掉
    arr = (INPUT * 2)(_key(0x08), _key(0x08, up=True))
    ctypes.windll.user32.SendInput(2, arr, ctypes.sizeof(INPUT))
    g.close()


if __name__ == "__main__":
    main()
