# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client_v33'))
from app.strictmatch import emoji_code, order_verdict

names=['悲伤土豆（备注很长，换号要通知）','另一个非常非常长的派单人名称【备注，中文逗号；斜杠/也不能拆】']
for n in names:
    assert order_verdict('悲伤土豆','蔡徐坤✌️', [names[0]], '杨煜') == ('reply','2') if n == names[0] else True
assert emoji_code('蔡徐坤✌️') == '2'
assert emoji_code('蔡徐坤🖖') == '2'
assert emoji_code('蔡徐坤👌') == '3'
print('emoji_and_scope_ok')
