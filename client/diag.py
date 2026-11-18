# -*- coding: utf-8 -*-
"""一键自检：在任何机器上跑一次，把结果（含一张标注图）发回来即可定位问题。

用法（在 client 目录下）：
    python diag.py

会依次检查：
    1. Windows 版本 / DPI 感知模式（Win7 缩放导致的坐标错位就是这里出问题）
    2. 能不能找到企业微信窗口、客户区物理矩形
    3. 自动定位聊天区（两条路径对比：PrintWindow 后台渲染 vs 屏幕 BitBlt）
    4. 切行结果 + 每条消息的发送人和内容（真实 OCR）
    5. 保存一张红框标注图 diag_overlay.png
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from app import locate, segment                                    # noqa: E402
from app.capture import (Grabber, client_rect_screen, find_wecom_windows,  # noqa: E402
                         force_foreground, is_foreground, pick_wecom_window,
                         restore, set_dpi_aware, window_rect, windows_version)

OUT = []


def p(*a):
    line = " ".join(str(x) for x in a)
    OUT.append(line)
    print(line)


def main() -> int:
    p("=" * 62)
    p("抢单助手 环境自检")
    p("=" * 62)
    p("Python      :", sys.version.split()[0], "|", sys.executable)
    p("Windows     :", windows_version())
    mode = set_dpi_aware()
    p("DPI 感知    :", mode, "（必须是 per-monitor-v2 或 system-aware）")
    p("像素比检查  : 见下方客户区尺寸，若与肉眼看到的窗口大小差一倍即为缩放问题")

    ws = find_wecom_windows()
    p("\n找到窗口数  :", len(ws))
    for h, t, c in ws:
        p(f"   hwnd={h} class={c!r} title={t!r} rect={window_rect(h)}")

    hwnd = pick_wecom_window()
    if not hwnd:
        p("\n❌ 没有找到企业微信窗口。请先打开并登录企业微信，停在群聊天界面再跑一次。")
        _save()
        return 1
    p("\n选中窗口    :", hwnd)
    try:
        restore(hwnd)
        force_foreground(hwnd)
        time.sleep(0.5)
    except Exception as e:
        p("  前台切换失败:", e)
    rc = client_rect_screen(hwnd)
    p("客户区(物理):", rc, "  ← 若 x/y/w/h 与窗口实际不符，就是 DPI 问题")
    if not rc:
        _save()
        return 1

    g = Grabber()
    images = {}

    t = time.perf_counter()
    try:
        wp = np.array(g.grab_window_client(hwnd, copy=True))
        p(f"PrintWindow : OK {wp.shape[1]}x{wp.shape[0]}  {(time.perf_counter()-t)*1000:.1f}ms  (后台渲染)")
        images["printwindow"] = wp
    except Exception as e:
        p("PrintWindow : 失败 ->", e)

    t = time.perf_counter()
    try:
        sc = np.array(g.grab((rc[0], rc[1], rc[0] + rc[2], rc[1] + rc[3]), copy=True))
        p(f"屏幕 BitBlt : OK {sc.shape[1]}x{sc.shape[0]}  {(time.perf_counter()-t)*1000:.1f}ms  (需要窗口在前台)")
        images["screen"] = sc
    except Exception as e:
        p("屏幕 BitBlt : 失败 ->", e)

    if not images:
        p("\n❌ 两种抓屏都失败，无法继续。")
        _save()
        return 1

    p("\n--- 自动定位 ---")
    chosen = None
    for name, img in images.items():
        t = time.perf_counter()
        area = locate.locate(img)
        dt = (time.perf_counter() - t) * 1000
        p(f"  来源={name:12} {dt:5.1f}ms  {area}")
        if area.found and chosen is None:
            chosen = (name, img, area)
    if chosen is None:
        name, img = next(iter(images.items()))
        chosen = (name, img, locate.locate(img))
        p("  ⚠️ 两条路径都没定位到结构，已按比例兜底")

    name, img, area = chosen
    p(f"  采用: {name}  →  消息区 {area.rect}")

    p("\n--- 切行 + 识别 ---")
    from app.ocr import Ocr
    o = Ocr()
    o.warmup()
    crop = img[area.y1:area.y2, area.x1:area.x2]
    lay = segment.Layout()
    rows = segment.analyze(crop, lay)
    lay.learn(rows)
    rows = segment.analyze(crop, lay)
    t = time.perf_counter()
    rows2 = segment.analyze(crop, lay)
    p(f"  行数={len(rows2)}  切行耗时={(time.perf_counter()-t)*1000:.1f}ms"
      f"  锚点 name_x={lay.name_x} content_x={lay.content_x}")
    msgs = segment.pair_messages(rows2)
    p(f"  消息条数={len(msgs)}")
    for i, m in enumerate(msgs[-8:], 1):
        t = time.perf_counter()
        who = o.read_name(segment.crop(crop, m.sender_row, pad_x=3, pad_y=6)) if m.sender_row else "(我)"
        txt = o.read_row(segment.crop(crop, m.body_row, pad_x=2, pad_y=2))
        dt = (time.perf_counter() - t) * 1000
        p(f"   {i}. [{who}] {txt!r}   ({dt:.0f}ms)")

    # 标注图
    out = Image.fromarray(np.ascontiguousarray(img[:, :, ::-1])).convert("RGB")
    d = ImageDraw.Draw(out)
    d.rectangle([area.x1, area.y1, area.x2, area.y2], outline=(255, 0, 0), width=3)
    d.rectangle([area.input_x1, area.input_y1, area.input_x2, area.input_y2],
                outline=(0, 128, 255), width=2)
    for m in msgs:
        d.rectangle([area.x1 + m.body_row.x1 - 2, area.y1 + m.body_row.y1,
                     area.x1 + m.body_row.x2 + 2, area.y1 + m.body_row.y2],
                    outline=(0, 200, 0), width=1)
    d.text((8, 8), f"win {windows_version()} | {name} | area {area.rect}", fill=(255, 0, 0))
    outdir = HERE.parent / "_artifacts"
    outdir.mkdir(exist_ok=True)
    outpath = outdir / "diag_overlay.png"
    out.save(outpath)
    p(f"\n标注图已保存: {outpath}")
    p("把上面的文字 + 这张图发回来，就能判断是否需要调整。")
    g.close()
    _save()
    return 0


def _save():
    outdir = HERE.parent / "_artifacts"
    outdir.mkdir(exist_ok=True)
    path = outdir / "diag_result.txt"
    path.write_text("\n".join(OUT), encoding="utf-8")
    print(f"\n结果也已保存: {path}")


if __name__ == "__main__":
    sys.exit(main())
