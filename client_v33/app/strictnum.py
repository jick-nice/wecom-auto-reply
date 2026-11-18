# -*- coding: utf-8 -*-
"""号码解析的严格化补丁：拦掉"中文虚指"被当成口令。

真机语料实测 v3.3 的 reply_code 有 5 类误判，全部来自**中文里的虚指/量词**：
    '下午2点看房'      → 2   （"点"是时间，NOISE_AFTER 里有"点"但这里"点"在数字后第1位，被漏）
    '差不多5点回来'    → 5   （同上）
    '客户说要考虑一下' → 1   （"一下"里的"一"）
    '这个我得请示一下' → 1   （同上）
    '我先吃饭，一会再回' → 1 （"一会"里的"一"）

做法：在 numparse.extract 外层包一道**虚指词表**，命中就整条判为 None。
这样既不改 .pyc，也不会影响真派单（真派单里不会出现这些词）。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 虚指 / 量词
# 只要一条消息里出现这些"数字+虚指后缀"的组合，就认为那个数字不是口令。
FAKE_NUM_PATTERNS = [
    # 一下 / 一会 / 一点 / 一次(口头) / 一顿 / 一趟 / 一些
    re.compile(r"(一下|一会|一点儿?|一趟|一顿|一些|一番|一口气|一下子)"),
    # X点（时间）：2点、10点半、3点左右
    re.compile(r"[0-9零一二两三四五六七八九十]+\s*(点|点钟|点半|点多|点整|点左右|点前|点后)"),
    # X分钟后 / X小时后 / X天后 / X个月
    re.compile(r"[0-9零一二两三四五六七八九十]+\s*(分钟|小时|钟头|天|周|星期|个月|年)"),
    # 差不多 / 大概 / 左右 这类模糊
    re.compile(r"(差不多|大概|大约|左右|将近|快|近)"),
    # 第X / X号人 / X个(不用来派单的泛指)
    re.compile(r"第\s*[0-9零一二两三四五六七八九十]+"),
    # 电话号码/长数字
    re.compile(r"\d{7,}"),
    # 时间 HH:MM
    re.compile(r"\d{1,2}\s*[:：]\s*\d{1,2}"),
    # 小数 / 分数 / 版本
    re.compile(r"\d+\s*[.．]\s*\d+"),
    re.compile(r"\d+\s*/\s*\d+"),
    # 区间 3-5 / 3~5 / 3至5
    re.compile(r"\d+\s*[-~—–至到]\s*\d+"),
    # 面积/价格/楼层等后面紧跟量词
    re.compile(r"\d+\s*(平|平米|㎡|平方|万|元|块|层|楼|号楼|栋|室|厅|卫|套|间|人|个|岁|次|成|折|公里|米)"),
]

# 强口令信号：出现这些就**优先**认（"扣X"、紧贴房源名）
STRONG_SIGNALS = [
    re.compile(r"扣\s*([0-9零一二两三四五六七八九十壹贰叁肆伍陆柒捌玖]{1,3})"),
    re.compile(r"看房\s*([0-9零一二两三四五六七八九十壹贰叁肆伍陆柒捌玖]{1,3})"),
    re.compile(r"定房\s*([0-9零一二两三四五六七八九十壹贰叁肆伍陆柒捌玖]{1,3})"),
]

# ★ 中文数字出现在**词语内部**时不是口令 —— 真机踩到的坑：
#     '八格牙路✌️'  → 解析成 8（"八"是词的一部分，真正口令是表情 ✌️=2）
#     '五花八门'    → 8      '三心二意' → 2      '一心一意' → 1
# 判据：中文数字**前后都是汉字**（在词里），且不在消息末尾 → 视为词内字。
_CN_NUM = "零〇一二两三四五六七八九十壹贰叁肆伍陆柒捌玖拾"
_HAN = r"\u4e00-\u9fff"
IDIOM_HAN = re.compile(rf"[{_HAN}][{_CN_NUM}][{_HAN}]")

# ★★ 核心规则（用户明确要求）：
#   中文数字口令**必须在消息末尾**。
#     '天津测试项目A八'   → 8   ✓（八在末尾）
#     '八格牙路'/'八个雅鹿' → 不回（八在开头）
#     '星期三'/'九成新'/'二话不说' → 不回（数在句中）
#
# 末尾行尾判定：中文数字后面只允许标点/空白到行尾。
#   注意：**不限制前面**——'天津测试项目A八' 的「八」前面是汉字「府」，正是要认的情况。
_TAIL_CN = re.compile(
    rf"(?P<num>[{_CN_NUM}]{{1,2}})"        # 中文数字（1~2 字，如"十""十二"）
    rf"[\s\u3000。．.!！?？~～,，、:：]*$"   # 后面只允许标点空白到行尾
)

# 时间/日期类前缀：出现在中文数字前面时，这个数字是时间不是口令
_CN_TIME_PREFIX = re.compile(
    r"(星期|礼拜|周|第|共|约|大概|差不多|将近|前|后|左右|上午|下午|中午|晚上|凌晨|早上|晚上|"
    r"月|号|点|分|岁|成|折|倍|米|平|元|块|万|个|人|次|天|年|小时|分钟)\s*$"
)

# 含中文数字的高频固定词（额外保险）
IDIOM_WORDS = (
    "八格牙路", "八格", "八个雅鹿", "一心一意", "三心二意", "五花八门", "七上八下",
    "四面八方", "十全十美", "乱七八糟", "五颜六色", "九牛一毛",
    "一塌糊涂", "一模一样", "三番五次", "三言两语", "二话不说",
    "一五一十", "七嘴八舌", "千头万绪", "百发百中", "五体投地",
    "六神无主", "九牛一毛", "一溜烟", "一眨眼", "一股脑儿",
    "七零八落", "丢三落四", "一转眼", "一上来", "二话不说",
    # 末尾是中文数字的成语（"丢三落四"的「四」在末尾，会被末尾规则误认）
    "丢三落四", "挑三拣四", "朝三暮四", "张三李四", "不三不四",
    "颠三倒四", "说三道四", "五湖四海", "五讲四美",
    # 其他末尾带中文数字的常见说法
    "一二三四", "数一数二", "独一无二", "接二连三", "举一反三",
)


def has_idiom_number(text: str) -> str | None:
    """检测"中文数字在词里"的情况。返回命中的片段。"""
    t = text or ""
    for w in IDIOM_WORDS:
        if w in t:
            return w
    m = IDIOM_HAN.search(t)
    return m.group(0) if m else None


def cn_num_at_tail(text: str) -> str | None:
    """中文数字是否**在消息末尾**（这才是口令）。

    返回末尾的中文数字（原文），否则 None。
    同时排除"星期三""九成新"这类：数字后面必须是行尾，
    前面不能是时间/量词前缀。
    """
    t = (text or "").rstrip()
    if not t:
        return None
    m = _TAIL_CN.search(t)
    if not m:
        return None
    # 数字前面若是"星期/第/共/点/成…"这类前缀 → 不是口令
    head = t[:m.start("num")]
    if _CN_TIME_PREFIX.search(head):
        return None
    return m.group("num")


def has_any_tail_digit(text: str) -> bool:
    """末尾是否有独立的号码（阿拉伯或中文）。"""
    t = (text or "").strip()
    if not t:
        return False
    if _TRAILING_NUM.search(t):
        return True
    return cn_num_at_tail(t) is not None


def has_fake_number(text: str) -> str | None:
    """返回命中的"虚指"说明；没有则 None。"""
    t = text or ""
    for pat in FAKE_NUM_PATTERNS:
        m = pat.search(t)
        if m:
            return m.group(0)
    return None


def has_strong_signal(text: str) -> bool:
    t = text or ""
    return any(p.search(t) for p in STRONG_SIGNALS)


def extract_strict(text: str):
    """严格版 extract。核心规则：**中文数字必须在末尾**才算口令。

    判定顺序：
      ① 有强信号（扣X/看房X）→ 走原解析（派单人明确指示，不受位置限制）
      ② 阿拉伯数字 → 走原解析（阿拉伯数字受现有 NOISE 规则约束，比较可靠）
      ③ 只有中文数字：
           · 在**末尾** → 是口令（天津测试项目A八 → 8）
           · 不在末尾 → 不是口令（八格牙路 / 星期三 / 九成新 / 二话不说）
      ④ 都没命中 → None
    """
    from . import numparse

    t = (text or "").strip()
    if not t:
        return None

    if has_strong_signal(t):
        return numparse.extract(t)

    fake = has_fake_number(t)
    if fake:
        return None

    # 成语/固定词里有中文数字（含末尾是数字的："丢三落四""不三不四"）
    #
    # 但要放行一种情况：成语**后面另跟独立数字** —— 那是表情识别补进来的号码，
    # 例如 '八格牙路✌️' → OCR 读成 '八格牙路 鸡'，表情补成 '八格牙路 2'。
    # 判据：成语后面还有阿拉伯数字，且该数字在**末尾**。
    if has_idiom_number(t):
        if not _TRAILING_NUM.search(t):
            return None
        # 有末尾独立数字 → 跳过成语阻断，继续按数字解析

    c = numparse.extract(t)
    if c is None:
        return None

    # 阿拉伯数字：保留现有逻辑
    if not _is_cn_num_char(c.raw):
        return c

    # 中文数字：必须在末尾
    tail = cn_num_at_tail(t)
    if tail is None:
        return None
    # 末尾那个数字必须就是解析出来的这个
    try:
        if int(c.value) != _cn_value(tail):
            return None
    except Exception:
        return None
    return c


def _is_cn_num_char(raw: str) -> bool:
    return bool(raw) and all(ch in _CN_NUM for ch in raw)


def _cn_value(s: str) -> int:
    """中文数字 → int（支持一到十九这种简单组合）。"""
    from .numparse import CN_SMALL, CN_BIG
    if len(s) == 1:
        return CN_SMALL.get(s, CN_BIG.get(s, -1))
    if len(s) == 2:
        a = CN_SMALL.get(s[0], CN_BIG.get(s[0], -1))
        b = CN_SMALL.get(s[1], CN_BIG.get(s[1], -1))
        if a == 10 and b > 0:          # 十一 ~ 十九
            return 10 + b
        if b == 10 and a > 0:          # 二十 / 三十
            return a * 10
    return -1


# 末尾的独立阿拉伯数字，用于判定"表情补的号码"
_TRAILING_NUM = re.compile(r"(?:^|[\s\u3000,，。.!！?？、:：])([0-9]{1,3})\s*$")


def reply_code_strict(text: str) -> str | None:
    c = extract_strict(text)
    return str(c.value) if c else None