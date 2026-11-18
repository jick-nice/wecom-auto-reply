# -*- coding: utf-8 -*-
"""入口：抢单助手 v3.0"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.capture import set_dpi_aware  # noqa: E402

set_dpi_aware()                          # 必须在 tkinter 之前

from app.gui import App  # noqa: E402

if __name__ == "__main__":
    App().mainloop()
