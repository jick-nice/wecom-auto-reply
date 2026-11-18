# -*- coding: utf-8 -*-
"""校验式发送单测：先在真机上确认"输入框校验"这条路通，再跑正式端到端。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate, segment                                   # noqa: E402
from app.capture import (Grabber, client_rect_screen, force_foreground,  # noqa: E402
                         pick_wecom_window, restore, set_dpi_aware)
from app.engine import Engine                                     # noqa: E402
from app.ocr import Ocr                                           # noqa: E402
from app.sender import Sender                                     # noqa: E402


def main(code="4"):
    set_dpi_aware()
    hwnd = pick_wecom_window()
    restore(hwnd)
    force_foreground(hwnd)
    time.sleep(0.3)
    o = Ocr()
    o.warmup()
    g = Grabber()
    full = np.array(g.grab_window_client(hwnd, copy=True))
    area = locate.locate(full)
    ox, oy, cw, ch = client_rect_screen(hwnd)
    eng = Engine({"dispatchers": ["x"], "my_name": "测试用户F"}, o, g, Sender((0, 0)),
                 log=lambda m: None)
    eng.hwnd = hwnd
    eng.area = area
    eng._origin = (ox, oy, cw, ch)
    s = Sender((ox + area.input_center[0], oy + area.input_center[1]))
    read = eng._input_reader()
    # 先把输入框里可能残留的东西清掉（最多 6 个退格）
    import ctypes
    from app.sender import INPUT, _key
    arr = (INPUT * 12)(*([_key(0x08), _key(0x08, up=True)] * 6))
    ctypes.windll.user32.SendInput(12, arr, ctypes.sizeof(INPUT))
    time.sleep(0.2)
    print("输入框文字区读数（应为空/很小）:", repr(read()))
    t = time.perf_counter()
    try:
        cost = s.send(code, hwnd=hwnd, read_input=read)
        print(f"✅ 已发送，{cost:.1f}ms，点击 {s.clicks} 次")
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}  点击 {s.clicks} 次")
    print(f"总耗时 {(time.perf_counter()-t)*1000:.1f}ms")
    time.sleep(0.8)
    full = np.array(g.grab_window_client(hwnd, copy=True))
    Image.fromarray(np.ascontiguousarray(full[:, :, ::-1])).save(ROOT / "_artifacts" / "verify2.png")
    crop = full[area.y1:area.y2, area.x1:area.x2]
    lay = segment.Layout()
    rows = segment.analyze(crop, lay)
    lay.learn(rows)
    rows = segment.analyze(crop, lay)
    print("--- 最后几条 ---")
    for m in segment.pair_messages(rows)[-4:]:
        who = o.read_name(segment.crop(crop, m.sender_row, pad_x=3, pad_y=6)) if m.sender_row else "(我)"
        print(f"   [{who}] {o.read_row(segment.crop(crop, m.body_row, pad_x=2, pad_y=2))!r}")
    g.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "4")
