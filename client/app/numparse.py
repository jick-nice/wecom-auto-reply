# -*- coding: utf-8 -*-
"""
numparse.py — 抢单号码识别核心（抗干扰版）

设计目标：
  1. 把各种"人为干扰"的写法归一化成 ASCII 数字
     3 / ３ / ③ / 3️⃣ / 叁 / 三 / 參 / 扣2 / 看房叁 …
  2. 从一条派单消息里挑出"唯一该回复的号码"，而不是见数字就回
  3. 纯逻辑、零依赖、可单测 —— 与 OCR 解耦，OCR 换引擎不影响它

优先级（分数越高越优先）：
  扣X          100   派单人明确要求
  X 紧贴房源名   70    "测试项目C3"、"测试项目 3️⃣" 这种写法
  花式写法       55    ③ 3️⃣ 叁 三 —— 用花式本身就是为了防止被人肉抢
  普通数字       30
  明显的干扰项   -100  面积/价格/楼层/房号/时间/手机号/年份
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------- 字符映射表

CN_SMALL = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
# 大写 / 繁体 / 异体：叁=三，贰=二，参=三，陆=六，柒=七
CN_BIG = {
    "壹": 1, "贰": 2, "貳": 2, "弍": 2, "叁": 3, "參": 3, "叄": 3, "弎": 3,
    "肆": 4, "伍": 5, "陆": 6, "陸": 6, "柒": 7, "捌": 8, "玖": 9,
    "拾": 10, "什": 10,
}
# 圈号 / 黑圈 / 方框 / 带点等花式阿拉伯数字
CIRCLED = {
    "⓪": 0, "①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5, "⑥": 6, "⑦": 7, "⑧": 8, "⑨": 9,
    "⓵": 1, "⓶": 2, "⓷": 3, "⓸": 4, "⓹": 5, "⓺": 6, "⓻": 7, "⓼": 8, "⓽": 9,
    "❶": 1, "❷": 2, "❸": 3, "❹": 4, "❺": 5, "❻": 6, "❼": 7, "❽": 8, "❾": 9,
    "➀": 1, "➁": 2, "➂": 3, "➃": 4, "➄": 5, "➅": 6, "➆": 7, "➇": 8, "➈": 9,
    "㊀": 1, "㊁": 2, "㊂": 3, "㊃": 4, "㊄": 5, "㊅": 6, "㊆": 7, "㊇": 8, "㊈": 9,
    "⑴": 1, "⑵": 2, "⑶": 3, "⑷": 4, "⑸": 5, "⑹": 6, "⑺": 7, "⑻": 8, "⑼": 9,
}
# OCR 长把 keycap emoji 读成这些残渣，先干掉再解析
_JUNK = dict.fromkeys(map(ord, "\ufe0f\u20e3\u200b\u200d\u00a0"), None)

# 干扰项：数字后面跟着这些单位，基本不可能是答案
NOISE_AFTER = re.compile(
    r"^\s*(平|㎡|m2|m²|平米|平方|万|元|块|层|楼|号楼|栋|幢|室|号|期|年|月|日|点|分|%|％|k|w|千|百|米|公里|"
    r"分钟|小时|时|天|周|套|间|卫|厅|居|房|折|折后|单价|总价|首付|月供|面积|"
    r"成|岁|个|人|名|位|次|遍|张|件|条|份|盒|箱|包|瓶|斤|克|升|度|单元|层高|共)"
)
# 干扰项：数字前面是这些符号或"排名/序数"词
NOISE_BEFORE = re.compile(
    r"[¥￥$]\s*$|第\s*$|约\s*$|前\s*$|共\s*$|排名\s*$|前\s*\S{0,2}\s*$|[/:：.．]\s*$"
    r"|[万千百元块]\s*$"                     # 「3万5」里的 5 属于同一个价格，不是口令
)
# 只有"数字 + 连字符 + 数字"才算区间（2-3）；「A-3」「B-3」是房源编号，要认
NOISE_RANGE_BEFORE = re.compile(r"\d\s*[\-~—–至到]\s*$")
# 「扣X」后面跟这些量词才否定掉（扣1万/扣2元），楼栋号之类的照认
KOU_BAD_UNIT = re.compile(r"^\s*(万|万元|元|块|千|百|平|㎡|平米|平方|折|%|％)")
# 区间 / 时刻 / 小数：3-5 间、9:30、3.5、1/3
NOISE_RANGE = re.compile(r"^\s*[\-~—–至到:：.．/]\s*\d")
# 排名 / 序数语境：前三、第一、名次3
NOISE_RANK = re.compile(r"前$|第$|排名|名次|共$")

_KOU_RE = re.compile(
    r"扣\s*([0-9]{1,3}|[零一二两三四五六七八九十壹贰貳叁參叄肆伍陆陸柒捌玖拾]{1,3})"
)

_HAN_RE = re.compile(r"[\u4e00-\u9fff]")

# 切行时时间分割线（"上午2:52"）偶尔会和下面的气泡并成一行，先剥掉
_TIME_PREFIX = re.compile(
    r"^\s*(?:上午|下午|凌晨|中午|晚上|昨天|前天|今天|星期[一二三四五六日天]|周[一二三四五六日天])?"
    r"\s*\d{1,2}[:：]\d{1,2}\s*"
)


@dataclass
class Cand:
    value: int
    start: int
    end: int
    kind: str     # kou | adj | fancy | plain
    raw: str
    score: int = 0

    def __repr__(self) -> str:
        return f"<{self.raw}→{self.value} {self.kind} {self.score}>"


def normalize(text: str) -> str:
    """全角→半角、去 emoji 残渣、去零宽字符、去掉混进来的时间分割线。"""
    if not text:
        return ""
    text = text.translate(_JUNK)
    text = unicodedata.normalize("NFKC", text)          # ３→3 ％→% ㎡ 保持
    return _TIME_PREFIX.sub("", text)


def _scan(text: str) -> list[Cand]:
    """扫出所有数字候选（阿拉伯 + 花式 + 中文）。"""
    out: list[Cand] = []
    i, n = 0, len(text)

    while i < n:
        ch = text[i]

        # (a) 阿拉伯数字串
        if ch.isdigit() and ch.isascii():
            j = i
            while j < n and text[j].isdigit() and text[j].isascii():
                j += 1
            out.append(Cand(int(text[i:j]), i, j, "plain", text[i:j]))
            i = j
            continue

        # (b) 圈号 / 黑圈 / 方框
        if ch in CIRCLED:
            out.append(Cand(CIRCLED[ch], i, i + 1, "fancy", ch))
            i += 1
            continue

        # (c) 中文数字（含大写繁体）
        if ch in CN_SMALL or ch in CN_BIG:
            val = CN_SMALL.get(ch, CN_BIG.get(ch))
            # 十X / 二X 的组合只处理 "十X"（十一~十九），其余按单字
            if ch in ("十", "拾") and i + 1 < n and (text[i + 1] in CN_SMALL or text[i + 1] in CN_BIG):
                nxt = CN_SMALL.get(text[i + 1], CN_BIG.get(text[i + 1]))
                if nxt:
                    out.append(Cand(10 + nxt, i, i + 2, "fancy", text[i:i + 2]))
                    i += 2
                    continue
            out.append(Cand(val, i, i + 1, "fancy", ch))
            i += 1
            continue

        i += 1

    return out


def _score(text: str, cand: Cand) -> int:
    """给候选打分：谁是"该回复的那个号"。可正可负，<=0 直接丢弃。"""
    s = 55 if cand.kind == "fancy" else 30
    tail = text[cand.end:]
    head = text[:cand.start]

    # --- 减分项：明显的干扰数字 ---
    if NOISE_AFTER.match(tail):
        s -= 100
    if NOISE_BEFORE.search(head):
        s -= 100
    if NOISE_RANGE.match(tail):          # 3-5 间 / 9:30 / 3.5 / 1/3
        s -= 100
    if NOISE_RANGE_BEFORE.search(head):  # 2-3：「-」前面是数字才算区间
        s -= 100
    if NOISE_RANK.search(head):          # 前三 / 第一 / 排名
        s -= 80
    if cand.kind == "plain" and len(cand.raw) >= 4:   # 电话 / 编号 / 年份
        s -= 100

    # 中文字数字（三/叁/两…）单字出现在句中多半是量词或成语，只有落在行尾才算数
    if len(cand.raw) == 1 and _HAN_RE.match(cand.raw):
        if tail.strip("。.!！?？ ~～"):
            s -= 45

    # --- 加分项：紧贴房源名、且在行尾 → 这就是答案 ---
    prev = head.rstrip()
    if prev and _HAN_RE.match(prev[-1]) and len(cand.raw) <= 3:
        if not tail.strip("。.!！?？ "):
            s += 40

    return s


def extract(text: str) -> Cand | None:
    """从一行（或拼接后的一整条消息）里挑出最像"应回复号码"的候选。"""
    text = normalize(text)
    cands = _scan(text)
    if not cands:
        return None

    # ① 扣X 最强信号：命中就直接返回，屏蔽其它一切数字
    m = _KOU_RE.search(text)
    if m and not KOU_BAD_UNIT.match(text[m.end(1):]):    # 「扣1万」「扣3元」不是口令
        for c in cands:
            if c.start == m.start(1):
                c.kind = "kou"
                c.score = 200
                return c

    # ② 其余候选按上下文打分
    for c in cands:
        c.score = _score(text, c)

    best = max(cands, key=lambda c: (c.score, c.start))   # 同分取更靠后的
    return best if best.score > 0 else None


def reply_code(text: str) -> str | None:
    """给抢单用的最终回复内容（ASCII 数字字符串或 None）。"""
    c = extract(text)
    return str(c.value) if c else None


def explain(text: str) -> str:
    """调试用：打印所有候选与得分。"""
    t = normalize(text)
    cands = _scan(t)
    for c in cands:
        if not c.score:
            c.score = _score(t, c)
    best = extract(text)
    return (f"text={text!r}\n  cands={cands}\n  best={best}")
