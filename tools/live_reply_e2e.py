# -*- coding: utf-8 -*-
"""真机端到端：让一条**真实出现在聊天里的消息**走完整条链路。

做法：用同一个企业微信账号发出「Garden3」，但在测试里把"派单人匹配"临时改写成
把它当成派单人 —— 这样不依赖第二台设备，也能验证：
    画面 → 切行 → OCR → 命中派单人 → 解析口令 → 打字 → 回读校验 → 回车 → 群里出现回复
测试只改内存里的对象，不动 config.json、不动企业微信。
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import engine as engine_mod, locate, segment          # noqa: E402
from app.capture import (Grabber, client_rect_screen, force_foreground,  # noqa: E402
                         pick_wecom_window, restore, set_dpi_aware)
from app.engine import Engine                                   # noqa: E402
from app.ocr import Ocr                                         # noqa: E402
from app.sender import INPUT, Sender, _key                      # noqa: E402

ART = ROOT / "_artifacts"
u32 = ctypes.windll.user32


def tap_keys(seq):
    ev = []
    for vk in seq:
        ev += [_key(vk), _key(vk, up=True)]
    arr = (INPUT * len(ev))(*ev)
    u32.SendInput(len(ev), arr, ctypes.sizeof(INPUT))


def type_text(text):
    vk_map = {"G": 0x47, "a": 0x41, "r": 0x52, "d": 0x44, "e": 0x45, "n": 0x4E, "3": 0x33}
    tap_keys([vk_map[c] for c in text] + [0x0D])


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

    cfg = {
        "dispatchers": ["测试用户F", "测试用户A"],
        "self_dispatch": True,        # 测试用：允许把「本机账号发的」当派单（真机模拟派单）
        "my_name": "测试用户A",          # 装着我是别人，这样"测试用户F发的"才有意义
        "require_foreground": True,
        "allow_click": True,
        "dark_thr": 150,
    }
    s = Sender((ox + area.input_center[0], oy + area.input_center[1]))
    logs = []
    eng = Engine(cfg, o, g, s, log=lambda m: (logs.append(m), print("  [eng]", m)))

    # 测试专用：把自己的消息当成「测试用户F」派出来的（只在内存里改，不落盘）
    real_match = engine_mod.match_dispatcher
    engine_mod.match_dispatcher = lambda name, ds: "测试用户F" if not (name or "").strip() else real_match(name, ds)

    eng.start()
    t_ready = time.time()
    time.sleep(3.0)
    print(f"引擎已就绪（画幅 {eng.stats['frames']}，基线后等待派单）")

    print("→ 发一条「9」当派单（只发数字：中文输入法下字母会被吃成候选字）")
    eng.ensure_area(force=True, quiet=True)      # 窗口可能动过，重新定位一次
    ox, oy, cw, ch = client_rect_screen(hwnd)
    s.input_pos = (ox + eng.area.input_center[0], oy + eng.area.input_center[1])
    print(f"  输入框点击点 = {s.input_pos}  area = {eng.area}")
    force_foreground(hwnd)
    time.sleep(0.25)
    tap_keys([0x08] * 24)                 # 先清空输入框（可能残留上次打的字）
    time.sleep(0.2)
    t0 = time.perf_counter()
    try:
        s.send("9", hwnd=hwnd, read_input=eng._input_reader())
        print(f"  发出耗时 {(time.perf_counter()-t0)*1000:.0f}ms（点击 {s.clicks} 次）")
    except Exception as e:
        print(f"  ❌ 自己这条没发出去: {type(e).__name__}: {e}")

    deadline = time.time() + 12
    while time.time() < deadline and eng.stats["sent"] == 0:
        time.sleep(0.05)
    dt = (time.time() - t_ready) * 1000
    print(f"\n发送次数 = {eng.stats['sent']}  OCR = {eng.stats['ocr']}  点击 = {s.clicks}")
    eng.stop()
    time.sleep(0.5)

    frame = np.array(g.grab_window_client(hwnd, copy=True))
    Image.fromarray(np.ascontiguousarray(frame[:, :, ::-1])).save(ART / "e2e_reply_final.png")
    crop = frame[area.y1:area.y2, area.x1:area.x2]
    lay = segment.Layout()
    rows = segment.analyze(crop, lay)
    lay.learn(rows)
    rows = segment.analyze(crop, lay)
    print("--- 最后几条 ---")
    last = []
    for m in segment.pair_messages(rows)[-5:]:
        who = o.read_name(segment.crop(crop, m.sender_row, pad_x=3, pad_y=6)) if m.sender_row else "(我)"
        txt = o.read_row(segment.crop(crop, m.body_row, pad_x=2, pad_y=2))
        last.append((who, txt))
        print(f"   [{who}] {txt!r}")

    g.close()
    ok = eng.stats["sent"] >= 1 and sum(1 for _, t in last if t.strip() == "9") >= 2
    print("\n结果:", "✅ 全链路通过（派单→识别→回复 3 进群）" if ok else "❌ 未通过")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
