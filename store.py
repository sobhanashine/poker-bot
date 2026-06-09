"""Persistent game-state store backed by Upstash Redis (REST).

Serverless functions are stateless, so the in-memory table dict cannot
survive between Telegram updates. We serialize each `Game` with pickle and
keep it in Redis keyed by chat id. Upstash's REST client is used because it
works over plain HTTP, which is friendly to short-lived serverless runtimes.
"""
from __future__ import annotations

import base64
import os
import pickle

from poker.game import Game

_redis = None


def _client():
    global _redis
    if _redis is None:
        from upstash_redis import Redis
        _redis = Redis(
            url=os.environ["UPSTASH_REDIS_REST_URL"],
            token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
        )
    return _redis


def _key(chat_id: int) -> str:
    return f"poker:game:{chat_id}"


def load_game(chat_id: int) -> Game | None:
    raw = _client().get(_key(chat_id))
    if not raw:
        return None
    return pickle.loads(base64.b64decode(raw))


def save_game(chat_id: int, game: Game) -> None:
    data = base64.b64encode(pickle.dumps(game)).decode()
    _client().set(_key(chat_id), data)


def delete_game(chat_id: int) -> None:
    _client().delete(_key(chat_id))
