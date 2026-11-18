# -*- coding: utf-8 -*-
"""真机性能与准确率检查：抓真窗口 → 定位 → 切行 → OCR，逐项计时。"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import segment                                   # noqa: E402
from app.capture import Grabber, pick_wecom_window, set_dpi_aware  # noqa: E402
from app.engine import Engine                             # noqa: E402
from app.ocr import Ocr                                   # noqa: E402


class NullSender:
    def __init__(self):
        self.input_pos = (0, 0)
        self.sent = []

    def send(self, text, force_click=False):
        self.sent.append(text)
        return 9.9


def main():
    set_dpi_aware()
    hwnd = pick_wecom_window()
    if not hwnd:
        print("没找到企业微信窗口")
        return 1
    g = Grabber()
    o = Ocr()
    o.warmup()
    logs = []
    cfg = {"dispatchers": ["悲伤土豆", "朱洋"], "my_name": "王琦",
           "dark_thr": 150, "max_orders": 3, "orders_today": 0}
    eng = Engine(cfg, o, g, NullSender(), log=logs.append)
    eng.ensure_area(force=True)
    print(f"定位: {eng.area}")

    print("\n--- 抓帧 ---")
    for i in range(5):
        t = time.perf_counter()
        f = eng._grab_frame()
        print(f"  #{i} {(time.perf_counter() - t) * 1000:6.2f}ms  {eng.grab_mode}  {f.shape}")

    print("\n--- 同一帧重复处理（缓存命中路径）---")
    for i in range(6):
        t = time.perf_counter()
        eng._handle_frame(f)
        print(f"  #{i} {(time.perf_counter() - t) * 1000:6.2f}ms  ocr={eng.stats['ocr']} cache={len(o._cache)}")

    print("\n--- 识别内容 ---")
    lay = segment.Layout()
    rows = segment.analyze(f, lay)
    lay.learn(rows)
    rows = segment.analyze(f, lay)
    print(f"  锚点 name_x={lay.name_x} content_x={lay.content_x}  行={len(rows)}")
    for m in segment.pair_messages(rows):
        s = o.line(segment.crop(f, m.sender_row)) if m.sender_row else "(我)"
        b = o.read_row(segment.crop(f, m.body_row))
        print(f"  [{s}] {b!r}")

    print("\n--- 冷启动一次完整判定（模拟新派单）---")
    o._cache.clear()
    t = time.perf_counter()
    eng._handle_frame(f)
    print(f"  含 2 次冷 OCR 全流程 {(time.perf_counter() - t) * 1000:.1f}ms")
    for line in logs:
        print(" ", line)
    g.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
