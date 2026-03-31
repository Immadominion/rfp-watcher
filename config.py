import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]

# Full public shared-view URL, e.g. https://airtable.com/appXXX/shrXXX
AIRTABLE_SHARED_VIEW_URL: str = os.environ.get("AIRTABLE_SHARED_VIEW_URL", "")

# Multiple Airtable sources (JSON list of {url, id, label} objects), or
# individually named env vars as fallback.
AIRTABLE_SOURCES_JSON: str = os.environ.get("AIRTABLE_SOURCES", "")

SOLANA_MOBILE_URL: str = os.environ.get(
    "SOLANA_MOBILE_AIRTABLE_URL",
    "https://airtable.com/appw7jfRXG6Joia2b/shrsfJpcHYJZat9Uk",
)
WALRUS_URL: str = os.environ.get(
    "WALRUS_AIRTABLE_URL",
    "https://airtable.com/appoDAKpC74UOqoDa/shr1je0hfpi4LFHHx/tbliqV4teM5mxdDVp",
)

POLL_INTERVAL_MINUTES: int = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))
DB_PATH: str = os.getenv("DB_PATH", "./state.db")
