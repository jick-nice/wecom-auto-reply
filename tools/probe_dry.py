# -*- coding: utf-8 -*-
"""干跑校验（不发消息）：清空输入框 → 读数 → 打一个数字 → 再读数 → 比较 → 退格清掉。

用来确认"输入框探针"能正确判断字有没有进去，全程不按回车、不进群。
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate                                             # noqa: E402
from app.capture import (Grabber, client_rect_screen, force_foreground,  # noqa: E402
                         pick_wecom_window, restore, set_dpi_aware)
from app.engine import Engine                                      # noqa: E402
from app.ocr import Ocr                                            # noqa: E402
from app.sender import INPUT, Sender, _key                         # noqa: E402

u32 = ctypes.windll.user32


def tap_key(vk, n=1):
    ev = []
    for _ in range(n):
        ev += [_key(vk), _key(vk, up=True)]
    arr = (INPUT * len(ev))(*ev)
    u32.SendInput(len(ev), arr, ctypes.sizeof(INPUT))


def main():
    set_dpi_aware()
    hwnd = pick_wecom_window()
    restore(hwnd)
    force_foreground(hwnd)
    time.sleep(0.4)
    o = Ocr()
    o.warmup()
    g = Grabber()
    full = np.array(g.grab_window_client(hwnd, copy=True))
    area = locate.locate(full)
    ox, oy, cw, ch = client_rect_screen(hwnd)
    eng = Engine({"dispatchers": ["x"], "my_name": "杨煜"}, o, g, Sender((0, 0)),
                 log=lambda m: None)
    eng.hwnd = hwnd
    eng.area = area
    eng._origin = (ox, oy, cw, ch)
    read = eng._input_reader()

    tap_key(0x08, 6)                       # 清空
    time.sleep(0.25)
    empty = read()
    print("空框读数:", empty)

    tap_key(0x36)                          # 打一个 6
    time.sleep(0.3)
    one = read()
    print("打一个数字后:", one)

    tap_key(0x08, 3)                       # 清掉
    time.sleep(0.25)
    back = read()
    print("清掉后:", back)

    ok = empty is not None and one is not None and (one - empty) >= 12
    print("判定:", "✅ 探针可用（增长", (one - empty) if (empty is not None and one is not None) else "?", "）"
          if ok else "❌ 探针不可用")
    print("清空后回到:", back, "（应接近空框读数）")
    g.close()
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
