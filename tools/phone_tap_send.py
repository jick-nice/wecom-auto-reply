# -*- coding: utf-8 -*-
"""进群 + 点发送：蓝色按钮只在聊天页 y∈[1400,2100] 这个区间找，避开底部标签栏。"""
from __future__ import annotations

import io
import subprocess
import sys
import time

import numpy as np
from PIL import Image

ADB = r"D:\Android\Sdk\platform-tools\adb.exe"
ART = r"C:\Users\21520\Downloads\企业微信自动回复\_artifacts"


def shot(name="ps"):
    raw = subprocess.run([ADB, "exec-out", "screencap", "-p"],
                         capture_output=True, timeout=30).stdout
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    try:
        img.save(f"{ART}\\{name}.png")
    except Exception:
        pass
    return np.array(img)


def blue_in_chat(img):
    h, w = img.shape[:2]
    y0, y1 = 1400, min(2100, h)
    seg = img[y0:y1, :, :].astype(int)
    r, g, b = seg[:, :, 0], seg[:, :, 1], seg[:, :, 2]
    mask = (b > 180) & (b - r > 60) & (b - g > 40) & (g > 90)
    if mask.sum() < 800:
        return None
    ys, xs = np.nonzero(mask)
    return (int(xs.min()) + int(xs.max())) // 2, (int(ys.min()) + y0 + int(ys.max()) + y0) // 2, int(mask.sum())


def main():
    img = shot("ps0")
    btn = blue_in_chat(img)
    if not btn:
        print("不在聊天页，尝试进群…")
        for y in (348, 330, 370, 300, 390, 420):
            subprocess.run([ADB, "shell", "input", "tap", "540", str(y)],
                           capture_output=True, timeout=20)
            time.sleep(1.4)
            img = shot(f"ps_try{y}")
            btn = blue_in_chat(img)
            print(f"  tap(540,{y}) -> {'找到发送键' if btn else '还没进去'}")
            if btn:
                break
    if not btn:
        print("❌ 进不去聊天页 / 没有发送键")
        return 2

    cx, cy, n = btn
    print(f"发送键中心=({cx},{cy}) 蓝色像素={n}")
    subprocess.run([ADB, "shell", "input", "tap", str(cx), str(cy)],
                   capture_output=True, timeout=20)
    time.sleep(1.2)
    after = shot("ps_after")
    still = blue_in_chat(after)
    print("发送后:", "按钮还在（可能没发出）" if still else "按钮消失 → 消息已发出")
    return 0 if not still else 3


if __name__ == "__main__":
    sys.exit(main())
