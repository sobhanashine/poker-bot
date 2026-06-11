"""Tests for persistent profiles (bankroll escrow) and nearby discovery."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nearby
import profiles
import store
import tables
from poker.game import GameError


class FakeRedis:
    """Minimal in-memory stand-in for the Upstash REST client."""

    def __init__(self):
        self.kv = {}
        self.hashes = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, ex=None):
        self.kv[key] = value

    def delete(self, key):
        self.kv.pop(key, None)

    def exists(self, key):
        return 1 if key in self.kv else 0

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field, None)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(store, "_redis", fake)
    return fake


HOST, GUEST = 111, 222


def bankroll(uid):
    return profiles.load(uid)["chips"]


def test_new_profile_gets_starting_bankroll():
    prof = profiles.get_or_create(HOST, "Ali")
    assert prof["chips"] == profiles.STARTING_BANKROLL
    assert prof["active"] is None


def test_create_debits_host_bankroll():
    table = tables.create_table(HOST, "Ali", starting_stack=1000)
    assert bankroll(HOST) == profiles.STARTING_BANKROLL - 1000
    prof = profiles.load(HOST)
    assert prof["active"] == {"code": table["code"], "stack": 1000}


def test_cannot_buy_in_beyond_bankroll():
    prof = profiles.get_or_create(HOST, "Ali")
    prof["chips"] = 300
    profiles.save(prof)
    with pytest.raises(GameError):
        tables.create_table(HOST, "Ali", starting_stack=1000)
    assert bankroll(HOST) == 300  # nothing was taken


def test_join_debits_and_leave_credits_back():
    table = tables.create_table(HOST, "Ali", starting_stack=1000)
    code = table["code"]
    tables.join_table(code, GUEST, "Sara")
    assert bankroll(GUEST) == profiles.STARTING_BANKROLL - 1000

    tables.leave_table(code, GUEST)
    assert bankroll(GUEST) == profiles.STARTING_BANKROLL
    assert profiles.load(GUEST)["active"] is None


def test_cannot_sit_at_two_tables():
    tables.create_table(HOST, "Ali", starting_stack=1000)
    with pytest.raises(GameError):
        tables.create_table(HOST, "Ali", starting_stack=1000)


def test_losses_and_wins_settle_to_bankroll():
    table = tables.create_table(HOST, "Ali", starting_stack=1000,
                                small_blind=10, big_blind=20)
    code = table["code"]
    tables.join_table(code, GUEST, "Sara")
    tables.start_hand(code, HOST)

    table = tables.load_and_tick(code)
    loser = table["game"].current_player.user_id
    winner = GUEST if loser == HOST else HOST
    tables.act(code, loser, "fold")

    tables.leave_table(code, loser)
    tables.leave_table(code, winner)

    total = bankroll(HOST) + bankroll(GUEST)
    assert total == 2 * profiles.STARTING_BANKROLL  # chips conserved
    assert bankroll(winner) > profiles.STARTING_BANKROLL
    assert bankroll(loser) < profiles.STARTING_BANKROLL

    # Stats recorded for both participants.
    assert profiles.load(winner)["hands_won"] == 1
    assert profiles.load(loser)["hands_played"] == 1


def test_rebuy_capped_by_bankroll():
    table = tables.create_table(HOST, "Ali", starting_stack=1000)
    code = table["code"]
    prof = profiles.load(HOST)
    prof["chips"] = 150  # bankroll smaller than a full top-up
    profiles.save(prof)

    p = table["game"].find(HOST)
    p.chips = 400
    tables._save(code, table)

    table = tables.rebuy(code, HOST)
    assert table["game"].find(HOST).chips == 550  # +150, not +600
    assert bankroll(HOST) == 0

    with pytest.raises(GameError):
        tables.rebuy(code, HOST)  # nothing left to rebuy with


def test_expired_table_refunds_last_snapshot(fake_redis):
    table = tables.create_table(HOST, "Ali", starting_stack=1000)
    fake_redis.delete(f"poker:table:{table['code']}")  # simulate TTL expiry

    prof = profiles.reconcile(profiles.load(HOST))
    assert prof["chips"] == profiles.STARTING_BANKROLL
    assert prof["active"] is None


def test_nearby_discovery_filters_by_distance():
    # Host in central Tehran, geo-visible.
    t = tables.create_table(HOST, "Ali", starting_stack=1000,
                            lat=35.7000, lon=51.4000)
    nearby.update_presence(GUEST, "Sara", 35.7100, 51.4100)

    # Guest ~1.4 km away sees the table and no other players.
    out = nearby.discover(35.7100, 51.4100, exclude_uid=GUEST)
    assert [x["code"] for x in out["tables"]] == [t["code"]]
    assert out["tables"][0]["seats"] == 1
    assert out["players"] == []

    # Someone in Isfahan (~340 km) sees neither.
    far = nearby.discover(32.65, 51.67, exclude_uid=999)
    assert far["tables"] == []
    assert far["players"] == []

    # The host searching sees the waiting guest.
    out2 = nearby.discover(35.7000, 51.4000, exclude_uid=HOST)
    assert [p["name"] for p in out2["players"]] == ["Sara"]


def test_dead_table_pruned_from_nearby(fake_redis):
    t = tables.create_table(HOST, "Ali", starting_stack=1000,
                            lat=35.7, lon=51.4)
    fake_redis.delete(f"poker:table:{t['code']}")

    out = nearby.discover(35.7, 51.4, exclude_uid=GUEST)
    assert out["tables"] == []
    assert nearby.TABLES_KEY not in fake_redis.hashes or \
        t["code"] not in fake_redis.hashes[nearby.TABLES_KEY]
