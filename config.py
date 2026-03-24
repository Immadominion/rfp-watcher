import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]

# Full public shared-view URL, e.g. https://airtable.com/appXXX/shrXXX
AIRTABLE_SHARED_VIEW_URL: str = os.environ["AIRTABLE_SHARED_VIEW_URL"]

POLL_INTERVAL_MINUTES: int = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))
DB_PATH: str = os.getenv("DB_PATH", "./state.db")
