"""Persistent player profiles (bankroll, stats) backed by Upstash Redis.

Every Telegram user gets one profile keyed by their Telegram id. The profile
holds the player's *bankroll* — the chips they own outside any table. Table
buy-ins are escrowed: joining a table debits the bankroll, leaving credits the
remaining stack back. Losses therefore reduce the bankroll for good, and a
player can never sit down with more chips than they own.

The profile also tracks which table the player is currently sitting at
(`active`) together with the last stack snapshot taken at a hand boundary.
If that table ever disappears (12h TTL expiry), `reconcile()` refunds the
snapshot so chips are not silently lost.

Chips are play-money only: they have no monetary value and can never be
bought, sold, transferred between users, or cashed out.
"""
from __future__ import annotations

import json
import time

from poker.game import GameError

import store  # reuse the configured Upstash REST client

STARTING_BANKROLL = 10_000


def _key(uid: int) -> str:
    return f"poker:profile:{uid}"


def _now() -> int:
    return int(time.time())


def load(uid: int) -> dict | None:
    raw = store._client().get(_key(uid))
    if not raw:
        return None
    return json.loads(raw)


def save(prof: dict) -> None:
    store._client().set(_key(prof["id"]), json.dumps(prof, ensure_ascii=False))


def get_or_create(uid: int, name: str) -> dict:
    prof = load(uid)
    if prof is None:
        prof = {
            "id": uid,
            "name": name,
            "chips": STARTING_BANKROLL,
            "hands_played": 0,
            "hands_won": 0,
            "biggest_pot": 0,
            "total_won": 0,
            "active": None,  # {"code", "stack"} while seated at a table
            "created_at": _now(),
        }
        save(prof)
    elif name and prof.get("name") != name:
        prof["name"] = name
        save(prof)
    return prof


def reconcile(prof: dict) -> dict:
    """Refund the escrowed stack if the player's table expired underneath them."""
    active = prof.get("active")
    if not active:
        return prof
    if not store._client().exists(f"poker:table:{active['code']}"):
        prof["chips"] += int(active.get("stack", 0))
        prof["active"] = None
        save(prof)
    return prof


def debit_buy_in(prof: dict, code: str, amount: int) -> None:
    """Move `amount` chips from the bankroll onto table `code` (escrow)."""
    active = prof.get("active")
    if active and active["code"] != code:
        raise GameError(
            f"شما سر میز «{active['code']}» نشسته‌اید؛ اول آن را ترک کنید.")
    if prof["chips"] < amount:
        raise GameError(
            f"موجودی شما کافی نیست (نیاز: {amount}، دارایی: {prof['chips']}).")
    prof["chips"] -= amount
    prev = int(active.get("stack", 0)) if active and active["code"] == code else 0
    prof["active"] = {"code": code, "stack": prev + amount}
    save(prof)


def credit_leave(uid: int, code: str, stack: int) -> None:
    """Return a player's remaining table stack to their bankroll."""
    prof = load(uid)
    if prof is None:
        return
    prof["chips"] += max(0, int(stack))
    active = prof.get("active")
    if active and active["code"] == code:
        prof["active"] = None
    save(prof)


def snapshot_stack(uid: int, code: str, stack: int) -> None:
    """Record the player's current table stack (taken at hand boundaries)."""
    prof = load(uid)
    if prof is None:
        return
    active = prof.get("active")
    if active and active["code"] == code:
        active["stack"] = int(stack)
        save(prof)


def record_hand(uid: int, code: str, stack: int, won: bool, pot: int) -> None:
    """Update stats + stack snapshot for one participant after a hand."""
    prof = load(uid)
    if prof is None:
        return
    prof["hands_played"] = prof.get("hands_played", 0) + 1
    if won:
        prof["hands_won"] = prof.get("hands_won", 0) + 1
        prof["biggest_pot"] = max(prof.get("biggest_pot", 0), int(pot))
        prof["total_won"] = prof.get("total_won", 0) + int(pot)
    active = prof.get("active")
    if active and active["code"] == code:
        active["stack"] = int(stack)
    save(prof)


def view(prof: dict) -> dict:
    """Public shape sent to the Mini App."""
    in_play = int((prof.get("active") or {}).get("stack", 0))
    return {
        "id": prof["id"],
        "name": prof.get("name", ""),
        "chips": prof.get("chips", 0),
        "hands_played": prof.get("hands_played", 0),
        "hands_won": prof.get("hands_won", 0),
        "biggest_pot": prof.get("biggest_pot", 0),
        "total_won": prof.get("total_won", 0),
        "in_play": in_play,
        "net_worth": prof.get("chips", 0) + in_play,
        "created_at": prof.get("created_at"),
        "active_code": (prof.get("active") or {}).get("code"),
    }
