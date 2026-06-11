"""Tests for the poker engine: evaluator, betting flow, and side pots."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from poker.cards import Card
from poker.evaluator import (
    FLUSH, FOUR_OF_A_KIND, FULL_HOUSE, HIGH_CARD, PAIR, STRAIGHT,
    STRAIGHT_FLUSH, THREE_OF_A_KIND, TWO_PAIR, evaluate,
)
from poker.game import Action, Game, Stage


def c(code: str) -> Card:
    ranks = {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
             "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}
    return Card(ranks[code[0]], code[1])


def hand(codes: str):
    return [c(x) for x in codes.split()]


def test_evaluator_categories():
    assert evaluate(hand("As Ks Qs Js Ts"))[0] == STRAIGHT_FLUSH
    assert evaluate(hand("As Ah Ad Ac Ks"))[0] == FOUR_OF_A_KIND
    assert evaluate(hand("As Ah Ad Ks Kh"))[0] == FULL_HOUSE
    assert evaluate(hand("As Ks Qs Js 9s"))[0] == FLUSH
    assert evaluate(hand("As Kh Qs Js Ts"))[0] == STRAIGHT
    assert evaluate(hand("As Ah Ad Ks Qh"))[0] == THREE_OF_A_KIND
    assert evaluate(hand("As Ah Ks Kh Qd"))[0] == TWO_PAIR
    assert evaluate(hand("As Ah Ks Qh Jd"))[0] == PAIR
    assert evaluate(hand("As Kh Qs Jh 9d"))[0] == HIGH_CARD


def test_wheel_straight():
    # A-2-3-4-5 is the lowest straight (five high).
    score = evaluate(hand("As 2h 3s 4h 5d"))
    assert score[0] == STRAIGHT
    assert score[1] == 5


def test_best_of_seven():
    # Seven cards; best is a flush.
    score = evaluate(hand("As Ks 7s 2s 9s 3h 4d"))
    assert score[0] == FLUSH


def test_compare_hands():
    assert evaluate(hand("As Ah Ad Ks Kh")) > evaluate(hand("As Ks Qs Js 9s"))
    assert evaluate(hand("Ks Kh 2s 2h 3d")) > evaluate(hand("Qs Qh As Kh Jd"))


def test_full_hand_simulation():
    g = Game(chat_id=1, small_blind=10, big_blind=20, starting_stack=1000, seed=42)
    a = g.add_player(1, "Alice")
    b = g.add_player(2, "Bob")
    g.start_hand()

    assert g.stage == Stage.PREFLOP
    # Heads-up: button (Alice) is SB=10, Bob is BB=20.
    assert g.total_pot == 30

    # Alice to act first preflop (button acts first heads-up).
    cp = g.current_player
    assert cp.user_id == 1
    g.act(1, Action.CALL)        # Alice calls to 20
    g.act(2, Action.CHECK)       # Bob checks
    assert g.stage == Stage.FLOP

    # Post-flop both check down each street.
    for _ in range(3):
        g.act(g.current_player.user_id, Action.CHECK)
        g.act(g.current_player.user_id, Action.CHECK)

    assert g.stage == Stage.SHOWDOWN
    res = g.showdown()
    assert sum(p["amount"] for p in res.pots) == 40
    assert a.chips + b.chips == 2000  # chips conserved


def test_fold_ends_hand():
    g = Game(chat_id=1, seed=1)
    g.add_player(1, "A")
    g.add_player(2, "B")
    g.start_hand()
    g.act(g.current_player.user_id, Action.FOLD)
    assert g.stage == Stage.HAND_OVER
    out = g.resolve_uncontested()
    assert out["amount"] == 30


def test_side_pot_all_in():
    g = Game(chat_id=1, small_blind=10, big_blind=20, seed=7)
    short = g.add_player(1, "Short")
    short.chips = 100
    mid = g.add_player(2, "Mid")
    mid.chips = 500
    big = g.add_player(3, "Big")
    big.chips = 500
    g.start_hand()
    total_before = sum(p.chips for p in g.players) + g.total_pot

    # Drive everyone all-in / calling.
    safety = 0
    while g.stage in (Stage.PREFLOP, Stage.FLOP, Stage.TURN, Stage.RIVER):
        cp = g.current_player
        la = g.legal_actions(cp.user_id)
        if Action.ALL_IN in la and cp.user_id == 1:
            g.act(cp.user_id, Action.ALL_IN)
        elif Action.CALL in la:
            g.act(cp.user_id, Action.CALL)
        elif Action.CHECK in la:
            g.act(cp.user_id, Action.CHECK)
        else:
            g.act(cp.user_id, Action.ALL_IN)
        safety += 1
        assert safety < 50

    if g.stage == Stage.SHOWDOWN:
        g.showdown()
    elif g.stage == Stage.HAND_OVER and len(g.active_seats) == 1:
        g.resolve_uncontested()

    total_after = sum(p.chips for p in g.players)
    assert total_after == total_before  # chips conserved across side pots


if __name__ == "__main__":
    import traceback
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(funcs) - failed}/{len(funcs)} passed")
    sys.exit(1 if failed else 0)


def test_omaha_deals_four_hole_cards():
    g = Game(chat_id=1, seed=7, variant="omaha")
    g.add_player(1, "Alice")
    g.add_player(2, "Bob")
    g.start_hand()
    assert all(len(p.hole) == 4 for p in g.players)
    assert g.hand_variant == "omaha"


def test_omaha_per_hand_variant_override():
    # A hold'em table can deal a single Omaha hand (mixed mode).
    g = Game(chat_id=1, seed=7)
    g.add_player(1, "Alice")
    g.add_player(2, "Bob")
    g.start_hand(variant="omaha")
    assert all(len(p.hole) == 4 for p in g.players)
    g.cleanup_after_hand()
    g.start_hand()  # back to the table default
    assert all(len(p.hole) == 2 for p in g.players)
    assert g.hand_variant == "holdem"


def test_omaha_must_use_exactly_two_hole_cards():
    from poker.evaluator import best_hand_omaha
    # Four spades on the board, only one in the hole: hold'em rules would
    # make a flush, Omaha rules (exactly 2 hole cards) must not.
    hole = hand("As 2h 7d 8c")
    board = hand("Ks Qs Js 3s 9h")
    assert evaluate(hole[:1] + board)[0] == FLUSH  # hold'em-style: flush
    score, cards = best_hand_omaha(hole, board)
    assert score[0] == HIGH_CARD
    assert len(cards) == 5
    assert sum(1 for c in cards if c in hole) == 2
    assert sum(1 for c in cards if c in board) == 3


def test_omaha_pair_in_hole_plays():
    from poker.evaluator import best_hand_omaha
    hole = hand("Ah Ad 7c 2s")
    board = hand("Ac Kh 9d 5s 3h")
    score, _ = best_hand_omaha(hole, board)
    assert score[0] == THREE_OF_A_KIND
