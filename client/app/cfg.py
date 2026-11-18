# -*- coding: utf-8 -*-
"""本地配置（%APPDATA%\\AutoGrab\\config.json）。"""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

APP_DIR_NAME = "AutoGrab"

DEFAULTS = {
    "api_url": "",                      # 例：https://xxx.vercel.app/api
    "manual_region": [],                # 兜底手填区域（相对窗口客户区），一般不用
    "input_pos": [0, 0],                # 输入框点击位，自动定位后自动填
    "enter_mode": "enter",              # enter | ctrl_enter
    "dispatchers": [],                  # 派单人（可含括号备注）
    "my_name": "",                      # 自己在群里的名字，用于判定抢到
    "max_orders": 3,
    "orders_today": 0,
    "orders_date": "",
    "require_foreground": True,         # 企业微信不在前台就不发，绝不抢焦点
    "allow_click": True,                # 允许软件自己点输入框（每次运行最多 3 次）
    "strip_h": 900,                     # 单帧最多处理多少像素高（超大窗口的上限保护）
    "dark_thr": 150,
    "auto_reply_after_win": False,      # 抢到后是否自动扣 1
    "win_reply_text": "1",
    "hotkey_vk": 0x78,                  # F9 启停
    "username": "",
    "remember_token": "",
}


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    p = Path(base) / APP_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    cfg = dict(DEFAULTS)
    p = config_path()
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    today = date.today().isoformat()
    if cfg.get("orders_date") != today:
        cfg["orders_date"] = today
        cfg["orders_today"] = 0
    return cfg


def save(cfg: dict) -> None:
    config_path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_daily(cfg: dict) -> None:
    cfg["orders_date"] = date.today().isoformat()
    cfg["orders_today"] = 0
