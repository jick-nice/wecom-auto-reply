# -*- coding: utf-8 -*-
"""后端客户端：登录 / 心跳 / 报单。后端跑在 Vercel，数据在 Supabase。"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

UA = "AutoGrab/3.0 (Windows)"


class ApiError(Exception):
    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


class Api:
    def __init__(self, base_url: str, token: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.last_ok = 0.0
        self.last_ent: dict | None = None

    # ------------------------------------------------------------- 底层
    def _post(self, payload: dict, timeout: float = 8.0) -> dict:
        if not self.base_url:
            raise ApiError("未配置后端地址", -1)
        req = urllib.request.Request(
            self.base_url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": UA,
                     **({"Authorization": f"Bearer {self.token}"} if self.token else {})},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8") or "{}").get("error", "")
            except Exception:
                detail = ""
            raise ApiError(detail or f"HTTP {e.code}", e.code)
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            raise ApiError(f"网络不可用: {e}", -2)
        except json.JSONDecodeError:
            raise ApiError("后端返回非 JSON", -3)
        if not body.get("ok"):
            raise ApiError(body.get("error") or "后端拒绝", -4)
        return body

    # ------------------------------------------------------------- 业务
    def login(self, username: str, password: str) -> dict:
        body = self._post({"action": "login", "username": username, "password": password})
        self.token = body.get("token", "")
        self.last_ok = time.time()
        self.last_ent = body.get("ent")
        return body

    def heartbeat(self) -> dict:
        body = self._post({"action": "heartbeat"})
        self.last_ok = time.time()
        self.last_ent = body.get("ent")
        return body

    def report_order(self, code: str, target: str = "") -> dict:
        return self._post({"action": "order", "code": code, "target": target})

    # -------------------------------------------------------------- 权限
    def allowed(self, grace_hours: float = 24.0) -> tuple[bool, str, dict | None]:
        """心跳失败时的降级判断：网络断了不立刻锁人，但超过 grace 就锁。"""
        try:
            body = self.heartbeat()
            ent = body.get("ent") or {}
            if not ent.get("allowed"):
                return False, ent.get("reason") or "使用资格已失效", ent
            return True, "", ent
        except ApiError as e:
            if e.code == 401 or e.code == 403:
                return False, str(e), None
            if self.last_ent and (time.time() - self.last_ok) < grace_hours * 3600:
                return True, f"离线宽限中（{str(e)}）", self.last_ent
            return False, str(e), None
