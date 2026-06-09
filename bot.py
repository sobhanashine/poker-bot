"""Telegram Texas Hold'em poker bot.

Run a multiplayer No-Limit Hold'em table inside any Telegram group chat.
Hole cards are delivered to each player via private message; betting happens
through inline buttons in the group.

Usage:
    BOT_TOKEN=123:abc python bot.py
"""
from __future__ import annotations

import logging
import os

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from poker.cards import render_cards
from poker.game import Action, Game, GameError, Stage

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("pokerbot")

# chat_id -> Game
TABLES: dict[int, Game] = {}

ACTION_LABELS = {
    Action.FOLD: "🃏 فولد",
    Action.CHECK: "✅ چک",
    Action.CALL: "📞 کال",
    Action.BET: "💰 بت",
    Action.RAISE: "⬆️ ریز",
    Action.ALL_IN: "🔥 آل‌این",
}


# ---------------------------------------------------------------------------#
# Helpers
# ---------------------------------------------------------------------------#
def get_table(chat_id: int) -> Game | None:
    return TABLES.get(chat_id)


def player_label(p) -> str:
    return p.name


def table_summary(game: Game) -> str:
    lines = [f"🎴 *میز پوکر* — بلایندها {game.small_blind}/{game.big_blind}"]
    if not game.players:
        lines.append("\nهنوز بازیکنی ننشسته. با /join وارد شوید.")
        return "\n".join(lines)
    lines.append("\n*بازیکنان:*")
    for i, p in enumerate(game.players):
        btn = " 🔘" if i == game.button and game.stage != Stage.WAITING else ""
        status = ""
        if p.folded:
            status = " (فولد)"
        elif p.all_in:
            status = " (آل‌این)"
        lines.append(f"• {p.name}: {p.chips} ژتون{btn}{status}")
    return "\n".join(lines)


def board_text(game: Game) -> str:
    stage_names = {
        Stage.PREFLOP: "پری‌فلاپ",
        Stage.FLOP: "فلاپ",
        Stage.TURN: "ترن",
        Stage.RIVER: "ریور",
        Stage.SHOWDOWN: "شوداون",
    }
    parts = [
        f"🃏 *{stage_names.get(game.stage, '')}*",
        f"میز: {render_cards(game.board)}",
        f"💵 پات: {game.total_pot}",
    ]
    return "\n".join(parts)


def action_keyboard(game: Game) -> InlineKeyboardMarkup | None:
    cp = game.current_player
    if cp is None:
        return None
    la = game.legal_actions(cp.user_id)
    cid = game.chat_id
    row1, row2 = [], []
    if Action.CHECK in la:
        row1.append(InlineKeyboardButton(
            ACTION_LABELS[Action.CHECK], callback_data=f"act:{cid}:check:0"))
    if Action.CALL in la:
        row1.append(InlineKeyboardButton(
            f"{ACTION_LABELS[Action.CALL]} {la[Action.CALL]}",
            callback_data=f"act:{cid}:call:0"))
    row1.append(InlineKeyboardButton(
        ACTION_LABELS[Action.FOLD], callback_data=f"act:{cid}:fold:0"))

    # Raise / bet sizing buttons.
    if Action.BET in la or Action.RAISE in la:
        base = la.get(Action.BET, la.get(Action.RAISE))
        pot = game.total_pot
        half_pot = max(base, game.current_bet + pot // 2)
        full_pot = max(base, game.current_bet + pot)
        verb = "bet" if Action.BET in la else "raise"
        cp_chips_total = cp.round_bet + cp.chips
        options = []
        seen = set()
        for label, amt in [("Min", base), ("½ پات", half_pot), ("پات", full_pot)]:
            amt = min(amt, cp_chips_total)
            if amt not in seen and amt < cp_chips_total:
                options.append(InlineKeyboardButton(
                    f"{label} {amt}", callback_data=f"act:{cid}:{verb}:{amt}"))
                seen.add(amt)
        row2.extend(options)

    if Action.ALL_IN in la:
        row2.append(InlineKeyboardButton(
            f"{ACTION_LABELS[Action.ALL_IN]} {la[Action.ALL_IN]}",
            callback_data=f"act:{cid}:all_in:0"))

    rows = [r for r in (row1, row2) if r]
    return InlineKeyboardMarkup(rows)


async def send_hole_cards(context: ContextTypes.DEFAULT_TYPE, game: Game) -> list[str]:
    """DM each player their hole cards. Returns names we could not reach."""
    unreachable = []
    for p in game.players:
        if not p.hole:
            continue
        try:
            await context.bot.send_message(
                chat_id=p.user_id,
                text=(f"🎴 *دست شما* ({game.chat_id})\n"
                      f"{render_cards(p.hole)}"),
                parse_mode=ParseMode.MARKDOWN,
            )
        except (Forbidden, TelegramError):
            unreachable.append(p.name)
    return unreachable


async def prompt_turn(context: ContextTypes.DEFAULT_TYPE, game: Game) -> None:
    cp = game.current_player
    if cp is None:
        return
    text = (f"{board_text(game)}\n\n"
            f"نوبت *{cp.name}* است. (برای کال: {game.current_bet - cp.round_bet})")
    await context.bot.send_message(
        chat_id=game.chat_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=action_keyboard(game),
    )


async def announce(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    await context.bot.send_message(
        chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------#
# Command handlers
# ---------------------------------------------------------------------------#
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎴 *بات پوکر تگزاس هولدم*\n\n"
        "این بات را در یک گروه اضافه کنید و بازی را شروع کنید:\n"
        "• /newtable — ساخت میز جدید\n"
        "• /join — نشستن سر میز\n"
        "• /leave — ترک میز\n"
        "• /begin — شروع دست (پخش کارت‌ها)\n"
        "• /table — وضعیت میز\n"
        "• /cards — ارسال مجدد کارت‌های شما (پیام خصوصی)\n"
        "• /chips — موجودی ژتون شما\n\n"
        "⚠️ قبل از بازی حتماً همین‌جا در چت خصوصی /start را بزنید تا "
        "بتوانم کارت‌هایتان را برایتان بفرستم.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_newtable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat.type == "private":
        await update.message.reply_text("میز باید در یک گروه ساخته شود.")
        return
    if chat.id in TABLES and TABLES[chat.id].stage != Stage.WAITING:
        await update.message.reply_text("یک دست در حال انجام است.")
        return
    sb, bb = 10, 20
    if context.args and len(context.args) >= 2:
        try:
            sb, bb = int(context.args[0]), int(context.args[1])
        except ValueError:
            pass
    TABLES[chat.id] = Game(chat_id=chat.id, small_blind=sb, big_blind=bb)
    await update.message.reply_text(
        f"✅ میز ساخته شد (بلایند {sb}/{bb}).\nبا /join وارد شوید.",
    )


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if chat.type == "private":
        await update.message.reply_text("لطفاً در گروه به میز بپیوندید.")
        return
    game = TABLES.get(chat.id)
    if not game:
        game = TABLES[chat.id] = Game(chat_id=chat.id)
    try:
        game.add_player(user.id, user.full_name)
    except GameError as e:
        await update.message.reply_text(str(e))
        return
    await update.message.reply_text(
        f"🪑 {user.full_name} سر میز نشست ({len(game.players)} بازیکن).")


async def cmd_leave(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    game = TABLES.get(update.effective_chat.id)
    if not game:
        await update.message.reply_text("میزی وجود ندارد.")
        return
    try:
        game.remove_player(update.effective_user.id)
    except GameError as e:
        await update.message.reply_text(str(e))
        return
    await update.message.reply_text(f"👋 {update.effective_user.full_name} میز را ترک کرد.")


async def cmd_table(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    game = TABLES.get(update.effective_chat.id)
    if not game:
        await update.message.reply_text("میزی وجود ندارد. /newtable را بزنید.")
        return
    await update.message.reply_text(
        table_summary(game), parse_mode=ParseMode.MARKDOWN)


async def cmd_chips(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    game = TABLES.get(update.effective_chat.id)
    if not game:
        await update.message.reply_text("میزی وجود ندارد.")
        return
    p = game.find(update.effective_user.id)
    if not p:
        await update.message.reply_text("شما سر این میز نیستید.")
        return
    await update.message.reply_text(f"💰 موجودی شما: {p.chips} ژتون")


async def cmd_cards(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    game = TABLES.get(update.effective_chat.id)
    if not game or game.stage == Stage.WAITING:
        await update.message.reply_text("دستی در جریان نیست.")
        return
    p = game.find(update.effective_user.id)
    if not p or not p.hole:
        await update.message.reply_text("کارتی برای شما وجود ندارد.")
        return
    try:
        await context.bot.send_message(
            chat_id=p.user_id,
            text=f"🎴 دست شما: {render_cards(p.hole)}",
        )
    except (Forbidden, TelegramError):
        await update.message.reply_text(
            "نتوانستم پیام خصوصی بفرستم. ابتدا در چت خصوصی به من /start بدهید.")


async def cmd_begin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    game = TABLES.get(chat.id)
    if not game:
        await update.message.reply_text("میزی وجود ندارد. /newtable را بزنید.")
        return
    try:
        game.start_hand()
    except GameError as e:
        await update.message.reply_text(str(e))
        return

    unreachable = await send_hole_cards(context, game)
    if unreachable:
        await announce(
            context, chat.id,
            "⚠️ نتوانستم برای این بازیکنان کارت بفرستم (باید در چت خصوصی "
            f"/start بزنند): {', '.join(unreachable)}")

    sb = game.players[(game.button if len(game.players) == 2
                       else (game.button + 1) % len(game.players))]
    await announce(
        context, chat.id,
        f"🎬 دست جدید شروع شد!\n🔘 دیلر: {game.players[game.button].name}\n"
        f"💵 بلایندها پرداخت شد. پات: {game.total_pot}")
    await prompt_turn(context, game)


# ---------------------------------------------------------------------------#
# Action callback
# ---------------------------------------------------------------------------#
async def on_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        _, chat_id_s, action_s, amount_s = query.data.split(":")
        chat_id = int(chat_id_s)
        amount = int(amount_s)
    except ValueError:
        return

    game = TABLES.get(chat_id)
    if not game:
        await query.answer("میز پیدا نشد.", show_alert=True)
        return
    cp = game.current_player
    if cp is None:
        return
    if query.from_user.id != cp.user_id:
        await query.answer("نوبت شما نیست.", show_alert=True)
        return

    try:
        action = Action(action_s)
        stage_before = game.stage
        board_before = len(game.board)
        log = game.act(query.from_user.id, action, amount)
    except (GameError, ValueError) as e:
        await query.answer(str(e), show_alert=True)
        return

    # Remove the buttons from the message that was just acted on.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass

    await announce(context, chat_id, f"▶️ {log}")
    await progress_game(context, game, stage_before, board_before)


async def progress_game(context, game: Game, stage_before: Stage,
                        board_before: int) -> None:
    """Drive the game forward after an action and render the new state."""
    # Hand ended because everyone else folded.
    if game.stage == Stage.HAND_OVER and len(game.active_seats) == 1:
        out = game.resolve_uncontested()
        await announce(
            context, game.chat_id,
            f"🏆 {out['winner'].name} پات {out['amount']} را برد "
            f"(بقیه فولد کردند).")
        await end_hand(context, game)
        return

    # New community cards were dealt — announce the street.
    if game.stage != stage_before and len(game.board) != board_before:
        await announce(context, game.chat_id, board_text(game))

    if game.stage == Stage.SHOWDOWN:
        await do_showdown(context, game)
        return

    if game.current_player is not None:
        await prompt_turn(context, game)


async def do_showdown(context, game: Game) -> None:
    result = game.showdown()
    lines = ["🃏 *شوداون*", f"میز: {render_cards(game.board)}", ""]
    for uid, hole in result.revealed.items():
        p = game.find(uid)
        if p:
            lines.append(f"{p.name}: {render_cards(hole)}")
    lines.append("")
    for i, pot in enumerate(result.pots):
        tag = "پات اصلی" if i == 0 else f"ساید‌پات {i}"
        for uid, desc, amt in pot["winners"]:
            p = game.find(uid)
            lines.append(f"🏆 {p.name} {amt} از {tag} را برد — {desc}")
    await announce(context, game.chat_id, "\n".join(lines))
    await end_hand(context, game)


async def end_hand(context, game: Game) -> None:
    game.cleanup_after_hand()
    if len(game.players) >= 2:
        await announce(
            context, game.chat_id,
            "دست تمام شد. برای دست بعدی /begin را بزنید "
            "(یا /join برای ورود بازیکن جدید).")
    else:
        await announce(
            context, game.chat_id,
            "بازی تمام شد — بازیکن کافی نیست. /join برای میز جدید.")


# ---------------------------------------------------------------------------#
# Entry point
# ---------------------------------------------------------------------------#
def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit(
            "BOT_TOKEN تنظیم نشده. توکن را از @BotFather بگیرید و اجرا کنید:\n"
            "  BOT_TOKEN=xxxx python bot.py")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("newtable", cmd_newtable))
    app.add_handler(CommandHandler("join", cmd_join))
    app.add_handler(CommandHandler("leave", cmd_leave))
    app.add_handler(CommandHandler("begin", cmd_begin))
    app.add_handler(CommandHandler("table", cmd_table))
    app.add_handler(CommandHandler("chips", cmd_chips))
    app.add_handler(CommandHandler("cards", cmd_cards))
    app.add_handler(CallbackQueryHandler(on_action, pattern=r"^act:"))

    logger.info("Poker bot is starting (long polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
