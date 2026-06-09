"""Validate Telegram Mini App `initData`.

The frontend (Telegram WebApp) sends `initData` — a signed query string proving
which Telegram user opened the app. We verify the HMAC with the bot token so a
caller cannot spoof another user. See:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl


class AuthError(Exception):
    pass


def verify_init_data(init_data: str, max_age_seconds: int = 86400) -> dict:
    """Return {user, start_param, auth_date} if valid, else raise AuthError."""
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise AuthError("server missing BOT_TOKEN")
    if not init_data:
        raise AuthError("missing initData")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise AuthError("missing hash")

    data_check_string = "\n".join(
        f"{k}={pairs[k]}" for k in sorted(pairs)
    )
    secret_key = hmac.new(
        b"WebAppData", token.encode(), hashlib.sha256
    ).digest()
    calc_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(calc_hash, received_hash):
        raise AuthError("bad signature")

    # Freshness check (defends against replay of an old initData).
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        auth_date = 0
    if max_age_seconds and auth_date and (time.time() - auth_date) > max_age_seconds:
        raise AuthError("initData expired")

    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError:
        raise AuthError("bad user field")
    if not user.get("id"):
        raise AuthError("no user id")

    return {
        "user": user,
        "start_param": pairs.get("start_param") or None,
        "auth_date": auth_date,
    }


def display_name(user: dict) -> str:
    name = user.get("first_name", "") or ""
    last = user.get("last_name", "") or ""
    full = (name + " " + last).strip()
    return full or user.get("username") or f"Player {user.get('id')}"
