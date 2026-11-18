# -*- coding: utf-8 -*-
"""验证自动定位：在截图/真机窗口上跑，并画框保存，肉眼可查"""
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app.locate import locate      # noqa: E402
from app import segment            # noqa: E402


def report(rgb, tag):
    h, w = rgb.shape[:2]
    t0 = time.perf_counter()
    a = locate(rgb)
    ms = (time.perf_counter() - t0) * 1000
    print(f"\n[{tag}] {w}x{h}  定位耗时 {ms:.1f}ms")
    print("  ", a)
    if not a.found:
        print("   ⚠️ 没定位到（走比例兜底）")
        return None

    crop = rgb[a.y1:a.y2, a.x1:a.x2]
    lay = segment.Layout()
    rows = segment.analyze(crop, lay)
    lay.learn(rows)
    rows = segment.analyze(crop, lay)
    msgs = segment.pair_messages(rows)
    print(f"   行={len(rows)} 消息={len(msgs)} 锚点 name_x={lay.name_x} content_x={lay.content_x}")
    for m in msgs[-5:]:
        print(f"     名字行 y={m.sender_row.y1 if m.sender_row else -1:4} "
              f"内容行 y={m.body_row.y1:4} x={m.body_row.x1}")

    out = Image.fromarray(np.ascontiguousarray(rgb[:, :, ::-1])).convert("RGB")
    d = ImageDraw.Draw(out)
    d.rectangle([a.x1, a.y1, a.x2, a.y2], outline=(255, 0, 0), width=2)
    d.rectangle([a.input_x1, a.input_y1, a.input_x2, a.input_y2], outline=(0, 128, 255), width=2)
    cx, cy = a.input_center
    d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=(0, 200, 0))
    outdir = ROOT / "_artifacts"
    outdir.mkdir(exist_ok=True)
    p = outdir / f"overlay_{tag}.png"
    out.save(p)
    print("   框选结果图:", p)
    return a


def main():
    for name in sys.argv[1:] or ["live_win.png", "s3_clean.png"]:
        img = ROOT / "samples" / name
        if not img.exists():
            continue
        report(np.array(Image.open(img).convert("RGB")), Path(name).stem)


if __name__ == "__main__":
    main()
