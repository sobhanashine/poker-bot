"""Texas Hold'em game engine (framework agnostic).

This module knows nothing about Telegram. It models a single table, the
betting rounds, side-pots for all-in situations, and the showdown. The bot
layer drives it and renders the results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .cards import Card, Deck
from .evaluator import best_hand, best_hand_omaha, describe

HOLE_CARDS = {"holdem": 2, "omaha": 4}


class Stage(Enum):
    WAITING = "waiting"
    PREFLOP = "preflop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"
    HAND_OVER = "hand_over"


class Action(Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    ALL_IN = "all_in"


@dataclass
class Player:
    user_id: int
    name: str
    chips: int
    hole: list[Card] = field(default_factory=list)
    round_bet: int = 0       # contributed in the current betting round
    committed: int = 0       # contributed across the whole hand
    folded: bool = False
    all_in: bool = False
    sitting_out: bool = False  # out of chips / left between hands

    @property
    def in_hand(self) -> bool:
        return not self.folded and not self.sitting_out

    @property
    def can_act(self) -> bool:
        return self.in_hand and not self.all_in


@dataclass
class Pot:
    amount: int
    eligible: list[int]  # user_ids eligible to win this pot


@dataclass
class ShowdownResult:
    pots: list[dict]            # [{amount, winners:[(user_id, hand_desc)]}]
    revealed: dict[int, list[Card]]  # user_id -> hole cards shown


class GameError(Exception):
    """Raised on an illegal operation against the engine."""


class Game:
    def __init__(self, chat_id: int, small_blind: int = 10, big_blind: int = 20,
                 starting_stack: int = 1000, seed: int | None = None,
                 variant: str = "holdem"):
        self.chat_id = chat_id
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.starting_stack = starting_stack
        self._seed = seed
        self.variant = variant            # default for every hand
        self.hand_variant = variant       # variant of the hand in progress

        self.players: list[Player] = []
        self.stage: Stage = Stage.WAITING
        self.deck: Deck | None = None
        self.board: list[Card] = []
        self.button: int = 0  # seat index of the dealer button

        self.current_bet: int = 0    # amount to call in this round
        self.min_raise: int = big_blind
        self.turn_index: int = 0     # seat index of player to act
        self.acted: set[int] = set()  # user_ids who acted since last aggression
        self.last_aggressor: int | None = None

    # ------------------------------------------------------------------ #
    # Table management (between hands)
    # ------------------------------------------------------------------ #
    def add_player(self, user_id: int, name: str) -> Player:
        if self.find(user_id):
            raise GameError("شما از قبل سر این میز نشسته‌اید.")
        if self.stage != Stage.WAITING:
            raise GameError("یک دست در حال انجام است؛ تا پایان آن صبر کنید.")
        p = Player(user_id=user_id, name=name, chips=self.starting_stack)
        self.players.append(p)
        return p

    def remove_player(self, user_id: int) -> None:
        p = self.find(user_id)
        if not p:
            raise GameError("شما سر این میز نیستید.")
        if self.stage == Stage.WAITING:
            self.players.remove(p)
        else:
            # Fold them out of the current hand; remove after it ends.
            p.folded = True
            p.sitting_out = True

    def find(self, user_id: int) -> Player | None:
        return next((p for p in self.players if p.user_id == user_id), None)

    @property
    def active_seats(self) -> list[Player]:
        return [p for p in self.players if p.in_hand]

    @property
    def total_pot(self) -> int:
        return sum(p.committed for p in self.players)

    # ------------------------------------------------------------------ #
    # Hand lifecycle
    # ------------------------------------------------------------------ #
    def start_hand(self, variant: str | None = None) -> None:
        eligible = [p for p in self.players if p.chips > 0]
        if len(eligible) < 2:
            raise GameError("برای شروع دست حداقل به ۲ بازیکن با ژتون نیاز است.")
        self.hand_variant = variant or getattr(self, "variant", "holdem")

        # Drop broke players from the table.
        self.players = [p for p in self.players if p.chips > 0]

        for p in self.players:
            p.hole = []
            p.round_bet = 0
            p.committed = 0
            p.folded = False
            p.all_in = False
            p.sitting_out = False

        self.deck = Deck(seed=self._seed)
        self.board = []
        self.stage = Stage.PREFLOP
        self.current_bet = 0
        self.min_raise = self.big_blind
        self.acted = set()
        self.last_aggressor = None

        # Move the button forward (it stays put on the very first hand).
        if hasattr(self, "_hand_started"):
            self.button = (self.button + 1) % len(self.players)
        self._hand_started = True

        self._post_blinds()
        self._deal_holes()

    def _seat_after(self, index: int) -> int:
        n = len(self.players)
        return (index + 1) % n

    def _next_can_act(self, start: int) -> int | None:
        """First seat at or after `start` that can act; None if nobody."""
        n = len(self.players)
        for offset in range(n):
            idx = (start + offset) % n
            if self.players[idx].can_act:
                return idx
        return None

    def _post_blinds(self) -> None:
        n = len(self.players)
        if n == 2:
            sb_seat = self.button          # heads-up: button posts SB
            bb_seat = self._seat_after(self.button)
        else:
            sb_seat = self._seat_after(self.button)
            bb_seat = self._seat_after(sb_seat)

        self._post(self.players[sb_seat], self.small_blind)
        self._post(self.players[bb_seat], self.big_blind)
        self.current_bet = self.big_blind
        self.min_raise = self.big_blind

        # First to act preflop is the seat after the big blind.
        first = self._next_can_act(self._seat_after(bb_seat))
        self.turn_index = first if first is not None else bb_seat

    def _post(self, p: Player, amount: int) -> None:
        amount = min(amount, p.chips)
        p.chips -= amount
        p.round_bet += amount
        p.committed += amount
        if p.chips == 0:
            p.all_in = True

    def _deal_holes(self) -> None:
        assert self.deck is not None
        n = HOLE_CARDS.get(getattr(self, "hand_variant", "holdem"), 2)
        for _ in range(n):
            for p in self.players:
                if p.in_hand:
                    p.hole.append(self.deck.deal_one())

    # ------------------------------------------------------------------ #
    # Betting
    # ------------------------------------------------------------------ #
    @property
    def current_player(self) -> Player | None:
        if self.stage not in (Stage.PREFLOP, Stage.FLOP, Stage.TURN, Stage.RIVER):
            return None
        return self.players[self.turn_index]

    def legal_actions(self, user_id: int) -> dict:
        """Return the legal actions for `user_id` plus useful amounts."""
        p = self.find(user_id)
        cp = self.current_player
        if not p or not cp or cp.user_id != user_id:
            return {}
        to_call = self.current_bet - p.round_bet
        actions: dict = {}
        if to_call <= 0:
            actions[Action.CHECK] = 0
        else:
            actions[Action.CALL] = min(to_call, p.chips)
        # Bet / raise availability.
        if p.chips > max(to_call, 0):
            if self.current_bet == 0:
                actions[Action.BET] = self.min_raise
            else:
                actions[Action.RAISE] = self.current_bet + self.min_raise
        actions[Action.ALL_IN] = p.chips
        actions[Action.FOLD] = 0
        return actions

    def act(self, user_id: int, action: Action, amount: int = 0) -> str:
        """Apply an action for the player whose turn it is. Returns a log line."""
        cp = self.current_player
        if cp is None:
            raise GameError("الان نوبت شرط‌بندی نیست.")
        if cp.user_id != user_id:
            raise GameError("نوبت شما نیست.")

        p = cp
        to_call = self.current_bet - p.round_bet
        msg: str

        if action == Action.FOLD:
            p.folded = True
            msg = f"{p.name} فولد کرد"

        elif action == Action.CHECK:
            if to_call > 0:
                raise GameError("نمی‌توانید چک کنید؛ شرطی برای کال کردن هست.")
            msg = f"{p.name} چک کرد"

        elif action == Action.CALL:
            pay = min(to_call, p.chips)
            if pay <= 0:
                raise GameError("چیزی برای کال کردن نیست.")
            self._post(p, pay)
            tag = " (آل‌این)" if p.all_in else ""
            msg = f"{p.name} کال کرد ({pay}){tag}"

        elif action in (Action.BET, Action.RAISE):
            # `amount` is the new total round_bet the player wants to reach.
            target = amount
            if target <= self.current_bet:
                raise GameError("مبلغ ریز باید بیشتر از شرط فعلی باشد.")
            needed = target - p.round_bet
            if needed > p.chips:
                raise GameError("ژتون کافی ندارید؛ از آل‌این استفاده کنید.")
            raise_size = target - self.current_bet
            if raise_size < self.min_raise and needed < p.chips:
                raise GameError(f"حداقل ریز {self.min_raise} است.")
            self._post(p, needed)
            self.min_raise = max(self.min_raise, raise_size)
            self.current_bet = p.round_bet
            self.last_aggressor = p.user_id
            self.acted = {p.user_id}
            verb = "بت" if action == Action.BET else "ریز"
            msg = f"{p.name} {verb} کرد به {self.current_bet}"
            self.acted.add(p.user_id)
            self._advance_turn()
            return msg

        elif action == Action.ALL_IN:
            pay = p.chips
            if pay <= 0:
                raise GameError("ژتونی برای آل‌این ندارید.")
            new_total = p.round_bet + pay
            self._post(p, pay)
            if new_total > self.current_bet:
                raise_size = new_total - self.current_bet
                self.min_raise = max(self.min_raise, raise_size)
                self.current_bet = new_total
                self.last_aggressor = p.user_id
                self.acted = {p.user_id}
            msg = f"{p.name} آل‌این کرد ({pay})"

        else:
            raise GameError("اکشن نامعتبر.")

        self.acted.add(p.user_id)
        self._advance_turn()
        return msg

    def _advance_turn(self) -> None:
        # If only one player remains in the hand, the hand is over.
        if len(self.active_seats) <= 1:
            self.stage = Stage.HAND_OVER
            return

        if self._round_complete():
            self._advance_stage()
            return

        nxt = self._next_can_act(self._seat_after(self.turn_index))
        if nxt is None:
            self._advance_stage()
        else:
            self.turn_index = nxt

    def _round_complete(self) -> bool:
        actors = [p for p in self.players if p.can_act]
        if len(actors) == 0:
            return True
        # Everyone who can act must have acted and matched the current bet.
        for p in actors:
            if p.user_id not in self.acted:
                return False
            if p.round_bet != self.current_bet:
                return False
        return True

    # ------------------------------------------------------------------ #
    # Stage transitions
    # ------------------------------------------------------------------ #
    def _advance_stage(self) -> None:
        # Reset per-round betting state.
        for p in self.players:
            p.round_bet = 0
        self.current_bet = 0
        self.min_raise = self.big_blind
        self.acted = set()
        self.last_aggressor = None

        assert self.deck is not None
        if self.stage == Stage.PREFLOP:
            self.board += self.deck.deal(3)
            self.stage = Stage.FLOP
        elif self.stage == Stage.FLOP:
            self.board += self.deck.deal(1)
            self.stage = Stage.TURN
        elif self.stage == Stage.TURN:
            self.board += self.deck.deal(1)
            self.stage = Stage.RIVER
        elif self.stage == Stage.RIVER:
            self.stage = Stage.SHOWDOWN
            return

        # If nobody can act anymore (all-ins), keep dealing to showdown.
        actors = [p for p in self.players if p.can_act]
        if len(actors) <= 1 and len(self.active_seats) >= 2:
            # Deal out remaining streets with no betting.
            if self.stage != Stage.SHOWDOWN:
                self._run_out_board()
            return

        first = self._next_can_act(self._seat_after(self.button))
        self.turn_index = first if first is not None else self.button

    def _run_out_board(self) -> None:
        assert self.deck is not None
        while len(self.board) < 5:
            self.board += self.deck.deal(1)
        self.stage = Stage.SHOWDOWN

    # ------------------------------------------------------------------ #
    # Resolution
    # ------------------------------------------------------------------ #
    def _build_pots(self) -> list[Pot]:
        contrib = {p.user_id: p.committed for p in self.players if p.committed > 0}
        pots: list[Pot] = []
        while any(v > 0 for v in contrib.values()):
            level = min(v for v in contrib.values() if v > 0)
            contributors = [uid for uid, v in contrib.items() if v > 0]
            amount = 0
            for uid in contributors:
                contrib[uid] -= level
                amount += level
            eligible = [
                uid for uid in contributors
                if (pl := self.find(uid)) and not pl.folded
            ]
            if pots and pots[-1].eligible == eligible:
                pots[-1].amount += amount
            else:
                pots.append(Pot(amount=amount, eligible=eligible))
        return pots

    def resolve_uncontested(self) -> dict:
        """Award the whole pot to the last remaining player (everyone folded)."""
        remaining = self.active_seats
        assert len(remaining) == 1
        winner = remaining[0]
        amount = self.total_pot
        winner.chips += amount
        self.stage = Stage.HAND_OVER
        return {"winner": winner, "amount": amount}

    def showdown(self) -> ShowdownResult:
        pots = self._build_pots()
        results = []
        revealed: dict[int, list[Card]] = {}

        # Evaluate every contender once.
        omaha = getattr(self, "hand_variant", "holdem") == "omaha"
        scores: dict[int, tuple] = {}
        for p in self.players:
            if not p.folded and p.hole:
                if omaha:
                    score, _ = best_hand_omaha(p.hole, self.board)
                else:
                    score, _ = best_hand(p.hole + self.board)
                scores[p.user_id] = score
                revealed[p.user_id] = p.hole

        for pot in pots:
            contenders = [uid for uid in pot.eligible if uid in scores]
            if not contenders:
                continue
            best = max(scores[uid] for uid in contenders)
            winners = [uid for uid in contenders if scores[uid] == best]
            share = pot.amount // len(winners)
            remainder = pot.amount - share * len(winners)
            pot_winners = []
            for i, uid in enumerate(winners):
                win = self.find(uid)
                give = share + (1 if i < remainder else 0)
                win.chips += give
                pot_winners.append((uid, describe(scores[uid]), give))
            results.append({"amount": pot.amount, "winners": pot_winners})

        self.stage = Stage.HAND_OVER
        return ShowdownResult(pots=results, revealed=revealed)

    def cleanup_after_hand(self) -> None:
        """Remove players who left or busted; ready the table for the next hand."""
        self.players = [
            p for p in self.players if not p.sitting_out and p.chips > 0
        ]
        self.stage = Stage.WAITING
        self.board = []
        for p in self.players:
            p.hole = []
            p.folded = False
            p.all_in = False
            p.round_bet = 0
            p.committed = 0
