# rfp-watcher

A lightweight Telegram bot that polls configured sources (starting with an Airtable)
for new listings and messages you the moment one appears.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      rfp-watcher                        │
│                                                         │
│  APScheduler (every N min)                              │
│        │                                                │
│        ▼                                                │
│  ┌─────────────────────────────┐                        │
│  │        Watcher Engine       │                        │
│  │  ┌─────────────────────┐    │                        │
│  │  │  AirtableWatcher    │    │  ← add more here       │
│  │  └─────────────────────┘    │                        │
│  └──────────────┬──────────────┘                        │
│                 │ new items?                             │
│                 ▼                                        │
│  ┌──────────────────────────┐                           │
│  │  SQLite state (seen IDs, │                           │
│  │  subscriber chat_ids)    │                           │
│  └──────────────────────────┘                           │
│                 │                                        │
│                 ▼                                        │
│  ┌──────────────────────────┐                           │
│  │   TelegramNotifier       │──────► Your Telegram      │
│  └──────────────────────────┘                           │
│                                                         │
│  telebot long-polling  ◄──────── /start /stop /status  │
└─────────────────────────────────────────────────────────┘
```

**Key design decisions:**

| Concern | Choice | Why |
|---|---|---|
| Polling | APScheduler interval | Simple, no external queue needed |
| State | SQLite on persistent disk | Zero infra overhead, survives restarts |
| First-run | Seeds existing records silently | You won't get spammed with old RFPs |
| Extensibility | `BaseWatcher` interface | Drop in a new watcher class, add to `WATCHERS` list |

---

## Prerequisites

- Python 3.11+
- A Railway account with a project ready
- Your Telegram bot token (you already have this from BotFather)
- An Airtable Personal Access Token (PAT)

---

## Step-by-step setup

> **You do NOT need to own the Airtable base.**
> The URL you have (`/shrXXX`) is a public shared view. The watcher uses
> Airtable's own internal shared-view API — no API key, no PAT, no login.

### 1 — Get your Telegram chat ID

Your bot needs to know where to send messages. The easiest way:

1. Open Telegram and search for `@SolanaMobileRFPs_bot`
2. Send `/start`
3. The bot will register your chat ID automatically in the database

> Alternatively, message `@userinfobot` on Telegram — it will reply with your
> numeric chat ID. You can also set it manually in the DB if needed.

### 2 — Configure environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token_here
AIRTABLE_SHARED_VIEW_URL=https://airtable.com/appw7jfRXG6Joia2b/shrsfJpcHYJZat9Uk
POLL_INTERVAL_MINUTES=15
DB_PATH=/data/state.db
```

### 3 — Run locally (optional test)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# copy and fill .env first
python main.py
```

On first run you'll see `"First run: seeded X existing record(s)"` — that's correct,
it just learns what's already there. From then on it only notifies you about new ones.

---

## Railway deployment

### One-time setup

1. **Push this folder to a GitHub repo**

   ```bash
   cd rfp-watcher
   git init && git add . && git commit -m "init"
   gh repo create rfp-watcher --private --source=. --push
   # or push manually to an existing repo
   ```

2. **Create a new Railway service**

   - Go to https://railway.app → your project → **New Service** → **GitHub Repo**
   - Select the repo you just pushed
   - Railway detects the `Dockerfile` automatically

3. **Add environment variables in Railway**

   - Service → **Variables** tab
   - Add just two required keys: `TELEGRAM_BOT_TOKEN` and `AIRTABLE_SHARED_VIEW_URL`
   - Do NOT commit `.env` — Railway injects these at runtime

4. **Add a persistent volume (critical for SQLite state)**

   - Service → **Volumes** tab → **Add Volume**
   - Mount path: `/data`
   - This ensures `state.db` survives deploys and restarts

5. **Deploy**

   - Click **Deploy** (or it auto-deploys on push)
   - Check **Logs** tab — you should see:
     ```
     Database ready.
     Scheduler started — polling every 15 minutes.
     First run: seeded N existing record(s) — no notifications sent.
     Starting Telegram bot (long-polling)…
     ```

6. **Subscribe yourself**

   - Open Telegram → `@SolanaMobileRFPs_bot` → send `/start`
   - You're registered. New RFPs will now ping you.

### Subsequent deploys

Just push to GitHub. Railway auto-redeploys. The SQLite database on `/data` persists
across deploys so no state is lost.

---

## Bot commands

| Command | What it does |
|---|---|
| `/start` | Subscribe — registers your chat ID |
| `/rfps` | Lists all current RFPs with status, deadline, and link when available |
| `/open` | Lists only RFPs still open for submissions |
| `/closed` | Lists only RFPs that are closed for submissions |
| `/stop` | Unsubscribe |
| `/status` | Shows subscriber count and poll interval |
| `/help` | Shows the full command list |

---

## Adjusting the poll interval

Default is every **15 minutes**. Change `POLL_INTERVAL_MINUTES` in Railway variables
and redeploy. 5–15 min is a reasonable range for RFP boards.

---

## Adding more watchers

To watch something else (a website, another Airtable, a Twitter/X search, etc.):

1. Create `watchers/my_new_source.py` extending `BaseWatcher`:

   ```python
   from watchers.base import BaseWatcher, WatcherItem

   class MyNewWatcher(BaseWatcher):
       watcher_id = "my_new_source"   # unique, never change
       label      = "My New Source"

       def fetch_items(self) -> list[WatcherItem]:
           # fetch from your source, return list of WatcherItem
           ...
   ```

2. Add it to the `WATCHERS` list in `main.py`:

   ```python
   from watchers.my_new_source import MyNewWatcher

   WATCHERS = [
       AirtableWatcher(),
       MyNewWatcher(),   # ← new line
   ]
   ```

3. Push and redeploy. The new watcher gets its own namespace in the DB so state
   is tracked independently.

---

## Environment variable reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | From BotFather |
| `AIRTABLE_SHARED_VIEW_URL` | ✅ | — | Full URL of the public shared view |
| `POLL_INTERVAL_MINUTES` | ❌ | `15` | How often to check (minutes) |
| `DB_PATH` | ❌ | `./state.db` | Path to SQLite file — use `/data/state.db` on Railway |

---

## Notes

- The bot token in this README is intentionally left as a placeholder in `.env.example`.
  Never commit your real `.env` file.
- Railway's free tier runs services continuously — this bot is extremely lightweight
  (no web server, just polling) so it sits well within free limits.
- If you ever want to reset "seen" state (e.g. to re-notify everything), delete the
  `seen_records` table rows or delete `state.db` and restart.
