from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "client_v33"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from app.captureguard import FailureReporter, frame_usable, geometry_usable


class FakeFrame:
    shape = (8, 12, 3)
    size = 8 * 12 * 3


class EmptyFrame:
    shape = (0, 0, 3)
    size = 0


def test_fake_frames_are_classified_without_win32():
    assert frame_usable(FakeFrame())
    assert not frame_usable(EmptyFrame())
    assert not frame_usable(None)
    assert geometry_usable((10, 20, 800, 600))
    assert not geometry_usable(None)
    assert not geometry_usable((10, 20, 0, 600))


def test_first_failure_is_logged_and_repeated_failures_are_rate_limited():
    messages = []
    reporter = FailureReporter(messages.append, interval=3600)
    reporter.report("窗口几何尚未就绪")
    reporter.report("窗口几何尚未就绪")
    assert len(messages) == 1
    assert "自动重试" in messages[0]


def test_reason_change_is_logged_immediately():
    messages = []
    reporter = FailureReporter(messages.append, interval=3600)
    reporter.report("窗口几何尚未就绪")
    reporter.report("PrintWindow 未返回有效画面")
    assert len(messages) == 2
    assert "PrintWindow" in messages[-1]
