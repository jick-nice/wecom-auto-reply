"""Keep pale inline emoji inside all message crops and fingerprints.

The compiled segmenter measures dark text only. Patch its shared crop function,
not just OCR: baseline, _read_msg, strict judging and image log dedup all use it.
"""
import numpy as np


def _components(mask):
    """Small bounded ROI, 8-connected color seeds; no new runtime dependency."""
    seen = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        pending = [(int(y), int(x))]
        seen[y, x] = True
        xmin = xmax = int(x)
        ymin = ymax = int(y)
        count = 0
        while pending:
            yy, xx = pending.pop()
            count += 1
            xmin, xmax = min(xmin, xx), max(xmax, xx)
            ymin, ymax = min(ymin, yy), max(ymax, yy)
            for ny in range(max(0, yy - 1), min(height, yy + 2)):
                for nx in range(max(0, xx - 1), min(width, xx + 2)):
                    if mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        pending.append((ny, nx))
        yield xmin, ymin, xmax + 1, ymax + 1, count


def bounds(rgb, row, pad_x=2, pad_y=2):
    """Return padded bounds, adding only nearby same-line colored components.

    Search/size/alignment limits scale with the text row height, so a taller DPI
    does not require a screenshot-specific rectangle. Vertical center matching
    excludes emoji belonging to messages above or below this line.
    """
    height, width = rgb.shape[:2]
    x1, y1, x2, y2 = row.x1, row.y1, row.x2, row.y2
    if str(row.kind).lower() in ('content', 'own') and y2 > y1 and x2 > x1:
        from .emojimap2 import foreground
        h = y2 - y1
        left = max(0, x1 - h)
        right = min(width, x2 + 3 * h)
        top = max(0, int(y1 - 0.75 * h))
        bottom = min(height, int(y2 + 0.75 * h) + 1)
        mask = foreground(rgb[top:bottom, left:right])
        for a, b, c, d, count in _components(mask):
            a, b, c, d = a + left, b + top, c + left, d + top
            cw, ch = c - a, d - b
            overlap = min(d, row.y2) - max(b, row.y1)
            if (count >= max(4, h * h * 0.035)
                    and 0.3 * h <= cw <= 2.5 * h
                    and 0.5 * h <= ch <= 2.5 * h
                    and abs((b + d - row.y1 - row.y2) / 2) <= 0.5 * h
                    and overlap >= 0.3 * h):
                x1, y1, x2, y2 = min(x1, a), min(y1, b), max(x2, c), max(y2, d)
    return (max(0, x1 - pad_x), max(0, y1 - pad_y),
            min(width, x2 + pad_x), min(height, y2 + pad_y))


def apply():
    from . import segment
    if getattr(segment.crop, '_inline_emoji_crop', False):
        return
    original = segment.crop

    def crop(rgb, row, pad_x=2, pad_y=2):
        if str(row.kind).lower() not in ('content', 'own'):
            return original(rgb, row, pad_x=pad_x, pad_y=pad_y)
        x1, y1, x2, y2 = bounds(rgb, row, pad_x, pad_y)
        return rgb[y1:y2, x1:x2]

    crop._inline_emoji_crop = True
    segment.crop = crop
