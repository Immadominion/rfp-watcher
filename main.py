import logging
import re
from datetime import datetime

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

_MAX_MESSAGE_LEN = 3500
_MAX_FIELD_LEN = 280
_OPEN_STATUS = "open"
_CLOSED_STATUS = "closed"
_UNKNOWN_STATUS = "unknown"
_KNOWN_STATUS_FIELDS = ("RFP Status",)
_KNOWN_DEADLINE_FIELDS = ("Application Deadline", "Submission Deadline", "Deadline")
_STATUS_KEYWORDS = ("status", "submission", "state", "accepting", "open", "close")
_DEADLINE_KEYWORDS = ("deadline", "due", "close", "closing", "submission")
_OPEN_HINTS = ("open", "accepting", "active", "live", "current", "ongoing")
_CLOSED_HINTS = ("closed", "expired", "ended", "complete", "completed", "inactive")
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d/%m/%Y",
    "%d/%m/%y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
)

# ── Registered watchers ───────────────────────────────────────────────────────
# Add new watcher instances here to monitor additional sources.
WATCHERS = [
    AirtableWatcher(),
]


def configure_bot_commands() -> None:
    bot.set_my_commands([
        telebot.types.BotCommand("start", "Subscribe to new RFP alerts"),
        telebot.types.BotCommand("rfps", "List all current RFPs"),
        telebot.types.BotCommand("open", "List open RFPs"),
        telebot.types.BotCommand("closed", "List closed RFPs"),
        telebot.types.BotCommand("status", "Show bot health"),
        telebot.types.BotCommand("stop", "Unsubscribe from alerts"),
        telebot.types.BotCommand("help", "Show available commands"),
    ])


def _help_text() -> str:
    return (
        "Available commands:\n"
        "/start - subscribe to new RFP alerts\n"
        "/rfps - list every current RFP\n"
        "/open - list RFPs still open for submissions\n"
        "/closed - list RFPs that are closed for submissions\n"
        "/status - check bot health\n"
        "/stop - unsubscribe\n"
        "/help - show this command list"
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _truncate(value: str, limit: int = _MAX_FIELD_LEN) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3].rstrip()}..."


def _find_matching_metadata(item, keywords: tuple[str, ...]) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    for key, value in item.metadata.items():
        haystack = f"{key} {value}".lower()
        if any(keyword in haystack for keyword in keywords):
            matches.append((key, str(value)))
    return matches


def _parse_date(value: str) -> datetime | None:
    raw = value.strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue

    for match in re.findall(r"[A-Za-z]{3,9} \d{1,2}, \d{4}|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}", raw):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(match, fmt)
            except ValueError:
                continue

    return None


def _extract_deadline(item) -> tuple[str, datetime] | None:
    for field in _KNOWN_DEADLINE_FIELDS:
        value = item.metadata.get(field)
        if not value:
            continue
        parsed = _parse_date(str(value))
        if parsed is not None:
            return field, parsed

    for key, value in _find_matching_metadata(item, _DEADLINE_KEYWORDS):
        parsed = _parse_date(value)
        if parsed is not None:
            return key, parsed
    return None


def _classify_item_status(item) -> str:
    for field in _KNOWN_STATUS_FIELDS:
        value = item.metadata.get(field)
        if not value:
            continue
        combined = _normalize_text(f"{field} {value}")
        if any(hint in combined for hint in _CLOSED_HINTS):
            return _CLOSED_STATUS
        if any(hint in combined for hint in _OPEN_HINTS):
            return _OPEN_STATUS

    for key, value in _find_matching_metadata(item, _STATUS_KEYWORDS):
        combined = _normalize_text(f"{key} {value}")
        if any(hint in combined for hint in _CLOSED_HINTS):
            return _CLOSED_STATUS
        if any(hint in combined for hint in _OPEN_HINTS):
            return _OPEN_STATUS

    deadline = _extract_deadline(item)
    if deadline is None:
        return _UNKNOWN_STATUS

    _, parsed = deadline
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    return _OPEN_STATUS if parsed >= now else _CLOSED_STATUS


def _status_label(status: str) -> str:
    if status == _OPEN_STATUS:
        return "Open for submissions"
    if status == _CLOSED_STATUS:
        return "Closed for submissions"
    return "Status unclear"


def _summary_metadata_lines(item) -> list[str]:
    lines: list[str] = []
    deadline = _extract_deadline(item)
    if deadline is not None:
        key, parsed = deadline
        lines.append(f"Deadline: {parsed.strftime('%Y-%m-%d')} ({key})")

    skip_keys = {"link", "url", "project link"}
    if deadline is not None:
        skip_keys.add(deadline[0].lower())

    preferred_fragments = (
        "organization",
        "company",
        "sponsor",
        "category",
        "type",
        "budget",
        "prize",
        "track",
        "region",
        "location",
    )

    selected: list[tuple[str, str]] = []
    for key, value in item.metadata.items():
        lowered = key.lower()
        if lowered in skip_keys:
            continue
        if any(fragment in lowered for fragment in preferred_fragments):
            selected.append((key, str(value)))

    if len(selected) < 3:
        for key, value in item.metadata.items():
            lowered = key.lower()
            if lowered in skip_keys:
                continue
            entry = (key, str(value))
            if entry not in selected:
                selected.append(entry)
            if len(selected) >= 3:
                break

    for key, value in selected[:3]:
        lines.append(f"{key}: {_truncate(value)}")

    return lines


def _format_item_summary(index: int, item) -> str:
    lines = [f"{index}. {_truncate(item.title, 140)}", f"Status: {_status_label(_classify_item_status(item))}"]
    lines.extend(_summary_metadata_lines(item))
    if item.url:
        lines.append(f"Link: {_truncate(item.url, 220)}")
    return "\n".join(lines)


def _send_chunked_message(chat_id: int, header: str, bodies: list[str]) -> None:
    current = header
    for body in bodies:
        candidate = f"{current}\n\n{body}" if current else body
        if len(candidate) <= _MAX_MESSAGE_LEN:
            current = candidate
            continue

        bot.send_message(chat_id, current)
        current = body

    if current:
        bot.send_message(chat_id, current)


def _send_rfp_listing(message: telebot.types.Message, filter_status: str | None = None) -> None:
    watcher = WATCHERS[0]

    try:
        items = watcher.fetch_items()
    except Exception:
        logger.exception("Failed to fetch items for listing command.")
        bot.reply_to(message, "I couldn't fetch the RFP list right now. Try again in a minute.")
        return

    classified_items = [(item, _classify_item_status(item)) for item in items]
    if filter_status is not None:
        classified_items = [entry for entry in classified_items if entry[1] == filter_status]

    classified_items.sort(
        key=lambda entry: (dl[1] if (dl := _extract_deadline(entry[0])) else datetime.max)
    )

    if not classified_items:
        empty_message = "No RFPs matched that filter right now."
        if filter_status == _UNKNOWN_STATUS:
            empty_message = "No RFPs with unclear status right now."
        bot.reply_to(message, empty_message)
        return

    if filter_status == _OPEN_STATUS:
        heading = f"Open RFPs: {len(classified_items)}"
    elif filter_status == _CLOSED_STATUS:
        heading = f"Closed RFPs: {len(classified_items)}"
    else:
        heading = f"All current RFPs: {len(classified_items)}"

    bodies = [_format_item_summary(index, item) for index, (item, _) in enumerate(classified_items, start=1)]
    _send_chunked_message(message.chat.id, heading, bodies)


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
        "• /rfps — list all current RFPs\n"
        "• /open — list open RFPs\n"
        "• /closed — list closed RFPs\n"
        "• /stop — unsubscribe\n"
        "• /status — check bot health\n"
        "• /help — show all commands",
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


@bot.message_handler(commands=["rfps"])
def handle_rfps(message: telebot.types.Message) -> None:
    _send_rfp_listing(message)


@bot.message_handler(commands=["open"])
def handle_open_rfps(message: telebot.types.Message) -> None:
    _send_rfp_listing(message, _OPEN_STATUS)


@bot.message_handler(commands=["closed"])
def handle_closed_rfps(message: telebot.types.Message) -> None:
    _send_rfp_listing(message, _CLOSED_STATUS)


@bot.message_handler(commands=["help"])
def handle_help(message: telebot.types.Message) -> None:
    bot.reply_to(message, _help_text())


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
    try:
        configure_bot_commands()
    except Exception:
        logger.warning("Could not register bot commands with Telegram (non-fatal).")

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
