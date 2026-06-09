"""Vercel serverless entry point — Telegram webhook for the poker bot.

Telegram POSTs each update here. We rebuild a (updater-less) PTB application,
load the relevant table from Redis, process the update with the existing
handlers, then persist the table back to Redis.

Required env vars on Vercel:
    BOT_TOKEN                  — token from @BotFather
    UPSTASH_REDIS_REST_URL     — from the Upstash Redis dashboard
    UPSTASH_REDIS_REST_TOKEN   — from the Upstash Redis dashboard
    WEBHOOK_SECRET (optional)  — must match Telegram's secret_token header
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# Make the repo root importable (bot.py, poker/, store.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

import bot as botmod
from store import delete_game, load_game, save_game


def build_app() -> Application:
    app = (
        Application.builder()
        .token(os.environ["BOT_TOKEN"])
        .updater(None)      # webhook mode: no polling updater
        .job_queue(None)    # no scheduler needed in serverless
        .build()
    )
    app.add_handler(CommandHandler("start", botmod.cmd_start))
    app.add_handler(CommandHandler("play", botmod.cmd_play))
    app.add_handler(CommandHandler("newtable", botmod.cmd_newtable))
    app.add_handler(CommandHandler("join", botmod.cmd_join))
    app.add_handler(CommandHandler("leave", botmod.cmd_leave))
    app.add_handler(CommandHandler("begin", botmod.cmd_begin))
    app.add_handler(CommandHandler("table", botmod.cmd_table))
    app.add_handler(CommandHandler("chips", botmod.cmd_chips))
    app.add_handler(CommandHandler("cards", botmod.cmd_cards))
    app.add_handler(CallbackQueryHandler(botmod.on_action, pattern=r"^act:"))
    return app


def extract_chat_id(data: dict) -> int | None:
    msg = data.get("message") or data.get("edited_message")
    if msg:
        return msg["chat"]["id"]
    cb = data.get("callback_query")
    if cb:
        cb_data = cb.get("data", "")
        parts = cb_data.split(":")
        if len(parts) >= 2 and parts[0] == "act":
            try:
                return int(parts[1])
            except ValueError:
                pass
        if cb.get("message"):
            return cb["message"]["chat"]["id"]
    return None


async def _process(data: dict) -> None:
    app = build_app()
    await app.initialize()
    try:
        update = Update.de_json(data, app.bot)
        await app.process_update(update)
    finally:
        await app.shutdown()


def handle_update(data: dict) -> None:
    """Load state, process one update, persist state."""
    chat_id = extract_chat_id(data)

    # Reset the in-memory table dict so warm instances never leak state
    # between different chats, then hydrate the one chat we're handling.
    botmod.TABLES.clear()
    existed = False
    if chat_id is not None:
        game = load_game(chat_id)
        if game is not None:
            botmod.TABLES[chat_id] = game
            existed = True

    asyncio.run(_process(data))

    if chat_id is not None:
        if chat_id in botmod.TABLES:
            save_game(chat_id, botmod.TABLES[chat_id])
        elif existed:
            delete_game(chat_id)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (Vercel/BaseHTTPRequestHandler API)
        secret = os.environ.get("WEBHOOK_SECRET")
        if secret and self.headers.get(
                "X-Telegram-Bot-Api-Secret-Token") != secret:
            self.send_response(403)
            self.end_headers()
            return

        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        try:
            handle_update(data)
        except Exception as exc:  # never make Telegram retry on app errors
            import traceback
            traceback.print_exc()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(f"error: {exc}".encode())
            return

        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"poker bot webhook is up")
