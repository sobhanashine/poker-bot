"""Vercel serverless API for the poker Mini App.

The frontend POSTs JSON: {"op": "...", "initData": "<telegram initData>", ...}.
We authenticate the Telegram user from initData, dispatch the operation against
the shared table store, and return the user-specific view as JSON.

Ops:
    create  {small_blind?, big_blind?}        -> create a table, host joins
    join    {code}                            -> join an existing table
    state   {code}                            -> poll latest view
    start   {code}                            -> host deals a hand
    act     {code, action, amount?}           -> take a poker action
    leave   {code}                            -> leave the table
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# Make repo-root modules importable (tables, tgauth, store, poker/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tables
from poker.game import GameError
from tgauth import AuthError, display_name, verify_init_data


def _handle(data: dict) -> tuple[int, dict]:
    op = data.get("op")
    if not op:
        return 400, {"error": "missing op"}

    try:
        auth = verify_init_data(data.get("initData", ""))
    except AuthError as e:
        return 401, {"error": f"auth failed: {e}"}

    user = auth["user"]
    uid = int(user["id"])
    name = display_name(user)
    code = (data.get("code") or auth.get("start_param") or "").strip().upper()

    try:
        if op == "create":
            sb = int(data.get("small_blind", 10))
            bb = int(data.get("big_blind", 20))
            table = tables.create_table(uid, name, sb, bb)
        elif op == "join":
            if not code:
                return 400, {"error": "missing code"}
            table = tables.join_table(code, uid, name)
        elif op == "state":
            if not code:
                return 400, {"error": "missing code"}
            table = tables._load(code)
            if table is None:
                return 404, {"error": "table not found"}
        elif op == "start":
            table = tables.start_hand(code, uid)
        elif op == "act":
            table = tables.act(
                code, uid, data.get("action", ""), int(data.get("amount", 0)))
        elif op == "leave":
            tables.leave_table(code, uid)
            return 200, {"ok": True, "left": True}
        else:
            return 400, {"error": f"unknown op: {op}"}
    except GameError as e:
        return 200, {"error": str(e), "table": _safe_view(code, uid)}
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return 500, {"error": f"server error: {e}"}

    return 200, {"ok": True, "table": tables.view(table, uid)}


def _safe_view(code: str, uid: int) -> dict | None:
    """Best-effort current view (used to return state alongside an error)."""
    try:
        t = tables._load(code)
        return tables.view(t, uid) if t else None
    except Exception:  # noqa: BLE001
        return None


class handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._send(400, {"error": "bad json"})
            return
        status, payload = _handle(data)
        self._send(status, payload)

    def do_GET(self):  # noqa: N802
        self._send(200, {"ok": True, "service": "poker mini app api"})
