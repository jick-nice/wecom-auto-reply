"""Offline real-screenshot regression; FakeSender never touches the desktop.
Run: python -m unittest discover -s client_v33/tests -p test_gesture_pipeline.py -v
The two gray gestures are explicitly calibrated variants, not held-out accuracy.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'client_v33'))
from app import emojimap2 as em, engine, riskguard, segment
riskguard.apply()


def image(name):
    return np.array(Image.open(ROOT / 'samples' / name).convert('RGB'))


def green_rows():
    a = image('gestures_green.png')
    out=[]
    for x,y,X,Y in em.find_boxes(a):
        row=np.full((38,100,3),(53,210,141),np.uint8)
        row[10:10+Y-y,60:60+X-x]=a[y:Y,x:X]
        out.append(row)
    return out


class FakeOCR:
    def __init__(self,text='蔡徐坤 鸡',name='悲伤土豆'):
        self.text,self.name=text,name
    def read_row(self,*args,**kwargs): return self.text
    def read_name(self,*args,**kwargs): return self.name
    def read(self,*args,**kwargs): return ''


class FakeSender:
    def __init__(self): self.calls=[]
    def send(self,code,*args,**kwargs):
        self.calls.append(code)
        return 1.0


def make_engine(text='蔡徐坤 鸡',name='悲伤土豆'):
    logs=[]
    sender=FakeSender()
    cfg={'dispatchers':['悲伤土豆'],'my_name':'测试员','debug_judge':True,
         'verify_send':False,'shadow':False,'max_orders':100}
    eng=engine.Engine(cfg,FakeOCR(text,name),object(),sender,log=logs.append)
    eng._geometry_ready=lambda: True
    eng._input_reader=lambda: None
    eng.shadow=False
    eng.verify_send=False
    return eng,sender,logs


def msg_for(a):
    h,w=a.shape[:2]
    # A real Msg uses the sender row to determine is_own. No sender OCR desktop.
    return segment.Msg(segment.Row('NAME',0,1,0,1),segment.Row('CONTENT',0,h,0,w))


class GestureRegression(unittest.TestCase):
    def test_green_fixed_mapping_and_boxes(self):
        a=image('gestures_green.png')
        boxes=em.find_boxes(a)
        self.assertEqual(len(boxes),4)
        self.assertTrue(all(y>0 for _,y,_,_ in boxes))
        self.assertEqual([em.classify(a[y:Y,x:X])[0] for x,y,X,Y in boxes],['3','2','5','2'])
        inverted=255-a
        self.assertEqual([em.classify(inverted[y:Y,x:X])[0]
                          for x,y,X,Y in em.find_boxes(inverted)],['3','2','5','2'])

    def test_gray_original_and_inversion(self):
        a=image('gesture_gray.png')
        for row in (a[:35],a[66:]):
            self.assertEqual(em.classify_row(row)[0],'2')
            self.assertEqual(em.classify_row(255-row)[0],'2')

    def test_independent_transforms(self):
        a=image('gesture_gray.png')
        for row in (a[:35],a[66:]):
            moved=np.pad(row,((3,4),(11,7),(0,0)),mode='edge')
            self.assertEqual(em.classify_row(moved)[0],'2')
            dim=np.clip(row.astype(float)*0.90+6,0,255).astype(np.uint8)
            self.assertEqual(em.classify_row(dim)[0],'2')

    def test_text_circles_stickers_not_gestures(self):
        negative=[image('gesture_gray.png')[:35,:54],image('gesture_gray.png')[66:,:53]]
        for kind in ('circle','sticker','text','blob'):
            p=Image.new('RGB',(100,38),(230,231,234)); d=ImageDraw.Draw(p)
            if kind=='circle':
                d.ellipse((65,9,84,28),outline=(196,146,88),width=2);d.text((71,12),'2',fill=(196,146,88))
            elif kind=='sticker':
                d.rounded_rectangle((60,5,84,30),radius=4,fill=(175,150,123));d.text((68,11),'2',fill='white')
            elif kind=='text': d.text((55,12),'12345',fill=(10,10,10))
            else: d.ellipse((60,8,80,28),fill=(255,190,120))
            negative.append(np.array(p))
        for row in negative:
            self.assertIsNone(em.classify_row(row)[0])

    def test_engine_green_and_gray_full_pipeline(self):
        gray=image('gesture_gray.png')
        for row,want in zip(green_rows()+[gray[:35],gray[66:]],['3','2','5','2','2','2']):
            eng,sender,logs=make_engine('八格牙路 鸡')
            msg=msg_for(row)
            for _ in range(6): eng._judge(row,msg)
            self.assertEqual(sender.calls,[want],logs)
            self.assertEqual(sum('表情识别' in s for s in logs),1,logs)
            self.assertEqual(sum('抢单 →' in s for s in logs),1,logs)

    def test_english_name_with_gesture(self):
        row=green_rows()[0]
        eng,sender,logs=make_engine('ZhengRongFu chicken');msg=msg_for(row)
        for _ in range(4): eng._judge(row,msg)
        self.assertEqual(sender.calls,['3'],logs)

    def test_engine_metadata_precomputed_and_ocr_fake_number(self):
        row=green_rows()[2];eng,sender,logs=make_engine('蔡徐坤 2点')
        msg=msg_for(row)
        body=eng._read_body(row,msg)
        self.assertEqual(getattr(body,'image_code',None),'5')
        for _ in range(6): eng._judge(row,msg,text=body)
        self.assertEqual(sender.calls,['5'],logs)
        self.assertEqual(sum('表情识别' in s for s in logs),1)

    def test_engine_wrong_sender_and_own_bubble(self):
        row=green_rows()[0]
        for name in ('土豆','悲伤土豆丝','路人甲'):
            eng,sender,logs=make_engine(name=name);msg=msg_for(row)
            for _ in range(5): eng._judge(row,msg)
            self.assertEqual(sender.calls,[])
            self.assertEqual(sum('严格过滤' in s for s in logs),1,logs)
        eng,sender,logs=make_engine();msg=segment.Msg(None,msg_for(row).body_row)
        for _ in range(3): eng._judge(row,msg,sender='悲伤土豆')
        self.assertEqual(sender.calls,[])

    def test_engine_gray_inverted_full_pipeline(self):
        gray=image('gesture_gray.png')
        for row in (255-gray[:35],255-gray[66:]):
            eng,sender,logs=make_engine(); msg=msg_for(row)
            for _ in range(5): eng._judge(row,msg)
            self.assertEqual(sender.calls,['2'],logs)

    def test_real_ocr_gray_commands(self):
        from app.ocr import Ocr
        ocr=Ocr();ocr.warmup()
        gray=image('gesture_gray.png')
        for row in (gray[:35],gray[66:],255-gray[:35],255-gray[66:]):
            eng,sender,logs=make_engine();eng.ocr=ocr;msg=msg_for(row)
            body=eng._read_body(row,msg)
            for _ in range(4): eng._judge(row,msg,sender='悲伤土豆',text=body)
            self.assertEqual(sender.calls,['2'],logs)

    def test_engine_throttle_recheck_not_lost(self):
        rows=green_rows();eng,sender,logs=make_engine()
        with patch.object(riskguard.time,'time',return_value=1000.0):
            for _ in range(2): eng._judge(rows[0],msg_for(rows[0]))
            for _ in range(5): eng._judge(rows[2],msg_for(rows[2]))
            self.assertEqual(sender.calls,['3'],logs)
            self.assertEqual(sum('本单先挂起' in s for s in logs),1,logs)
        with patch.object(riskguard.time,'time',return_value=1003.0):
            for _ in range(3): eng._judge(rows[2],msg_for(rows[2]))
            self.assertEqual(sender.calls,['3','5'],logs)
        with patch.object(riskguard.time,'time',return_value=999999.0):
            for _ in range(3): eng._judge(rows[0],msg_for(rows[0]))
            self.assertEqual(sender.calls,['3','5'],logs)

    def test_strong_text_priority_and_chitchat(self):
        row=green_rows()[0]
        eng,sender,logs=make_engine('北风花园 扣7');msg=msg_for(row)
        for _ in range(4): eng._judge(row,msg)
        self.assertEqual(sender.calls,['7'],logs)
        for text in ('谢谢 2','一会再回',''):
            eng,sender,logs=make_engine(text)
            for _ in range(4): eng._judge(row,msg)
            self.assertEqual(sender.calls,[],logs)


if __name__=='__main__': unittest.main(verbosity=2)
