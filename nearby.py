"""Location-based discovery: find tables and players near you.

Players who opt in share their location from the Mini App. We keep two Redis
hashes (small, periodically pruned on read):

    poker:nearby:tables   code -> {lat, lon, ts, host, sb, bb, buy_in}
    poker:nearby:players  uid  -> {lat, lon, ts, name}

Distance is computed server-side with the haversine formula — clients only
ever learn coarse distances (km), never another user's coordinates.
"""
from __future__ import annotations

import json
import math
import time

import store

TABLES_KEY = "poker:nearby:tables"
PLAYERS_KEY = "poker:nearby:players"

RADIUS_KM = 30          # how far "nearby" reaches
PRESENCE_TTL = 1800     # players drop off the radar after 30 min
TABLE_AD_TTL = 12 * 3600  # matches the table TTL in tables.py


def _now() -> int:
    return int(time.time())


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rad = math.radians
    dlat = rad(lat2 - lat1)
    dlon = rad(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(dlon / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_table(code: str, lat: float, lon: float, host: str,
                   small_blind: int, big_blind: int, buy_in: int) -> None:
    entry = {"lat": lat, "lon": lon, "ts": _now(), "host": host,
             "sb": small_blind, "bb": big_blind, "buy_in": buy_in}
    store._client().hset(TABLES_KEY, code, json.dumps(entry, ensure_ascii=False))


def unregister_table(code: str) -> None:
    store._client().hdel(TABLES_KEY, code)


def update_presence(uid: int, name: str, lat: float, lon: float) -> None:
    entry = {"lat": lat, "lon": lon, "ts": _now(), "name": name}
    store._client().hset(PLAYERS_KEY, str(uid), json.dumps(entry, ensure_ascii=False))


def clear_presence(uid: int) -> None:
    store._client().hdel(PLAYERS_KEY, str(uid))


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def discover(lat: float, lon: float, exclude_uid: int,
             radius_km: float = RADIUS_KM) -> dict:
    """Return nearby open tables and waiting players, pruning stale entries."""
    import tables as tables_mod  # late import: tables.py imports this module

    client = store._client()
    now = _now()

    found_tables = []
    raw_tables = client.hgetall(TABLES_KEY) or {}
    for code, raw in raw_tables.items():
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            client.hdel(TABLES_KEY, code)
            continue
        if now - entry.get("ts", 0) > TABLE_AD_TTL:
            client.hdel(TABLES_KEY, code)
            continue
        dist = _haversine_km(lat, lon, entry["lat"], entry["lon"])
        if dist > radius_km:
            continue
        table = tables_mod._load(code)
        if table is None or not table["game"].players:
            client.hdel(TABLES_KEY, code)
            continue
        seats = len(table["game"].players)
        if seats >= tables_mod.MAX_SEATS:
            continue  # full right now; keep the ad, hide from results
        found_tables.append({
            "code": code,
            "host": entry.get("host", ""),
            "small_blind": entry.get("sb"),
            "big_blind": entry.get("bb"),
            "buy_in": entry.get("buy_in"),
            "seats": seats,
            "max_seats": tables_mod.MAX_SEATS,
            "distance_km": round(dist, 1),
        })

    found_players = []
    raw_players = client.hgetall(PLAYERS_KEY) or {}
    for uid_s, raw in raw_players.items():
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            client.hdel(PLAYERS_KEY, uid_s)
            continue
        if now - entry.get("ts", 0) > PRESENCE_TTL:
            client.hdel(PLAYERS_KEY, uid_s)
            continue
        if str(uid_s) == str(exclude_uid):
            continue
        dist = _haversine_km(lat, lon, entry["lat"], entry["lon"])
        if dist > radius_km:
            continue
        found_players.append({
            "name": entry.get("name", "?"),
            "distance_km": round(dist, 1),
        })

    found_tables.sort(key=lambda t: t["distance_km"])
    found_players.sort(key=lambda p: p["distance_km"])
    return {"tables": found_tables, "players": found_players,
            "radius_km": radius_km}
