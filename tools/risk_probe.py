# -*- coding: utf-8 -*-
"""风控差分实验：只抓屏，不发任何按键。看会不会再弹「设备环境异常」。

监控信号：
  · 窗口句柄变了（出现安全验证页时企业微信会换主窗口）
  · locate() 连续两次找不到输入框结构（安全验证页没有聊天结构）
  · 每次抓屏的耗时/指纹

用法：python tools/risk_probe.py [秒数] [printwindow|bitblt]
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import locate                                    # noqa: E402
from app.capture import (Grabber, client_rect_screen, find_wecom_windows,  # noqa: E402
                         pick_wecom_window, set_dpi_aware)

user32 = ctypes.WinDLL("user32", use_last_error=True)


def main(seconds=180.0, mode="printwindow"):
    set_dpi_aware()
    g = Grabber()
    print(f"模式={mode}  预计 {seconds:.0f}s，全程不产生任何键盘/鼠标输入事件")
    print("开始时间", time.strftime("%H:%M:%S"))

    hwnd0 = pick_wecom_window()
    if not hwnd0:
        print("❌ 没有企业微信窗口")
        return 1
    print("初始 hwnd =", hwnd0, "rect =", client_rect_screen(hwnd0))

    t_end = time.time() + seconds
    fails = 0
    n = 0
    last_report = 0.0
    cost_sum = 0.0
    while time.time() < t_end:
        n += 1
        hwnd = pick_wecom_window()
        if hwnd != hwnd0:
            print(f"\n🚨 [{time.strftime('%H:%M:%S')}] 窗口句柄变了：{hwnd0} -> {hwnd}"
                  f"（极可能弹了安全验证页）")
            return 2
        try:
            t = time.perf_counter()
            if mode == "printwindow":
                f = np.array(g.grab_window_client(hwnd, copy=True))
            else:
                rc = client_rect_screen(hwnd)
                f = np.array(g.grab((rc[0], rc[1], rc[0] + rc[2], rc[1] + rc[3]), copy=True))
            cost = (time.perf_counter() - t) * 1000
            cost_sum += cost
            area = locate.locate(f)
            if not area.found:
                fails += 1
                print(f"  ⚠️ 定位失败 {fails} 次")
                if fails >= 3:
                    print("\n🚨 连续定位失败，可能已进入安全验证页")
                    return 2
            else:
                fails = 0
        except Exception as e:
            print("  抓屏异常:", e)
            time.sleep(1)
            continue

        if time.time() - last_report > 30:
            last_report = time.time()
            left = t_end - time.time()
            print(f"  [{n:4d} 帧] 平均 {cost_sum/n:.1f}ms  剩余 {left:.0f}s  "
                  f"area={area.rect} found={area.found}")
        time.sleep(1.0)

    print(f"\n✅ 完成 {n} 帧，全程无输入事件，没有出现安全验证页")
    print(f"   平均抓屏+定位 {cost_sum/n:.1f}ms")
    g.close()
    return 0


if __name__ == "__main__":
    sec = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
    md = sys.argv[2] if len(sys.argv) > 2 else "printwindow"
    sys.exit(main(sec, md))
