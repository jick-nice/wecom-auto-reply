# -*- coding: utf-8 -*-
"""用 adb 驱动手机上的企业微信，往测试群里发一条假派单。

用法：
    python tools/phone_dispatch.py "ZhengRongFu2"
    python tools/phone_dispatch.py            # 默认发 ZhengRongFu2

流程：关掉可能的弹窗 → 进群 → 点输入框 → 输入 → 点发送 → 回读确认。
只操作手机，不碰 PC 上的企业微信。
"""
from __future__ import annotations

import re
import subprocess
import sys
import time

ADB = r"D:\Android\Sdk\platform-tools\adb.exe"
GROUP = "森焱科技工作室"


def sh(*args, timeout=25):
    r = subprocess.run([ADB, "shell", *args], capture_output=True, timeout=timeout)
    return r.stdout.decode("utf-8", "ignore")


def dump() -> str:
    sh("uiautomator", "dump", "/sdcard/u.xml")
    return sh("cat", "/sdcard/u.xml")


def nodes(xml: str):
    """返回 [(text, cx, cy)]"""
    out = []
    for m in re.finditer(r'text="([^"]{0,40})"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
        t = m.group(1)
        x1, y1, x2, y2 = map(int, m.groups()[1:])
        if t.strip():
            out.append((t, (x1 + x2) // 2, (y1 + y2) // 2))
    return out


def tap(x, y):
    sh("input", "tap", str(x), str(y))


def main(text="ZhengRongFu2"):
    xml = dump()
    ns = nodes(xml)

    # 0) 掉线 / 弹窗
    for t, x, y in ns:
        if "登录态已过期" in t or "重新登录" in t:
            print("❌ 手机企业微信登录态已过期，请在手机上重新登录后再跑")
            for t2, x2, y2 in ns:
                if t2.strip() == "我知道了":
                    tap(x2, y2)
            return 3
    dismissed = False
    for t, x, y in ns:
        if t.strip() == "我知道了":
            print("→ 关掉弹窗")
            tap(x, y)
            time.sleep(0.6)
            dismissed = True
            break
    if dismissed:
        xml = dump()
        ns = nodes(xml)

    # 1) 不在群里就点进去
    if not any("发消息或按住" in t for t, x, y in ns):
        hit = [n for n in ns if GROUP in n[0]]
        if not hit:
            print("❌ 手机上没找到群", GROUP)
            print("   当前界面文字：", [t for t, x, y in ns][:12])
            return 4
        print("→ 进入群", hit[0][0])
        tap(hit[0][1], hit[0][2])
        time.sleep(1.2)
        xml = dump()
        ns = nodes(xml)

    # 2) 点输入框
    box = [n for n in ns if "发消息或按住" in n[0] or "发送消息" in n[0]]
    if not box:
        print("❌ 找不到输入框，界面文字：", [t for t, x, y in ns][:12])
        return 5
    bx, by = box[0][1], box[0][2]
    print("→ 点输入框", (bx, by))
    tap(bx, by)
    time.sleep(1.0)

    # 3) 输入
    print("→ 输入", text)
    sh("input", "text", text)
    time.sleep(0.8)

    # 4) 找发送按钮（键盘弹起后位置会变，所以每次重新 dump）
    xml = dump()
    ns = nodes(xml)
    btn = [n for n in ns if n[0].strip() in ("发送", "Send")]
    if not btn:
        # 键盘上可能叫"发送"，也可能是个 content-desc
        m = re.search(r'content-desc="发送"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            btn = [("发送", (x1 + x2) // 2, (y1 + y2) // 2)]
    if not btn:
        print("   没找到发送按钮，尝试回车发送")
        sh("input", "keyevent", "66")
        time.sleep(1.0)
    else:
        print("→ 点发送", (btn[0][1], btn[0][2]))
        tap(btn[0][1], btn[0][2])
        time.sleep(1.2)

    # 5) 回读确认
    xml = dump()
    ns = nodes(xml)
    got = [t for t, x, y in ns if text.lower() in t.lower()]
    print("→ 回读：", "✅ 消息已发出" if got else "⚠️ 没在界面上读到刚发的内容")
    return 0 if got else 6


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "ZhengRongFu2"))
