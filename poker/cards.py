"""Card and deck primitives for Texas Hold'em."""
from __future__ import annotations

import random
from dataclasses import dataclass

# Rank values: 2..14 (Ace high). Used for comparisons.
RANKS = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14,
}
RANK_TO_CHAR = {v: k for k, v in RANKS.items()}

# Suits with their emoji representation for nice Telegram rendering.
SUITS = {
    "s": "♠️",
    "h": "♥️",
    "d": "♦️",
    "c": "♣️",
}


@dataclass(frozen=True)
class Card:
    rank: int  # 2..14
    suit: str  # one of "s", "h", "d", "c"

    def __str__(self) -> str:
        return f"{RANK_TO_CHAR[self.rank]}{SUITS[self.suit]}"

    @property
    def code(self) -> str:
        """Plain ascii code, e.g. 'As', 'Td'."""
        return f"{RANK_TO_CHAR[self.rank]}{self.suit}"


class Deck:
    """A standard 52-card deck that can be shuffled and dealt from."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self.cards: list[Card] = [
            Card(rank, suit) for suit in SUITS for rank in RANKS.values()
        ]
        self.shuffle()

    def shuffle(self) -> None:
        self._rng.shuffle(self.cards)

    def deal(self, n: int = 1) -> list[Card]:
        if n > len(self.cards):
            raise ValueError("Not enough cards left in the deck")
        dealt = self.cards[:n]
        self.cards = self.cards[n:]
        return dealt

    def deal_one(self) -> Card:
        return self.deal(1)[0]

    def __len__(self) -> int:
        return len(self.cards)


def render_cards(cards: list[Card]) -> str:
    """Render a list of cards as a space-separated string."""
    return " ".join(str(c) for c in cards) if cards else "—"
