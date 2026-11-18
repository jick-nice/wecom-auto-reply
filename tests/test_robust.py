# -*- coding: utf-8 -*-
"""全方位鲁棒性测试：显示设置在变、干扰消息在变，识别必须还准。

三块：
  A. 口令解析：大写 / 全角 / 中文数字 / keycap emoji / 圈码 + 一堆"看着像其实不是"的干扰项
  B. 视觉：对真实截图做 缩放 / 亮度 / 对比度 / JPEG / 模糊 / 反色（深色模式）变换
  C. 逻辑：非派单人的闲聊、贴纸、系统消息、@消息 不能触发回复

跑法：python tests\\test_robust.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "client"))

from app import segment                                   # noqa: E402
from app.numparse import reply_code                       # noqa: E402
from app.ocr import Ocr                                   # noqa: E402

PASS = 0
FAIL = []
o = Ocr()


def check(name, got, want):
    global PASS
    if got == want:
        PASS += 1
    else:
        FAIL.append(f"{name}: 期望 {want!r} 实际 {got!r}")


# ------------------------------------------------------------------ A 口令解析
# (文本, 期望数字)  期望 None = 不该被当成派单
CASES = [
    # 正常派单
    ("正荣府扣2", 2), ("正荣府2", 2), ("正荣府 2", 2), ("正荣府　2", 2),
    ("双街3", 3), ("宜居园 3️⃣", 3), ("双街新家园 看房叁", 3),
    ("89平 扣3", 3), ("3号楼 扣1", 1), ("正荣府扣伍", 5), ("正荣府扣９", 9),
    ("2️⃣", 2), ("③", 3), ("❷", 2), ("叁", 3), ("參", 3), ("叄", 3),
    ("正荣府2️⃣", 2), ("正荣府扣2️⃣", 2),
    ("ZHENGRONGFU2", 2), ("ZhengRongFu8", 8), ("A-3", 3), ("B3", 3),
    ("正荣府扣２", 2),                          # 全角数字
    ("正荣府扣 2 号楼", 2),
    ("宜居园扣1", 1), ("宜居园1", 1), ("双街 扣3", 3),
    ("正荣府扣2，手慢无", 2),
    ("【正荣府】扣2", 2),
    # 看着像、其实不是 —— 必须返回 None
    ("前三名", None), ("排名3", None), ("第3个", None), ("3天", None),
    ("3小时", None), ("3成", None), ("约3", None), ("1/3", None),
    ("2-3间", None), ("3栋", None), ("3室", None), ("3楼", None),
    ("89平", None), ("89平米", None), ("3万", None), ("扣1万", None),
    ("欢迎加入", None), ("收到", None), ("好的", None), ("在吗", None),
    ("😀", None), ("👍", None), ("", None),
    ("1234567", None), ("3.5", None), ("v2.1", None), ("2024年", None),
    ("9:30", None), ("3号楼", None), ("2单元", None),
    ("一共3个人", None), ("明天3点", None),
    # —— 加的这批：多数字混排 / 序号 / 单位 / 时段 ——
    ("89平 3号楼 扣2", 2),                     # 多个数字里抓「扣」那个
    ("正荣府 2室2厅 扣3", 3),
    ("正荣府3 89平 总价120万", 3),             # 房源号在前、后面全是干扰
    ("扣2 89平 3楼", 2),
    ("正荣府（二期）扣4", 4),                  # 全角括号
    ("正荣府：扣5", 5),                        # 全角冒号
    ("正荣府  扣  6", 6),                      # 多空格
    ("【急】正荣府 扣7 手慢无", 7),
    ("正荣府扣8（89平）", 8),
    ("3️⃣号楼", None),                          # keycap 后面跟"号楼"仍是房号
    ("2号线", None), ("3单元", None), ("4号楼", None),
    ("2024-06-01", None), ("13800138000", None),
    ("3~5间", None), ("3至5套", None),
    ("价格3万5", None), ("首付3成", None),
    ("三室两厅", None), ("一共三套", None),
    ("看房叁", 3), ("看房 叁", 3), ("定房贰", 2),
    ("正荣府扣2！", 2), ("正荣府扣③", 3),
]


def test_numparse():
    for text, want in CASES:
        got = reply_code(text)
        got = None if got is None else str(got)
        check(f"numparse[{text!r}]", got, None if want is None else str(want))


# ------------------------------------------------------------------ B 视觉变换
def load(name):
    return np.array(Image.open(ROOT / "samples" / name).convert("RGB"))


def read_chat(img):
    """跑一遍 定位→切行→OCR，返回 [(说话人, 文本)]。"""
    img = segment.normalize(img)
    lay = segment.Layout()
    rows = segment.analyze(img, lay, dark_thr=segment.auto_thr(img))
    if rows:
        lay.learn(rows)
        rows = segment.analyze(img, lay, dark_thr=segment.auto_thr(img))
    out = []
    for m in segment.pair_messages(rows):
        who = o.read_name(segment.crop(img, m.sender_row, pad_x=3, pad_y=6)) if m.sender_row else ""
        txt = o.read_row(segment.crop(img, m.body_row, pad_x=2, pad_y=2))
        out.append((who, txt))
    return out


def transform(img, kind, v):
    im = Image.fromarray(img)
    if kind == "scale":
        return np.array(im.resize((int(im.width * v), int(im.height * v)), Image.LANCZOS))
    if kind == "bright":
        return np.array(ImageEnhance.Brightness(im).enhance(v))
    if kind == "contrast":
        return np.array(ImageEnhance.Contrast(im).enhance(v))
    if kind == "jpeg":
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=v)
        return np.array(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))
    if kind == "blur":
        return np.array(im.filter(ImageFilter.GaussianBlur(v)))
    if kind == "invert":                     # 模拟深色模式/高对比度
        return 255 - np.array(im)
    raise ValueError(kind)


def has_dispatch(rows, name="朱洋", code="3"):
    for who, txt in rows:
        if name in (who or "") and str(reply_code(txt)) == str(code):
            return True
    return False


def test_vision():
    base = load("s3_clean.png")
    rows = read_chat(base)
    check("vision[原图] 找到 朱洋→3", has_dispatch(rows), True)
    print("   原图识别:", [(w[:6], t) for w, t in rows if w][:4])

    for kind, vals in [
        ("scale", [0.5, 0.6, 0.75, 0.9, 1.1, 1.25, 1.5, 2.0, 3.0]),
        ("bright", [0.6, 0.75, 0.85, 1.15, 1.3, 1.5]),
        ("contrast", [0.6, 0.75, 1.3, 1.6]),
        ("jpeg", [40, 70]),
        ("blur", [0.5, 1.0]),
        ("invert", [1]),
    ]:
        for v in vals:
            img = transform(base, kind, v)
            rows = read_chat(img)
            ok = has_dispatch(rows)
            check(f"vision[{kind}={v}]", ok, True)
            if not ok:
                print(f"   ✗ {kind}={v} 识别到:", [(w[:6], t) for w, t in rows][:5])


# ------------------------------------------------------------------ C 干扰消息
def build_noise_image():
    """合成一张"脏"聊天图：系统消息 + 别人闲聊 + 贴纸 + 图片 + 派单人的正常派单。"""
    from PIL import ImageDraw, ImageFont

    W, H = 900, 620
    im = Image.new("RGB", (W, H), (247, 247, 247))
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 15)
    small = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 12)

    def bubble(x, y, w, h, color=(255, 255, 255)):
        d.rounded_rectangle([x, y, x + w, y + h], radius=6, fill=color)

    t = "悲伤土豆 加入了群聊"
    d.text(((W - d.textlength(t, font=small)) / 2, 20), t, font=small, fill=(150, 150, 150))
    bubble(70, 70, 120, 34)
    d.text((60, 48), "于超凡", font=small, fill=(150, 150, 150))
    d.text((80, 78), "3", font=font, fill=(30, 30, 30))
    d.text((60, 140), "王琦", font=small, fill=(150, 150, 150))
    d.rounded_rectangle([70, 160, 250, 340], radius=10, fill=(255, 196, 0))
    d.ellipse([110, 200, 210, 300], fill=(255, 120, 60))
    d.text((60, 380), "于超凡", font=small, fill=(150, 150, 150))
    d.rectangle([70, 400, 330, 520], fill=(205, 210, 215))
    d.text((150, 450), "[图片]", font=font, fill=(120, 120, 120))
    d.text((60, 545), "朱洋", font=small, fill=(150, 150, 150))
    bubble(70, 565, 170, 36)
    d.text((82, 573), "宜居园3", font=font, fill=(30, 30, 30))
    return np.array(im)[:, :, ::-1]


def test_noise_rows():
    img = build_noise_image()
    rows = read_chat(img)
    print("   合成图识别:", [(w, t) for w, t in rows])
    got = [t for w, t in rows]
    check("noise 贴纸/图片没产生假口令",
          all(reply_code(t) is None or str(reply_code(t)) == "3" for t in got), True)
    check("noise 仍能找到派单人的 3", has_dispatch(rows), True)


def test_engine_negative():
    """引擎级：别人（非派单人）发的数字，一条都不能回；只有派单人那条回一次。"""
    from app.engine import Engine

    class FakeSender:
        def __init__(self):
            self.sent = []
            self.clicks = 0

        def send(self, text, hwnd=None, force_click=False, read_input=None):
            self.sent.append(text)
            return 5.0

    img = build_noise_image()
    lay = segment.Layout()
    rows = segment.analyze(img, lay, dark_thr=segment.auto_thr(img))
    lay.learn(rows)
    rows = segment.analyze(img, lay, dark_thr=segment.auto_thr(img))
    msgs = segment.pair_messages(rows)

    fs = FakeSender()
    eng = Engine({"dispatchers": ["朱洋"], "my_name": "我", "max_orders": 3},
                 o, None, fs, log=lambda m: None)
    for _ in range(3):                      # 模拟连续 3 帧
        for m in msgs:
            eng._judge(img, m)
    check("engine 只回派单人的号", fs.sent, ["3"])


def main():
    test_numparse()
    try:
        test_vision()
    except Exception as e:
        FAIL.append(f"vision 整体异常: {type(e).__name__}: {e}")
    try:
        test_noise_rows()
    except Exception as e:
        FAIL.append(f"noise 整体异常: {type(e).__name__}: {e}")
    try:
        test_engine_negative()
    except Exception as e:
        FAIL.append(f"engine 整体异常: {type(e).__name__}: {e}")

    print(f"\n通过 {PASS} 项，失败 {len(FAIL)} 项")
    for f in FAIL[:40]:
        print("  ✗", f)
    if len(FAIL) > 40:
        print(f"  ... 还有 {len(FAIL)-40} 项")
    return 0 if not FAIL else 2


if __name__ == "__main__":
    sys.exit(main())
