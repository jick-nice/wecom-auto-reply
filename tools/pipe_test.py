# -*- coding: utf-8 -*-
"""离线管线验证：用真实截图跑「切行 → rec-only OCR → 判号码」全流程"""
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import segment                     # noqa: E402
from app.numparse import reply_code         # noqa: E402
from app.ocr import Ocr                     # noqa: E402

IMG = ROOT / "samples" / ("s3_clean.png" if len(sys.argv) < 2 else sys.argv[1])


def main() -> None:
    rgb = np.array(Image.open(IMG).convert("RGB"))
    h, w = rgb.shape[:2]
    print(f"image {IMG.name} {w}x{h}")

    ocr = Ocr()
    ocr.warmup()

    # 第一遍：学锚点
    lay = segment.Layout()
    rows = segment.analyze(rgb, lay)
    lay.learn(rows)
    rows = segment.analyze(rgb, lay)
    msgs = segment.pair_messages(rows)
    print(f"锚点 name_x={lay.name_x} content_x={lay.content_x}  行={len(rows)} 消息={len(msgs)}")

    for m in msgs:
        t0 = time.perf_counter()
        who = ocr.line(segment.crop(rgb, m.sender_row)) if m.sender_row else "(我/无名字行)"
        body = ocr.read_row(segment.crop(rgb, m.body_row))
        ms = (time.perf_counter() - t0) * 1000
        print(f"  y={m.body_row.y1:4d}  [{who}]  {body!r:32}  号码={reply_code(body)!s:5} {ms:5.1f}ms")

    # 第二遍（缓存命中）→ 稳态耗时
    t0 = time.perf_counter()
    rows = segment.analyze(rgb, lay)
    msgs = segment.pair_messages(rows)
    ta = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    for m in msgs:
        if m.sender_row:
            ocr.line(segment.crop(rgb, m.sender_row))
        ocr.read_row(segment.crop(rgb, m.body_row))
    tb = (time.perf_counter() - t0) * 1000
    print(f"\n稳态：切行 {ta:.1f}ms  全部 OCR(缓存) {tb:.1f}ms")
    print(f"只看最新一条的预估：切行 {ta:.1f}ms + 2 次 rec ≈ 10ms + 发送 10ms")


if __name__ == "__main__":
    main()
