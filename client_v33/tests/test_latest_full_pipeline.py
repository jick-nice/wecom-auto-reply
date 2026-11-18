"""Full latest screenshot regression using the real client_v33 OCR and engine.

No desktop capture or real Sender is instantiated. The screenshot includes the
input area; only the chat area above y=100 is passed to the message pipeline.
Run: python -m unittest discover -s client_v33/tests -p test_latest_full_pipeline.py -v
"""
import json
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image

from test_gesture_pipeline import image, make_engine
from app import segment, engine, emojimap2, bodycrop, riskguard
from app.ocr import Ocr


class LatestFullScreenshotRegression(unittest.TestCase):
    def test_latest_real_ocr_full_message_should_reply_two(self):
        screenshot = image('gesture_latest.png')
        self.assertEqual(screenshot.shape, (235, 594, 3))
        # y=100 is the visible top border of the input toolbar. Keep the full
        # chat width and both sender/body rows; do not crop just the gesture.
        frame = segment.normalize(screenshot[:100].copy())
        eng, sender, logs = make_engine()
        eng.ocr = Ocr()
        eng.ocr.warmup()
        eng._learn_layout_from(frame)
        threshold = segment.auto_thr(frame, int(eng.cfg.get('dark_thr', 150)))
        rows = segment.analyze(frame, eng.layout, dark_thr=threshold)
        messages = eng._pairs(rows)
        self.assertEqual(len(messages), 1, rows)
        msg = messages[0]
        self.assertIsNotNone(msg.sender_row)
        name_crop = segment.crop(frame, msg.sender_row, pad_x=3, pad_y=6)
        body_crop = segment.crop(frame, msg.body_row, pad_x=2, pad_y=2)
        record = eng._read_msg(frame, msg)
        for _ in range(6):
            eng._judge(frame, msg, sender=record['sender'],
                       text=record['text'], key=record['key'])
        report = {
            'screenshot_shape': list(screenshot.shape),
            'chat_bounds': [0, 0, 594, 100],
            'threshold': threshold,
            'layout': vars(eng.layout),
            'rows': [repr(row) for row in rows],
            'name_crop_shape': list(name_crop.shape),
            'body_crop_shape': list(body_crop.shape),
            'sender': record['sender'],
            'body': str(record['text']),
            'image_code': getattr(record['text'], 'image_code', None),
            'send': sender.calls,
            'logs': logs,
        }
        print('\nLATEST_FULL_PIPELINE=' + json.dumps(report, ensure_ascii=False))
        self.assertEqual(record['sender'], '悲伤土豆', report)
        self.assertIn('蔡徐坤', record['text'], report)
        self.assertEqual(getattr(record['text'], 'image_code', None), '2', report)
        self.assertEqual(sender.calls, ['2'], report)
        eng._area_ok = True
        for _ in range(3):
            eng._handle_frame(frame)
        self.assertEqual(sender.calls, ['2'], logs)

    def test_startup_baseline_suppresses_existing_full_message(self):
        frame = image('gesture_latest.png')[:100].copy()
        eng, sender, logs = make_engine()
        eng.ocr = Ocr()
        eng.area = object()
        eng._area_ok = True
        eng._grab_frame = lambda: frame
        eng._warm_baseline()
        msg = eng._pairs(segment.analyze(
            frame, eng.layout, dark_thr=segment.auto_thr(frame)))[0]
        record = eng._read_msg(frame, msg)
        self.assertEqual(record['sender'], '悲伤土豆')
        self.assertEqual(record['text'].image_code, '2')
        self.assertIn(record['key'], eng._sent)
        for _ in range(6):
            eng._handle_frame(frame)
        self.assertEqual(sender.calls, [], logs)

    def test_same_text_different_outside_dark_bbox_gestures_have_distinct_keys(self):
        frame = image('gesture_latest.png')[:100].copy()
        eng, sender, logs = make_engine()
        eng.ocr = Ocr()
        eng._learn_layout_from(frame)
        msg = eng._pairs(segment.analyze(
            frame, eng.layout, dark_thr=segment.auto_thr(frame)))[0]
        first = eng._read_msg(frame, msg)
        other = frame.copy()
        # Replace only the emoji beyond the unchanged dark-text bbox. Text and
        # Msg/Row are identical, so the old crop would produce the same key.
        x1, y1, x2, y2 = bodycrop.bounds(frame, msg.body_row)
        seed_boxes = [box for box in emojimap2.find_boxes(frame[y1:y2, x1:x2])
                      if x1 + box[0] >= msg.body_row.x2]
        self.assertEqual(len(seed_boxes), 1)
        a, b, c, d = seed_boxes[0]
        xx, yy = x1 + a, y1 + b
        other[y1:y2, xx:x2] = frame[msg.body_row.y1, msg.body_row.x2 + 1]
        template = image('gestures_green.png')
        A, B, C, D = emojimap2.find_boxes(template)[0]
        hand = template[B:D, A:C]
        other[yy:yy + hand.shape[0], xx:xx + hand.shape[1]] = hand
        second = eng._read_msg(other, msg)
        self.assertNotEqual(first['key'], second['key'])
        self.assertEqual(first['text'].image_code, '2')
        self.assertEqual(second['text'].image_code, '3')
        for now, pixels, rec in ((1000.0, frame, first), (1003.0, other, second)):
            with patch.object(riskguard.time, 'time', return_value=now):
                for _ in range(4):
                    eng._judge(pixels, msg, sender=rec['sender'], text=rec['text'], key=rec['key'])
        self.assertEqual(sender.calls, ['2', '3'], logs)

    def test_crop_scales_and_excludes_neighboring_message(self):
        original = image('gesture_latest.png')[:100].copy()
        for scale in (1.0, 1.5, 2.0):
            frame = np.array(Image.fromarray(original).resize(
                (round(original.shape[1] * scale), round(original.shape[0] * scale)),
                Image.Resampling.NEAREST))
            row = segment.Row(segment.CONTENT, round(50 * scale), round(62 * scale),
                              round(28 * scale), round(65 * scale))
            box = bodycrop.bounds(frame, row)
            # Every original emoji seed is inside the resulting crop.
            seeds = emojimap2.foreground(frame)
            seeds[:, :row.x2] = False
            seeds[:row.y1 - round(6 * scale)] = False
            seeds[row.y2 + round(6 * scale):] = False
            ys, xs = np.nonzero(seeds)
            self.assertLessEqual(box[0], int(xs.min()))
            self.assertLessEqual(box[1], int(ys.min()))
            self.assertGreater(box[2], int(xs.max()))
            self.assertGreater(box[3], int(ys.max()))
            # Add another same-colored hand on a distinct line, near the
            # search-band edge: its center must not extend this message crop.
            altered = frame.copy()
            source = frame[box[1]:box[3], box[0]:box[2]]
            offset = source.shape[0] + max(1, round(scale))
            end = min(frame.shape[0], box[1] + offset + source.shape[0])
            altered[box[1] + offset:end, box[0]:box[2]] = source[:end - box[1] - offset]
            self.assertEqual(bodycrop.bounds(altered, row), box)


if __name__ == '__main__':
    unittest.main(verbosity=2)
