"""Poker hand evaluation: pick the best 5-card hand out of 7 cards."""
from __future__ import annotations

from collections import Counter
from itertools import combinations

from .cards import Card, RANK_TO_CHAR

# Hand category rankings (higher is better).
HIGH_CARD = 0
PAIR = 1
TWO_PAIR = 2
THREE_OF_A_KIND = 3
STRAIGHT = 4
FLUSH = 5
FULL_HOUSE = 6
FOUR_OF_A_KIND = 7
STRAIGHT_FLUSH = 8

CATEGORY_NAMES = {
    HIGH_CARD: "High Card",
    PAIR: "Pair",
    TWO_PAIR: "Two Pair",
    THREE_OF_A_KIND: "Three of a Kind",
    STRAIGHT: "Straight",
    FLUSH: "Flush",
    FULL_HOUSE: "Full House",
    FOUR_OF_A_KIND: "Four of a Kind",
    STRAIGHT_FLUSH: "Straight Flush",
}

# A score is a tuple: (category, tiebreakers...). Larger tuples win.
HandScore = tuple


def _straight_high(ranks: list[int]) -> int | None:
    """Given sorted-desc unique ranks, return the high card of a straight, or None.

    Handles the wheel (A-2-3-4-5) where Ace plays low.
    """
    unique = sorted(set(ranks), reverse=True)
    # Ace can be low for the wheel.
    if 14 in unique:
        unique.append(1)
    run = 1
    for i in range(1, len(unique)):
        if unique[i] == unique[i - 1] - 1:
            run += 1
            if run >= 5:
                return unique[i] + 4
        else:
            run = 1
    return None


def _score_5(cards: list[Card]) -> HandScore:
    """Score exactly 5 cards."""
    ranks = sorted((c.rank for c in cards), reverse=True)
    suits = [c.suit for c in cards]
    is_flush = len(set(suits)) == 1
    straight_high = _straight_high(ranks)

    counts = Counter(ranks)
    # Sort by (count, rank) descending so pairs/trips come first.
    by_count = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    count_pattern = tuple(c for _, c in by_count)
    ordered_ranks = tuple(r for r, _ in by_count)

    if is_flush and straight_high:
        return (STRAIGHT_FLUSH, straight_high)
    if count_pattern == (4, 1):
        return (FOUR_OF_A_KIND, ordered_ranks[0], ordered_ranks[1])
    if count_pattern == (3, 2):
        return (FULL_HOUSE, ordered_ranks[0], ordered_ranks[1])
    if is_flush:
        return (FLUSH, *ranks)
    if straight_high:
        return (STRAIGHT, straight_high)
    if count_pattern == (3, 1, 1):
        return (THREE_OF_A_KIND, ordered_ranks[0], ordered_ranks[1], ordered_ranks[2])
    if count_pattern == (2, 2, 1):
        return (TWO_PAIR, ordered_ranks[0], ordered_ranks[1], ordered_ranks[2])
    if count_pattern == (2, 1, 1, 1):
        return (PAIR, *ordered_ranks)
    return (HIGH_CARD, *ranks)


def evaluate(cards: list[Card]) -> HandScore:
    """Return the best 5-card score from 5, 6, or 7 cards."""
    if len(cards) < 5:
        raise ValueError("Need at least 5 cards to evaluate")
    if len(cards) == 5:
        return _score_5(cards)
    return max(_score_5(list(combo)) for combo in combinations(cards, 5))


def best_hand(cards: list[Card]) -> tuple[HandScore, list[Card]]:
    """Return (score, the 5 cards) of the best hand."""
    best_combo = max(combinations(cards, 5), key=_score_5)
    return _score_5(list(best_combo)), list(best_combo)


def best_hand_omaha(hole: list[Card], board: list[Card]) -> tuple[HandScore, list[Card]]:
    """Omaha rules: the hand must use exactly 2 hole cards and 3 board cards."""
    best_score: HandScore | None = None
    best_cards: list[Card] = []
    for hp in combinations(hole, 2):
        for bp in combinations(board, 3):
            five = list(hp) + list(bp)
            score = _score_5(five)
            if best_score is None or score > best_score:
                best_score, best_cards = score, five
    if best_score is None:
        raise ValueError("Omaha evaluation needs hole and board cards")
    return best_score, best_cards


def describe(score: HandScore) -> str:
    """Human-readable name of a hand category, with primary rank when useful."""
    category = score[0]
    name = CATEGORY_NAMES[category]
    if category in (PAIR, THREE_OF_A_KIND, FOUR_OF_A_KIND):
        return f"{name} of {RANK_TO_CHAR[score[1]]}s"
    if category == FULL_HOUSE:
        return f"{name}, {RANK_TO_CHAR[score[1]]}s over {RANK_TO_CHAR[score[2]]}s"
    if category == TWO_PAIR:
        return f"{name}, {RANK_TO_CHAR[score[1]]}s and {RANK_TO_CHAR[score[2]]}s"
    if category in (STRAIGHT, STRAIGHT_FLUSH):
        return f"{name} to {RANK_TO_CHAR[score[1]]}"
    return name
