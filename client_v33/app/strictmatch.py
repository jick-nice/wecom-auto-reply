# -*- coding: utf-8 -*-
"""严格版派单人匹配 + 派单消息判定。

替换 v3.3 的 match_dispatcher：
    原实现 `if n in s or s in n: return d` + 相似度 >= 0.72，
    真机语料实测有 4 类误判：
        '悲伤土豆丝'  → 判成派单人（只多一个字）
        '土豆'        → 判成派单人（只占后半）
        '悲伤'        → 判成派单人（只占前半）
        '悲'          → 判成派单人（单字）
    真实群里"土豆""悲伤"这类名字的人很多，一旦误判就会去回别人的闲聊。

本实现的约束（宁可漏，不可错）：
    ① 完全相等（去备注、去空格后）        → 最强命中
    ② 相差 1 个字符且长度 >= 4           → 命中（容忍打错字/多一个后缀）
    ③ 一方是另一方的**前缀或后缀**且较短方长度 >= 3 且长度比 >= 0.6
                                          → 命中（"悲伤土豆（业务）"这类）
    ④ 其余一律不命中（包括单字、半截、只像 0.7 的）

另外提供 order_verdict()：判断一条消息**到底是不是派单**，
把"派单人聊闲天"和"真派单"分开。
"""
from __future__ import annotations

import difflib
import re

_ALIAS = re.compile(r"[（(【\[].*$")       # 去括号备注
_SPACE = re.compile(r"[\s\u3000]+")
# 纯数字/纯符号（"3"、"..."、"？？"）不可能是派单正文
_ONLY_NUM = re.compile(r"^[\d\s.．,，、]+$")
_ONLY_PUNC = re.compile(r"^[\W_]+$", re.UNICODE)
# 名字后面"可忽略"的尾巴：纯数字、字母、括号内容、常见职位后缀
_IGNORABLE_TAIL = re.compile(
    r"^[\dA-Za-z\-_~·•\.\s]*(\(\d*\)|（\d*）)?[\dA-Za-z\-_~·•\.\s]*$"
)
# 末尾的独立阿拉伯数字（可带空格/标点），用于判断"表情补的数字"或"另写的号码"
_TRAILING_NUM = re.compile(r"(?:^|[\s\u3000,，。.!！?？、:：])([0-9]{1,3})\s*$")
_EMOJI_CODES = {
    "👌": "3", "👍": "1", "✌": "2", "✌️": "2", "🖖": "2",
    "✋": "5", "🖐": "5", "🤚": "5", "🤟": "3", "🤞": "2",
}


def emoji_code(text: str) -> str | None:
    value = str(text or "")
    for emoji, code in sorted(_EMOJI_CODES.items(), key=lambda item: len(item[0]), reverse=True):
        if emoji in value:
            return code
    return None

# 短语黑名单：这些是寒暄/闲聊，哪怕带数字也不该当派单
CHITCHAT_PATTERNS = [
    re.compile(p) for p in (
        r"^(好的?|收到|了解|明白|知道了?|行|可以|没问题|ok|OK|嗯+|哦+|哈+)",
        r"(谢谢|多谢|感谢|麻烦你|辛苦了)",
        r"(稍等|等一下|一会|马上|这就|待会|晚点|回头)",
        r"(我在路上|在路上|路上堵|堵车)",
        r"(吃[饭饭]|喝[水茶]|休息|下班|上班|开会)",
        r"(考虑一下|请示一下|看一下|问一下|确认一下|商量一下|想一下)",
        r"(明天见|今天有事|改天)",
        r"^(在吗|在不在|你好|您好|早上好|中午好|下午好|晚上好)",
        r"(多少钱|价格|能不能便宜|打折|优惠)",
        r"^(是的?|对|不是|没有|有)\s*$",
        r"^(我|你|他|她)(们)?(也|都)?(要|想|觉得|看|去|来|说|问)",
    )
]


def base_name(name: str) -> str:
    """去掉括号备注、空白，用于比对。"""
    s = _ALIAS.sub("", name or "")
    s = _SPACE.sub("", s)
    return s.strip()


def is_chitchat(text: str) -> bool:
    """这条正文像不像"人在聊天"而不是"在派单"。"""
    t = (text or "").strip()
    if not t:
        return True
    if _ONLY_NUM.match(t) or _ONLY_PUNC.match(t):
        return True
    for pat in CHITCHAT_PATTERNS:
        if pat.search(t):
            return True
    return False


def match_dispatcher(sender: str, dispatchers: list[str]) -> str | None:
    """严格匹配派单人。宁可漏，不可错。"""
    s = base_name(sender)
    if not s or not dispatchers:
        return None

    for d in dispatchers:
        n = base_name(d)
        if not n:
            continue

        # ① 完全相等
        if n == s:
            return d

        # ② 相差 1 个字符，且两边都不短（容忍多打/漏打一个字）
        #    必须同时满足：短的是长的连续子串 + 多出来的是"可忽略尾巴"。
        #    否则 '悲伤土豆' vs '悲伤土豆丝' 会被判成同一个人（真机语料的坑）。
        if len(n) >= 4 and len(s) >= 4 and abs(len(n) - len(s)) == 1:
            short, long_ = (n, s) if len(n) < len(s) else (s, n)
            if short in long_:
                extra = long_.replace(short, "", 1)
                if (not extra) or _IGNORABLE_TAIL.match(extra):
                    if difflib.SequenceMatcher(None, n, s).ratio() >= 0.85:
                        return d

        # ③ 前缀/后缀包含：只允许"长的那方是多带了后缀（如业务、小编、2）"，
        #    不允许"短的那方是长的那方的开头但有实义差异"（'悲伤土豆' vs '悲伤土豆丝'）
        #    —— 真机语料里 '悲伤土豆丝' 是**别人**，不能算派单人。
        #
        #    收紧方式：要求"多出来的那部分"必须是可忽略的尾部（纯数字/字母/括号内容），
        #    否则视为不同的人。
        short, long_ = (n, s) if len(n) <= len(s) else (s, n)
        if len(short) >= 3 and (long_.startswith(short) or long_.endswith(short)):
            extra = long_.replace(short, "", 1)
            # 多出来的只能是号码/字母/符号这类"备注性"尾巴
            if extra and _IGNORABLE_TAIL.match(extra) and \
                    len(short) / max(1, len(long_)) >= 0.55:
                return d
            if not extra:
                return d

    return None


def order_verdict(sender: str, text: str, dispatchers: list[str],
                  my_name: str = "") -> tuple[str, str]:
    """判断一条消息该怎么处理。

    返回 (动作, 原因)：
        ("reply", 口令)  —— 该回，第二项是要回的数字
        ("win", 被@的人) —— 是 @ 判定
        ("skip", 原因)   —— 不该动
    """
    from .numparse import reply_code
    from .engine import at_target

    if not dispatchers:
        return ("skip", "没配置派单人")
    if not match_dispatcher(sender, dispatchers):
        return ("skip", f"非派单人({sender!r})")

    t = (text or "").strip()
    if not t:
        return ("skip", "空正文")

    code = emoji_code(t)
    if code is not None:
        return ("reply", code)

    # @ 判定优先（派单人 @ 人 = 分胜负）
    tgt = at_target(t)
    code = reply_code(t)
    if tgt and code is None:
        return ("win", tgt)

    if code is None:
        return ("skip", f"没解析出号码({t!r})")

    # 虚指数字（"2点"、"一下"、"5万"…）→ 不是口令，不回
    from .strictnum import has_fake_number, has_strong_signal, cn_num_at_tail
    strong = has_strong_signal(t)
    if not strong:
        fake = has_fake_number(t)
        if fake:
            return ("skip", f"虚指数字({fake!r}) 不是口令")

        # ★ 核心规则：中文数字口令**必须在末尾**。
        #   '天津正荣府八' → 8     （八在末尾）
        #   '八格牙路' / '八个雅鹿' / '星期三' / '九成新' → 不回
        # 这条由 strictnum.extract_strict 统一处理，这里只做兜底校验：
        # 若解析出的号码是中文数字但不在末尾，判为不可信。
        try:
            from .numparse import extract as _ex
            _cand = _ex(t)
            if _cand is not None and all(
                    ch in "零〇一二两三四五六七八九十壹贰叁肆伍陆柒捌玖拾"
                    for ch in (_cand.raw or "")):
                if cn_num_at_tail(t) is None:
                    return ("skip", f"中文数字不在末尾({_cand.raw!r}) 不是口令")
        except Exception:
            pass

    # 派单人闲聊（带数字但其实是聊天）→ 不回
    if is_chitchat(t):
        return ("skip", f"像闲聊而非派单({t!r})")

    return ("reply", code)