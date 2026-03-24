import logging

import telebot
from apscheduler.schedulers.background import BackgroundScheduler

import db
from config import POLL_INTERVAL_MINUTES, TELEGRAM_BOT_TOKEN
from notifiers import telegram as tg
from watchers.airtable import AirtableWatcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# ── Registered watchers ───────────────────────────────────────────────────────
# Add new watcher instances here to monitor additional sources.
WATCHERS = [
    AirtableWatcher(),
]


# ── Telegram bot command handlers ─────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def handle_start(message: telebot.types.Message) -> None:
    chat_id = message.chat.id
    username = getattr(message.from_user, "username", None)
    db.add_chat_id(chat_id, username)
    bot.reply_to(
        message,
        "✅ *Subscribed!*\n\n"
        "I'll ping you whenever a new RFP appears.\n\n"
        "Commands:\n"
        "• /stop — unsubscribe\n"
        "• /status — check bot health",
        parse_mode="Markdown",
    )
    logger.info("New subscriber: chat_id=%s username=@%s", chat_id, username)


@bot.message_handler(commands=["stop"])
def handle_stop(message: telebot.types.Message) -> None:
    db.remove_chat_id(message.chat.id)
    bot.reply_to(message, "You've been unsubscribed. Send /start to re-subscribe.")


@bot.message_handler(commands=["status"])
def handle_status(message: telebot.types.Message) -> None:
    count = len(db.get_chat_ids())
    bot.reply_to(
        message,
        f"🟢 *Bot is running*\n"
        f"👥 Subscribers: {count}\n"
        f"⏱ Poll interval: every {POLL_INTERVAL_MINUTES} min",
        parse_mode="Markdown",
    )


# ── Watcher engine ────────────────────────────────────────────────────────────

def run_watchers() -> None:
    logger.info("Running watcher checks…")
    chat_ids = db.get_chat_ids()

    for watcher in WATCHERS:
        try:
            items = watcher.fetch_items()
            seen = db.get_all_seen(watcher.watcher_id)
            is_first_run = len(seen) == 0
            new_items = [item for item in items if item.id not in seen]

            if is_first_run:
                # Seed state on first run — don't flood with old records
                for item in new_items:
                    db.mark_seen(watcher.watcher_id, item.id)
                logger.info(
                    "[%s] First run: seeded %d existing record(s) — no notifications sent.",
                    watcher.label,
                    len(new_items),
                )
                continue

            if not new_items:
                logger.info("[%s] No new items.", watcher.label)
                continue

            for item in new_items:
                db.mark_seen(watcher.watcher_id, item.id)
                if chat_ids:
                    msg = f"🆕 *New listing — {watcher.label}*\n\n{item.format_message()}"
                    tg.broadcast(chat_ids, msg)
                    logger.info("[%s] Notified: %s", watcher.label, item.title)

        except Exception:
            logger.exception("[%s] Watcher failed.", watcher.watcher_id)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    db.init_db()
    logger.info("Database ready.")

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(run_watchers, "interval", minutes=POLL_INTERVAL_MINUTES)
    scheduler.start()
    logger.info("Scheduler started — polling every %d minutes.", POLL_INTERVAL_MINUTES)

    # Check immediately on startup so you don't wait for first interval
    run_watchers()

    logger.info("Starting Telegram bot (long-polling)…")
    bot.infinity_polling(timeout=30, long_polling_timeout=25)


if __name__ == "__main__":
    main()
