# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client_v33'))
from app.strictmatch import emoji_code, order_verdict

names=['测试用户A（备注很长，换号要通知）','另一个非常非常长的派单人名称【备注，中文逗号；斜杠/也不能拆】']
for n in names:
    assert order_verdict('测试用户A','测试内容✌️', [names[0]], '测试用户F') == ('reply','2') if n == names[0] else True
assert emoji_code('测试内容✌️') == '2'
assert emoji_code('测试内容🖖') == '2'
assert emoji_code('测试内容👌') == '3'
print('emoji_and_scope_ok')
