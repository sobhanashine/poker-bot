"""Multiplayer table store + lifecycle for the poker Mini App.

A "table" is a shared game lobby identified by a short code. State lives in
Upstash Redis (same client as store.py) so it survives across stateless
serverless invocations. Players poll for the latest view; because hold'em is
turn-based, only the player whose turn it is can mutate the hand.

Pacing features (enforced lazily, on the next poll/action — no background jobs):
  * turn timer  — each player has a deadline; on timeout we auto check/fold
  * pre-actions — a player queues "check/fold" or "call any" before their turn
  * auto-deal   — the next hand deals itself a few seconds after the last ends

Money features:
  * custom blinds + starting stack chosen when the table is created
  * rebuy / top-up to a full buy-in between hands
"""
from __future__ import annotations

import base64
import pickle
import random
import string
import time

from poker.game import Action, Game, GameError, Stage

import nearby
import profiles
import store  # reuse the configured Upstash REST client

TABLE_TTL_SECONDS = 60 * 60 * 12   # tables auto-expire after 12h of inactivity
MAX_SEATS = 8
LOG_LIMIT = 14
DEFAULT_TURN_SECONDS = 30
NEXT_HAND_DELAY = 6                 # seconds to show the result before re-dealing
DEFAULT_STACK = 1000

_BETTING = {Stage.PREFLOP, Stage.FLOP, Stage.TURN, Stage.RIVER}
_ACTION_BY_NAME = {a.value: a for a in Action}
_PREACTIONS = {"check_fold", "call_any", "check"}

# Game modes:
#   holdem — every hand is Texas Hold'em
#   omaha  — every hand is Omaha (4 hole cards, use exactly 2)
#   mixed  — Hold'em, but every Nth hand is Omaha. Because this changes the
#            game everyone signed up for, all seated players must agree
#            before the first hand can be dealt.
MODES = {"holdem", "omaha", "mixed"}
DEFAULT_OMAHA_EVERY = 2


def _key(code: str) -> str:
    return f"poker:table:{code}"


def _gen_code() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I
    return "".join(random.choice(alphabet) for _ in range(6))


def _now() -> int:
    return int(time.time())


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _load(code: str) -> dict | None:
    raw = store._client().get(_key(code))
    if not raw:
        return None
    return pickle.loads(base64.b64decode(raw))


def _save(code: str, table: dict) -> None:
    table["updated_at"] = _now()
    data = base64.b64encode(pickle.dumps(table)).decode()
    store._client().set(_key(code), data, ex=TABLE_TTL_SECONDS)


def _seat_player(game: Game, uid: int, name: str, chips: int) -> None:
    """Seat a player between hands (works at WAITING or HAND_OVER)."""
    if game.find(uid):
        return
    if len(game.players) >= MAX_SEATS:
        raise GameError("Table is full.")
    from poker.game import Player
    game.players.append(Player(user_id=uid, name=name, chips=chips))


def _eligible(game: Game) -> list:
    return [p for p in game.players if p.chips > 0]


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def create_table(host_id: int, host_name: str, small_blind: int = 10,
                 big_blind: int = 20, starting_stack: int = DEFAULT_STACK,
                 turn_seconds: int = DEFAULT_TURN_SECONDS,
                 lat: float | None = None, lon: float | None = None,
                 mode: str = "holdem",
                 omaha_every: int = DEFAULT_OMAHA_EVERY) -> dict:
    small_blind = max(1, int(small_blind))
    big_blind = max(small_blind + 1, int(big_blind))
    starting_stack = max(big_blind * 5, int(starting_stack))
    turn_seconds = min(180, max(10, int(turn_seconds)))
    if mode not in MODES:
        mode = "holdem"
    omaha_every = min(10, max(2, int(omaha_every)))

    # The buy-in comes out of the host's persistent bankroll (escrowed back
    # when they leave). You can never sit down with more than you own.
    prof = profiles.reconcile(profiles.get_or_create(host_id, host_name))
    if prof["chips"] < starting_stack:
        raise GameError(
            f"موجودی شما برای این بای‌این کافی نیست "
            f"(نیاز: {starting_stack}، دارایی: {prof['chips']}).")

    code = _gen_code()
    for _ in range(5):
        if _load(code) is None:
            break
        code = _gen_code()

    profiles.debit_buy_in(prof, code, starting_stack)

    game = Game(chat_id=abs(hash(code)) % (10**9),
                small_blind=small_blind, big_blind=big_blind,
                starting_stack=starting_stack,
                variant="omaha" if mode == "omaha" else "holdem")
    game.add_player(host_id, host_name)

    # Geo-tagged tables show up for nearby players looking for a game.
    if lat is not None and lon is not None:
        nearby.register_table(code, lat, lon, host_name,
                              small_blind, big_blind, starting_stack)
    mode_label = {"holdem": "Hold'em", "omaha": "Omaha",
                  "mixed": f"Mixed (Omaha every {omaha_every} hands)"}[mode]
    table = {
        "code": code,
        "host_id": host_id,
        "game": game,
        "members": {host_id: host_name},
        "buy_in": starting_stack,
        "turn_seconds": turn_seconds,
        "mode": mode,
        "omaha_every": omaha_every,
        # Mixed mode must be agreed by every seated player before the first
        # deal. The host proposed it, so they count as agreed already.
        "agreed": [host_id] if mode == "mixed" else [],
        "preactions": {},
        "turn_uid": None,
        "turn_deadline": None,
        "next_hand_at": None,
        "log": [f"{host_name} created the table ({mode_label})"],
        "result": None,
        "hand_no": 0,
        "created_at": _now(),
    }
    _tick(table)
    _save(code, table)
    return table


def join_table(code: str, user_id: int, name: str) -> dict:
    table = _load(code)
    if table is None:
        raise GameError("Table not found. Check the code.")
    game: Game = table["game"]
    if game.find(user_id) is None:
        if game.stage in _BETTING:
            raise GameError("A hand is in progress; you'll be seated next hand.")
        if len(game.players) >= MAX_SEATS:
            raise GameError("Table is full.")
        prof = profiles.reconcile(profiles.get_or_create(user_id, name))
        profiles.debit_buy_in(prof, code, table["buy_in"])
        _seat_player(game, user_id, name, table["buy_in"])
        table["log"].append(f"{name} joined")
    table.setdefault("members", {})[user_id] = name
    _tick(table)
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
        # Cash the remaining stack back to the bankroll. If removal was
        # deferred (mid-hand fold-out), zero the seat so the chips can't be
        # paid twice when the seat is dropped at cleanup.
        profiles.credit_leave(user_id, code, p.chips)
        p.chips = 0
        table.get("members", {}).pop(user_id, None)
        table["preactions"].pop(user_id, None)
        if user_id in table.get("agreed", []):
            table["agreed"].remove(user_id)
        table["log"].append(f"{name} left")
        if user_id == table["host_id"] and game.players:
            table["host_id"] = game.players[0].user_id
    _tick(table)
    _save(code, table)
    return table


def rebuy(code: str, user_id: int, amount: int | None = None) -> dict:
    table = _load(code)
    if table is None:
        raise GameError("Table not found.")
    game: Game = table["game"]
    if game.stage in _BETTING:
        raise GameError("You can only rebuy between hands.")
    buy_in = table["buy_in"]
    name = table.get("members", {}).get(user_id, f"Player {user_id}")
    p = game.find(user_id)
    if p is None:
        if len(game.players) >= MAX_SEATS:
            raise GameError("Table is full.")
        prof = profiles.reconcile(profiles.get_or_create(user_id, name))
        profiles.debit_buy_in(prof, code, buy_in)
        _seat_player(game, user_id, name, buy_in)
        table["log"].append(f"{name} bought in for {buy_in}")
    else:
        if p.chips >= buy_in:
            raise GameError("Your stack is already full.")
        topup = buy_in - p.chips
        prof = profiles.reconcile(profiles.get_or_create(user_id, name))
        if prof["chips"] <= 0:
            raise GameError("موجودی شما برای خرید مجدد کافی نیست.")
        topup = min(topup, prof["chips"])  # top up as far as the bankroll allows
        profiles.debit_buy_in(prof, code, topup)
        p.chips += topup
        p.sitting_out = False
        table["log"].append(f"{p.name} topped up (+{topup})")
    _tick(table)
    _save(code, table)
    return table


def start_hand(code: str, user_id: int) -> dict:
    table = _load(code)
    if table is None:
        raise GameError("Table not found.")
    if user_id != table["host_id"]:
        raise GameError("Only the host can start the hand.")
    game: Game = table["game"]
    if game.stage in _BETTING:
        raise GameError("A hand is already running.")
    if len(_eligible(game)) < 2:
        raise GameError("Need at least 2 players with chips.")
    if _agreement_pending(table):
        raise GameError("Everyone must agree to the mixed Omaha mode first.")
    _deal(table)
    _tick(table)
    _save(code, table)
    return table


def agree_mode(code: str, user_id: int) -> dict:
    """A seated player accepts the table's mixed Omaha proposal."""
    table = _load(code)
    if table is None:
        raise GameError("Table not found.")
    game: Game = table["game"]
    if table.get("mode") != "mixed":
        raise GameError("This table has nothing to agree to.")
    p = game.find(user_id)
    if p is None:
        raise GameError("Take a seat first.")
    agreed = table.setdefault("agreed", [])
    if user_id not in agreed:
        agreed.append(user_id)
        table["log"].append(f"{p.name} agreed to mixed Omaha")
    _tick(table)
    _save(code, table)
    return table


def _agreement_pending(table: dict) -> bool:
    """True while a mixed table still needs sign-off from seated players.

    Only gates the first deal — players who join mid-session can see the
    mode before sitting down, so joining is itself the agreement.
    """
    if table.get("mode") != "mixed" or table.get("hand_no", 0) > 0:
        return False
    game: Game = table["game"]
    agreed = set(table.get("agreed", []))
    return any(p.user_id not in agreed for p in game.players)


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
    table["preactions"].pop(user_id, None)
    _tick(table)
    _save(code, table)
    return table


def set_preaction(code: str, user_id: int, mode: str) -> dict:
    table = _load(code)
    if table is None:
        raise GameError("Table not found.")
    game: Game = table["game"]
    p = game.find(user_id)
    cp = game.current_player
    if mode == "none" or not mode:
        table["preactions"].pop(user_id, None)
    elif mode in _PREACTIONS:
        if not p or not p.in_hand or p.all_in:
            raise GameError("You can't pre-act right now.")
        if cp and cp.user_id == user_id:
            raise GameError("It's your turn — just act.")
        table["preactions"][user_id] = mode
    else:
        raise GameError("Invalid pre-action.")
    _tick(table)
    _save(code, table)
    return table


# --------------------------------------------------------------------------- #
# Progression engine (timeouts, pre-actions, auto-deal)
# --------------------------------------------------------------------------- #
def _hand_variant(table: dict, hand_no: int) -> str:
    mode = table.get("mode", "holdem")
    if mode == "omaha":
        return "omaha"
    if mode == "mixed":
        every = table.get("omaha_every", DEFAULT_OMAHA_EVERY)
        if hand_no % every == 0:
            return "omaha"
    return "holdem"


def _deal(table: dict) -> None:
    game: Game = table["game"]
    if game.stage == Stage.HAND_OVER:
        game.cleanup_after_hand()
    hand_no = table.get("hand_no", 0) + 1
    variant = _hand_variant(table, hand_no)
    game.start_hand(variant=variant)
    table["result"] = None
    table["preactions"] = {}
    table["next_hand_at"] = None
    table["turn_uid"] = None
    table["hand_no"] = hand_no
    label = " — OMAHA round!" if (
        variant == "omaha" and table.get("mode") == "mixed") else ""
    _push_log(table, f"Hand #{hand_no} dealt{label}")


def _set_deadline(table: dict) -> None:
    """Stamp a deadline for the player to act; stable across polls."""
    game: Game = table["game"]
    cp = game.current_player
    if cp is None:
        table["turn_uid"] = None
        table["turn_deadline"] = None
        return
    if table.get("turn_uid") != cp.user_id:
        table["turn_uid"] = cp.user_id
        table["turn_deadline"] = _now() + table.get(
            "turn_seconds", DEFAULT_TURN_SECONDS)


def _drain_auto_actions(table: dict) -> None:
    """Apply queued pre-actions and expired turn timers, in order."""
    game: Game = table["game"]
    for _ in range(MAX_SEATS * 6):  # generous loop guard
        cp = game.current_player
        if cp is None:
            break
        uid = cp.user_id
        to_call = game.current_bet - cp.round_bet
        pre = table["preactions"].get(uid)

        do = None
        if pre == "check_fold":
            do = "check" if to_call <= 0 else "fold"
            table["preactions"].pop(uid, None)
        elif pre == "call_any":
            do = "call" if to_call > 0 else "check"
            table["preactions"].pop(uid, None)
        elif pre == "check":
            table["preactions"].pop(uid, None)
            if to_call <= 0:
                do = "check"
            else:
                break  # bet appeared: void the pre-check, let them act
        else:
            dl = table.get("turn_deadline")
            if dl and _now() >= dl:
                do = "check" if to_call <= 0 else "fold"
            else:
                break  # waiting on a human who still has time

        try:
            log = game.act(uid, _ACTION_BY_NAME[do], 0)
            _push_log(table, f"{log} (auto)")
        except GameError:
            break
        _resolve_if_over(table)
        _set_deadline(table)  # fresh clock for the next player


def _tick(table: dict) -> None:
    """Advance everything that can happen without a fresh human action."""
    _resolve_if_over(table)
    _drain_auto_actions(table)
    _resolve_if_over(table)

    game: Game = table["game"]
    if table.get("result") is not None and game.stage == Stage.HAND_OVER:
        if len(_eligible(game)) >= 2:
            if not table.get("next_hand_at"):
                table["next_hand_at"] = _now() + NEXT_HAND_DELAY
            elif _now() >= table["next_hand_at"]:
                _deal(table)
        else:
            table["next_hand_at"] = None
    _set_deadline(table)


def _resolve_if_over(table: dict) -> None:
    """Resolve a finished hand once (idempotent)."""
    game: Game = table["game"]
    if table.get("result") is not None:
        return
    if game.stage == Stage.HAND_OVER and len(game.active_seats) == 1:
        out = game.resolve_uncontested()
        winner = out["winner"]
        table["result"] = {
            "type": "fold",
            "board": [c.code for c in game.board],
            "revealed": {},
            "winners": [{"id": winner.user_id, "name": winner.name,
                         "amount": out["amount"]}],
        }
        _push_log(table, f"{winner.name} won {out['amount']}")
        _settle_profiles(table)
    elif game.stage == Stage.SHOWDOWN:
        res = game.showdown()
        revealed = {uid: [c.code for c in cards]
                    for uid, cards in res.revealed.items()}
        winners = []
        # The hand-category description is deliberately dropped: players see
        # the cards, not a "Pair of Ks"-style hint.
        for pot in res.pots:
            for uid, _desc, amt in pot["winners"]:
                p = game.find(uid)
                winners.append({"id": uid, "name": p.name if p else "?",
                                "amount": amt})
        table["result"] = {"type": "showdown",
                            "board": [c.code for c in game.board],
                            "revealed": revealed, "winners": winners}
        for w in winners:
            _push_log(table, f"{w['name']} won {w['amount']}")
        _settle_profiles(table)


def _settle_profiles(table: dict) -> None:
    """After a hand resolves: update stats + bankroll stack snapshots.

    The snapshot is what gets refunded if the table later expires, so it must
    be taken here, after payouts, at every hand boundary.
    """
    game: Game = table["game"]
    result = table.get("result") or {}
    won_amount: dict[int, int] = {}
    for w in result.get("winners", []):
        won_amount[w["id"]] = won_amount.get(w["id"], 0) + w["amount"]
    for p in game.players:
        if not p.hole:  # was not dealt into this hand
            continue
        try:
            profiles.record_hand(p.user_id, table["code"], p.chips,
                                 won=p.user_id in won_amount,
                                 pot=won_amount.get(p.user_id, 0))
        except Exception:  # noqa: BLE001 — stats must never break the game
            pass


def _push_log(table: dict, line: str) -> None:
    table["log"].append(line)
    if len(table["log"]) > LOG_LIMIT:
        table["log"] = table["log"][-LOG_LIMIT:]


# --------------------------------------------------------------------------- #
# Serialization for the frontend
# --------------------------------------------------------------------------- #
def load_and_tick(code: str) -> dict | None:
    table = _load(code)
    if table is None:
        return None
    before = (table.get("hand_no"), table.get("turn_uid"),
              table.get("turn_deadline"), table.get("next_hand_at"),
              len(table.get("log", [])))
    _tick(table)
    after = (table.get("hand_no"), table.get("turn_uid"),
             table.get("turn_deadline"), table.get("next_hand_at"),
             len(table.get("log", [])))
    if before != after:
        _save(code, table)
    return table


def view(table: dict, user_id: int) -> dict:
    game: Game = table["game"]
    cp = game.current_player
    me = game.find(user_id)
    result = table.get("result")

    players = []
    for i, p in enumerate(game.players):
        revealed = (result or {}).get("revealed", {}).get(p.user_id)
        players.append({
            "id": p.user_id, "name": p.name, "chips": p.chips,
            "round_bet": p.round_bet, "committed": p.committed,
            "folded": p.folded, "all_in": p.all_in,
            "is_dealer": (i == game.button and game.stage != Stage.WAITING),
            "is_turn": (cp is not None and cp.user_id == p.user_id),
            "is_me": (p.user_id == user_id),
            "cards": revealed,
        })

    my_turn = bool(cp and cp.user_id == user_id)
    actions = _legal_view(game, user_id) if my_turn else None
    can_preact = bool(
        game.stage in _BETTING and me and me.in_hand and not me.all_in
        and not my_turn)
    can_rebuy = bool(
        game.stage not in _BETTING and me is not None
        and me.chips < table["buy_in"])

    mode = table.get("mode", "holdem")
    agreed = table.get("agreed", [])
    pending = _agreement_pending(table)
    next_variant = _hand_variant(table, table.get("hand_no", 0) + 1)

    return {
        "code": table["code"],
        "stage": game.stage.value,
        "board": [c.code for c in game.board],
        "pot": game.total_pot,
        "current_bet": game.current_bet,
        "small_blind": game.small_blind,
        "big_blind": game.big_blind,
        "buy_in": table["buy_in"],
        "hand_no": table.get("hand_no", 0),
        "mode": mode,
        "omaha_every": table.get("omaha_every", DEFAULT_OMAHA_EVERY),
        "variant": getattr(game, "hand_variant", "holdem"),
        "next_variant": next_variant,
        "agreement_pending": pending,
        "i_agreed": user_id in agreed,
        "agreed_count": sum(1 for p in game.players if p.user_id in agreed),
        "seat_count": len(game.players),
        "host_id": table["host_id"],
        "is_host": (user_id == table["host_id"]),
        "players": players,
        "my_cards": [c.code for c in me.hole] if me and me.hole else [],
        "my_turn": my_turn,
        "turn_name": cp.name if cp else None,
        "actions": actions,
        "result": result,
        "log": table.get("log", [])[-LOG_LIMIT:],
        "can_start": _can_start(table, user_id),
        "can_preact": can_preact,
        "my_preaction": table.get("preactions", {}).get(user_id),
        "can_rebuy": can_rebuy,
        "server_time": _now(),
        "turn_deadline": table.get("turn_deadline"),
        "turn_seconds": table.get("turn_seconds", DEFAULT_TURN_SECONDS),
        "next_hand_at": table.get("next_hand_at"),
        "seated": me is not None,
    }


def _can_start(table: dict, user_id: int) -> bool:
    game: Game = table["game"]
    if user_id != table["host_id"]:
        return False
    if game.stage in _BETTING:
        return False
    if _agreement_pending(table):
        return False
    return len(_eligible(game)) >= 2


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
        out["raise_verb"] = "bet" if Action.BET in la else "raise"
        out["raise_min"] = la.get(Action.BET, la.get(Action.RAISE))
        out["raise_max"] = (p.round_bet + p.chips) if p else 0
    return out
