"""
Watches a *public* Airtable shared view for new records.
No API key or account ownership required — uses Airtable's internal
shared-view endpoint, the same one their browser frontend calls.
"""
import logging
from urllib.parse import urlparse

import requests

from config import AIRTABLE_SHARED_VIEW_URL
from watchers.base import BaseWatcher, WatcherItem

logger = logging.getLogger(__name__)

_TITLE_FIELDS = (
    "RFP Title",
    "Title",
    "Name",
    "Project",
)
_LINK_FIELDS = (
    "Application Link",
    "Link",
    "URL",
    "Project Link",
)

# Airtable's internal endpoint for fetching public shared-view data.
_READ_ENDPOINT = "https://airtable.com/v0.3/view/{share_id}/readSharedViewData"


class AirtableWatcher(BaseWatcher):
    """Watches a public Airtable shared view for new records."""

    watcher_id = "airtable_rfps"
    label = "Solana Mobile RFPs (Airtable)"

    def __init__(self) -> None:
        parts = urlparse(AIRTABLE_SHARED_VIEW_URL).path.strip("/").split("/")
        share_id = next((p for p in parts if p.startswith("shr")), None)
        base_id  = next((p for p in parts if p.startswith("app")), None)

        if not share_id:
            raise ValueError(
                f"No share ID (shr…) found in URL: {AIRTABLE_SHARED_VIEW_URL}\n"
                "Expected format: https://airtable.com/appXXX/shrXXX"
            )

        self._url     = _READ_ENDPOINT.format(share_id=share_id)
        self._base_id = base_id

    def fetch_items(self) -> list[WatcherItem]:
        headers = {
            "Accept": "application/json",
            "x-requested-with": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 rfp-watcher/1.0",
        }
        if self._base_id:
            headers["x-airtable-application-id"] = self._base_id

        resp = requests.get(
            self._url,
            headers=headers,
            params={"stringifiedObjectParams": "{}"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        table  = data.get("data", {}).get("tableData", {})
        rows   = table.get("rows", [])
        # visibleFields gives us fieldId → human name mapping
        cols   = data.get("data", {}).get("visibleFields", [])
        col_map = {c["fieldId"]: c.get("name", c["fieldId"]) for c in cols}

        return [self._to_item(row, col_map) for row in rows]

    def _to_item(self, row: dict, col_map: dict) -> WatcherItem:
        record_id = row.get("id", "")
        cells     = row.get("cellValuesByFieldId", {})

        values_by_name: dict[str, str] = {}

        for field_id, value in cells.items():
            if value is None or value == "":
                continue
            name    = col_map.get(field_id, field_id)
            str_val = (
                ", ".join(str(v) for v in value)
                if isinstance(value, list)
                else str(value)
            )
            values_by_name[name] = str_val

        # Identify and remove title from metadata
        title_key = next((f for f in _TITLE_FIELDS if values_by_name.get(f)), None)
        if title_key:
            title_val = values_by_name.pop(title_key)
        elif values_by_name:
            first_key = next(iter(values_by_name))
            title_val = values_by_name.pop(first_key)
        else:
            title_val = None

        # Identify and remove link from metadata
        url_val = ""
        for field in _LINK_FIELDS:
            if field in values_by_name:
                url_val = values_by_name.pop(field)
                break

        return WatcherItem(
            id=record_id,
            title=title_val or f"Record {record_id}",
            url=url_val,
            metadata=values_by_name,
        )
