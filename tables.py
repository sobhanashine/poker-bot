"""Multiplayer table store + lifecycle for the poker Mini App.

A "table" is a shared game lobby identified by a short code. State lives in
Upstash Redis (same client as store.py) so it survives across stateless
serverless invocations. Multiple players hit the API; each player polls for the
latest view. Because hold'em is turn-based, only the player whose turn it is can
mutate the hand, which keeps the read-modify-write races to a minimum.
"""
from __future__ import annotations

import base64
import pickle
import random
import string
import time

from poker.game import Action, Game, GameError, Stage

import store  # reuse the configured Upstash REST client

TABLE_TTL_SECONDS = 60 * 60 * 12  # tables auto-expire after 12h of inactivity
MAX_SEATS = 8
LOG_LIMIT = 14

_ACTION_BY_NAME = {a.value: a for a in Action}


def _key(code: str) -> str:
    return f"poker:table:{code}"


def _gen_code() -> str:
    # Unambiguous characters only (no 0/O/1/I).
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(6))


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _load(code: str) -> dict | None:
    raw = store._client().get(_key(code))
    if not raw:
        return None
    return pickle.loads(base64.b64decode(raw))


def _save(code: str, table: dict) -> None:
    table["updated_at"] = int(time.time())
    data = base64.b64encode(pickle.dumps(table)).decode()
    store._client().set(_key(code), data, ex=TABLE_TTL_SECONDS)


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def create_table(host_id: int, host_name: str,
                 small_blind: int = 10, big_blind: int = 20) -> dict:
    code = _gen_code()
    for _ in range(5):
        if _load(code) is None:
            break
        code = _gen_code()
    game = Game(chat_id=abs(hash(code)) % (10**9),
                small_blind=small_blind, big_blind=big_blind)
    game.add_player(host_id, host_name)
    table = {
        "code": code,
        "host_id": host_id,
        "game": game,
        "log": [f"{host_name} created the table"],
        "result": None,
        "hand_no": 0,
        "created_at": int(time.time()),
    }
    _save(code, table)
    return table


def join_table(code: str, user_id: int, name: str) -> dict:
    table = _load(code)
    if table is None:
        raise GameError("Table not found. Check the code.")
    game: Game = table["game"]
    existing = game.find(user_id)
    if existing is None:
        if len(game.players) >= MAX_SEATS:
            raise GameError("Table is full.")
        if game.stage != Stage.WAITING:
            raise GameError("A hand is in progress; wait for the next one.")
        game.add_player(user_id, name)
        table["log"].append(f"{name} joined")
        _save(code, table)
    return table


def leave_table(code: str, user_id: int) -> dict | None:
    table = _load(code)
    if table is None:
        return None
    game: Game = table["game"]
    p = game.find(user_id)
    if p:
        name = p.name
        try:
            game.remove_player(user_id)
        except GameError:
            pass
        table["log"].append(f"{name} left")
        # Reassign host if needed.
        if user_id == table["host_id"] and game.players:
            table["host_id"] = game.players[0].user_id
        _save(code, table)
    return table


def start_hand(code: str, user_id: int) -> dict:
    table = _load(code)
    if table is None:
        raise GameError("Table not found.")
    if user_id != table["host_id"]:
        raise GameError("Only the host can start the hand.")
    game: Game = table["game"]
    if game.stage not in (Stage.WAITING, Stage.HAND_OVER):
        raise GameError("A hand is already running.")
    if game.stage == Stage.HAND_OVER:
        game.cleanup_after_hand()
    game.start_hand()
    table["result"] = None
    table["hand_no"] += 1
    table["log"].append(f"Hand #{table['hand_no']} dealt")
    _save(code, table)
    return table


def act(code: str, user_id: int, action_name: str, amount: int = 0) -> dict:
    table = _load(code)
    if table is None:
        raise GameError("Table not found.")
    game: Game = table["game"]
    action = _ACTION_BY_NAME.get(action_name)
    if action is None:
        raise GameError("Invalid action.")
    cp = game.current_player
    if cp is None:
        raise GameError("No betting in progress.")
    if cp.user_id != user_id:
        raise GameError("Not your turn.")

    log = game.act(user_id, action, amount)
    _push_log(table, log)
    _progress(table)
    _save(code, table)
    return table


def _progress(table: dict) -> None:
    """Resolve a finished hand (mirrors the bot's progress_game)."""
    game: Game = table["game"]
    if game.stage == Stage.HAND_OVER and len(game.active_seats) == 1:
        out = game.resolve_uncontested()
        winner = out["winner"]
        table["result"] = {
            "type": "fold",
            "board": [c.code for c in game.board],
            "revealed": {},
            "winners": [{
                "id": winner.user_id, "name": winner.name,
                "amount": out["amount"], "desc": "others folded",
            }],
        }
        _push_log(table, f"{winner.name} won {out['amount']} (others folded)")
    elif game.stage == Stage.SHOWDOWN:
        res = game.showdown()
        revealed = {
            uid: [c.code for c in cards] for uid, cards in res.revealed.items()
        }
        winners = []
        for pot in res.pots:
            for uid, desc, amt in pot["winners"]:
                p = game.find(uid)
                winners.append({
                    "id": uid, "name": p.name if p else "?",
                    "amount": amt, "desc": desc,
                })
        table["result"] = {
            "type": "showdown",
            "board": [c.code for c in game.board],
            "revealed": revealed,
            "winners": winners,
        }
        for w in winners:
            _push_log(table, f"{w['name']} won {w['amount']} — {w['desc']}")


def _push_log(table: dict, line: str) -> None:
    table["log"].append(line)
    if len(table["log"]) > LOG_LIMIT:
        table["log"] = table["log"][-LOG_LIMIT:]


# --------------------------------------------------------------------------- #
# Serialization for the frontend
# --------------------------------------------------------------------------- #
def view(table: dict, user_id: int) -> dict:
    game: Game = table["game"]
    cp = game.current_player
    me = game.find(user_id)
    result = table.get("result")

    players = []
    for i, p in enumerate(game.players):
        revealed = (result or {}).get("revealed", {}).get(p.user_id)
        players.append({
            "id": p.user_id,
            "name": p.name,
            "chips": p.chips,
            "round_bet": p.round_bet,
            "committed": p.committed,
            "folded": p.folded,
            "all_in": p.all_in,
            "is_dealer": (i == game.button and game.stage != Stage.WAITING),
            "is_turn": (cp is not None and cp.user_id == p.user_id),
            "is_me": (p.user_id == user_id),
            "cards": revealed,  # only set at showdown
        })

    actions = _legal_view(game, user_id) if (cp and cp.user_id == user_id) else None

    return {
        "code": table["code"],
        "stage": game.stage.value,
        "board": [c.code for c in game.board],
        "pot": game.total_pot,
        "current_bet": game.current_bet,
        "small_blind": game.small_blind,
        "big_blind": game.big_blind,
        "hand_no": table.get("hand_no", 0),
        "host_id": table["host_id"],
        "is_host": (user_id == table["host_id"]),
        "players": players,
        "my_cards": [c.code for c in me.hole] if me and me.hole else [],
        "my_turn": bool(cp and cp.user_id == user_id),
        "turn_name": cp.name if cp else None,
        "actions": actions,
        "result": result,
        "log": table.get("log", [])[-LOG_LIMIT:],
        "can_start": _can_start(table, user_id),
    }


def _can_start(table: dict, user_id: int) -> bool:
    game: Game = table["game"]
    if user_id != table["host_id"]:
        return False
    if game.stage not in (Stage.WAITING, Stage.HAND_OVER):
        return False
    ready = [p for p in game.players if p.chips > 0]
    return len(ready) >= 2


def _legal_view(game: Game, user_id: int) -> dict:
    la = game.legal_actions(user_id)
    p = game.find(user_id)
    to_call = max(0, game.current_bet - (p.round_bet if p else 0))
    out = {
        "to_call": to_call,
        "can_fold": Action.FOLD in la,
        "can_check": Action.CHECK in la,
        "can_call": Action.CALL in la,
        "call_amount": la.get(Action.CALL, 0),
        "can_all_in": Action.ALL_IN in la,
        "all_in_amount": la.get(Action.ALL_IN, 0),
    }
    if Action.BET in la or Action.RAISE in la:
        verb = "bet" if Action.BET in la else "raise"
        out["raise_verb"] = verb
        out["raise_min"] = la.get(Action.BET, la.get(Action.RAISE))
        out["raise_max"] = (p.round_bet + p.chips) if p else 0
    return out
