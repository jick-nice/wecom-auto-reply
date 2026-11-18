# -*- coding: utf-8 -*-
"""手机发一条消息：进输入框打字 → 自动找蓝色「发送」→ motionevent 点击。

注意：企业微信会丢弃 `input tap`，但 **`input motionevent DOWN/UP` 能过**（实测）。
"""
from __future__ import annotations

import io
import subprocess
import sys
import time

import numpy as np
from PIL import Image

ADB = r"D:\Android\Sdk\platform-tools\adb.exe"
ART = r"C:\Users\21520\Downloads\企业微信自动回复\_artifacts"


def sh(*a):
    return subprocess.run([ADB, "shell", *a], capture_output=True, timeout=30).stdout


def shot(name="ps"):
    raw = subprocess.run([ADB, "exec-out", "screencap", "-p"],
                         capture_output=True, timeout=30).stdout
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    try:
        im.save(f"{ART}\\{name}.png")
    except Exception:
        pass
    return np.array(im)


def tap(x, y):
    """用 motionevent 点（企业微信只认这个）。"""
    sh("input", "motionevent", "DOWN", str(x), str(y))
    time.sleep(0.05)
    sh("input", "motionevent", "UP", str(x), str(y))


def blue_button(img):
    """找那个大的蓝色「发送」块：取最大连通块，别被键盘上的小蓝点骗了。"""
    a = img.astype(int)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    m = (b > 170) & (b - r > 55) & (b - g > 35) & (g > 80)
    if m.sum() < 1500:
        return None
    # 4 倍降采样后做连通块，够快也够准
    small = m[::4, ::4]
    seen = np.zeros_like(small)
    best, best_area = None, 0
    H, W = small.shape
    for y0 in range(0, H, 3):
        for x0 in range(0, W, 3):
            if not small[y0, x0] or seen[y0, x0]:
                continue
            stack = [(y0, x0)]
            seen[y0, x0] = True
            ys, xs = [], []
            while stack:
                y, x = stack.pop()
                ys.append(y)
                xs.append(x)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < H and 0 <= nx < W and small[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            stack.append((ny, nx))
            area = len(ys)
            if area > best_area:
                best_area = area
                best = (int(np.mean(xs)) * 4, int(np.mean(ys)) * 4, area)
    if not best or best[2] < 120:          # 太小 = 不是按钮
        return None
    return best[0], best[1]


def say(text: str, shot_prefix="say") -> bool:
    """发一条：先探键盘状态 → 点输入框 → 清空 → 打字 → 点最大蓝块。"""
    btn = blue_button(shot(f"{shot_prefix}_pre"))
    kb = bool(btn and btn[1] < 1600)          # 蓝色块在上面 = 键盘已经弹起
    iy = 1073 if kb else 2255
    tap(483, iy)
    time.sleep(0.35)
    sh("input", "keyevent", "KEYCODE_MOVE_END")
    sh("input", "keyevent", *(["67"] * 24))   # 一路退格清空
    time.sleep(0.2)
    sh("input", "text", text)
    time.sleep(0.6)
    img = shot(f"{shot_prefix}_typed")
    btn = blue_button(img)
    if not btn:
        print(f"  ❌ 没找到发送键（{text}）")
        return False
    tap(*btn)
    time.sleep(1.0)
    after = shot(f"{shot_prefix}_sent")
    ok = blue_button(after) is None or blue_button(after)[1] != btn[1]
    print(f"  {'✅' if ok else '❌'} 发了 {text!r}（发送键 {btn}）")
    return ok


if __name__ == "__main__":
    say(sys.argv[1] if len(sys.argv) > 1 else "3")
