# -*- coding: utf-8 -*-
"""在手机上点「发送」：从截图里**自动找那个蓝色按钮**，不用猜坐标。

流程：进群 → 确保输入框有内容 → 找蓝色发送键 → 点 → 回读确认。
"""
from __future__ import annotations

import subprocess
import sys
import time

import numpy as np
from PIL import Image

ADB = r"D:\Android\Sdk\platform-tools\adb.exe"
ART = r"C:\Users\21520\Downloads\企业微信自动回复\_artifacts"


def sh(*a, timeout=30):
    return subprocess.run([ADB, "shell", *a], capture_output=True, timeout=timeout).stdout


def shot(name="p_shot"):
    """直接走 exec-out 拿 PNG 字节流，避开中文路径在 adb pull 上的编码问题。"""
    import io

    raw = subprocess.run([ADB, "exec-out", "screencap", "-p"],
                         capture_output=True, timeout=30).stdout
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    try:
        img.save(f"{ART}\\{name}.png")
    except Exception:
        pass
    return np.array(img)


def find_blue_btn(img):
    """找底部那一块微信蓝的实心按钮。"""
    h, w = img.shape[:2]
    top = int(h * 0.55)
    bottom = img[top:, :, :].astype(int)
    r, g, b = bottom[:, :, 0], bottom[:, :, 1], bottom[:, :, 2]
    mask = (b > 180) & (b - r > 60) & (b - g > 40) & (g > 90)
    if mask.sum() < 500:
        return None
    ys, xs = np.nonzero(mask)
    y0, y1 = int(ys.min() + top), int(ys.max() + top)
    x0, x1 = int(xs.min()), int(xs.max())
    return (x0 + x1) // 2, (y0 + y1) // 2, (x0, y0, x1, y1), int(mask.sum())


def main():
    img = shot("p_now")
    print("屏幕:", img.shape)
    found = find_blue_btn(img)
    if not found:
        print("❌ 没找到蓝色发送键（可能键盘挡住了/不在聊天页）")
        return 2
    cx, cy, box, n = found
    print(f"蓝色按钮 bbox={box} 中心=({cx},{cy}) 像素数={n}")
    sh("input", "tap", str(cx), str(cy))
    time.sleep(1.0)
    after = shot("p_after")
    # 发送成功后输入框里的字会消失：比较蓝色按钮是否还在
    still = find_blue_btn(after)
    print("点击后蓝色按钮:", "还在（可能没发出）" if still else "已消失（消息已发出）")
    return 0 if not still else 3


if __name__ == "__main__":
    sys.exit(main())
