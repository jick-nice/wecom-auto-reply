from __future__ import annotations

import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "client_v33"
sys.path.insert(0, str(CLIENT))
from app import profilepatch


class FakeButton:
    def __init__(self):
        self.states = []

    def config(self, **kwargs):
        self.states.append(kwargs)


class FakeApi:
    base_url = "https://fixture.invalid"

    def __init__(self):
        self.allowed_calls = 0

    def allowed(self):
        self.allowed_calls += 1
        return True, "ok", {"allowed": True}


class FakeApp:
    def __init__(self):
        self.cfg = {"dispatchers": ["sender"], "my_name": "operator"}
        self.api = FakeApi()
        self.ocr = None
        self.running = False
        self.started = 0
        self.big = FakeButton()
        self.callbacks = []
        self.logs = []

    def after(self, delay, callback):
        self.callbacks.append((delay, callback))

    def _log(self, text):
        self.logs.append(text)

    def save_settings(self):
        pass

    def start_grab(self):
        # Mirrors the legacy compiled GUI's synchronous eligibility recheck.
        if self.api.base_url and not self.api.allowed()[0]:
            return
        self.started += 1
        self.running = True


class StartupBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.original_modules = {name: sys.modules.get(name) for name in ("app.gui", "app.cfg", "app.ocr")}
        self.original_present = {name: name in sys.modules for name in self.original_modules}
        self.gui = types.ModuleType("app.gui")
        self.gui.App = type("App", (), {"save_settings": FakeApp.save_settings,
                                         "start_grab": FakeApp.start_grab})
        self.gui.LoginFrame = type("LoginFrame", (), {})
        self.cfg = types.ModuleType("app.cfg")
        self.cfg.load = lambda: {}
        self.cfg.save = lambda _cfg: None
        self.ocr = types.ModuleType("app.ocr")
        from unittest.mock import Mock
        self.ocr.Ocr = Mock()
        sys.modules["app.gui"] = self.gui
        sys.modules["app.cfg"] = self.cfg
        sys.modules["app.ocr"] = self.ocr
        profilepatch._applied = False
        self.assertTrue(profilepatch.apply())
        self.start = self.gui.App.start_grab

    def tearDown(self):
        profilepatch._applied = False
        for name, module in self.original_modules.items():
            if self.original_present[name]:
                sys.modules[name] = module
            else:
                sys.modules.pop(name, None)

    def wait_callbacks(self, app, minimum=2):
        deadline = time.monotonic() + 2
        while len(app.callbacks) < minimum and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertGreaterEqual(len(app.callbacks), minimum)

    def finish_callback(self, app):
        callbacks = [cb for delay, cb in app.callbacks if delay == 0]
        self.assertTrue(callbacks, "worker did not queue its Tk completion")
        callbacks[-1]()

    def test_button_cancels_startup_and_stale_finish_does_not_start(self):
        app = FakeApp()
        self.start(app)
        self.wait_callbacks(app)
        self.assertEqual(app.big.states[-1]["state"], "normal")
        self.assertIn("取消", app.big.states[-1]["text"])
        self.start(app)  # Same bound start callback is the startup cancel action.
        self.assertTrue(app._startup_cancel is None)
        self.finish_callback(app)
        self.assertEqual(app.started, 0)
        self.assertFalse(app.running)

    def test_watchdog_invalidates_late_finish(self):
        app = FakeApp()
        self.start(app)
        self.wait_callbacks(app)
        watchdogs = [cb for delay, cb in app.callbacks if delay == 30000]
        self.assertEqual(len(watchdogs), 1)
        watchdogs[0]()
        self.finish_callback(app)
        self.assertEqual(app.started, 0)
        self.assertFalse(app.running)

    def test_button_toggle_cancels_startup(self):
        app = FakeApp()
        def toggle(instance):
            if instance.running:
                return
            self.start(instance)
        self.start(app)
        self.wait_callbacks(app)
        toggle(app)
        self.finish_callback(app)
        self.assertEqual(app.started, 0)
        self.assertTrue(any("取消启动" in line for line in app.logs))

    def test_eligibility_is_checked_once_and_ocr_is_cached(self):
        app = FakeApp()
        saved_ocr = object()
        app.ocr = saved_ocr
        self.start(app)
        self.wait_callbacks(app)
        self.finish_callback(app)
        self.assertEqual(app.api.allowed_calls, 1)
        self.assertEqual(app.started, 1)
        self.assertIs(app.ocr, saved_ocr)
        self.ocr.Ocr.assert_not_called()


if __name__ == "__main__":
    unittest.main()
