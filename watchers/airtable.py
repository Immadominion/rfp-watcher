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
        # Fallback: scrape the HTML page for RFPs
        import re
        from html import unescape

        resp = requests.get(AIRTABLE_SHARED_VIEW_URL, timeout=20)
        resp.raise_for_status()
        html = resp.text

        # Use BeautifulSoup to robustly extract RFPs from the HTML
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        items = []
        # Find all RFP titles (they appear as <h1> or <h2> with anchor or strong tags)
        for header in soup.find_all(["h1", "h2", "h3"]):
            title = header.get_text(strip=True)
            # Heuristic: skip non-RFP headers
            if not title or "Solana Mobile" in title or "Active RFPs" in title:
                continue
            # The RFP body is the next siblings until the next header
            body_parts = []
            for sib in header.next_siblings:
                if getattr(sib, "name", None) in ("h1", "h2", "h3"):
                    break
                if hasattr(sib, "get_text"):
                    body_parts.append(sib.get_text(" ", strip=True))
                elif isinstance(sib, str):
                    body_parts.append(sib.strip())
            body = " ".join(body_parts)
            # Extract fields from body
            def extract_field(field, text):
                m = re.search(rf"{re.escape(field)}\s+(.+?)(?:\s{2,}|$)", text)
                return m.group(1).strip() if m else None

            metadata = {}
            for field in [
                "RFP Status", "Application Deadline", "Application Link", "Completion Deadline",
                "Maximum Grant Amount (USD equivalent)", "Payment Currency", "Context", "Deliverables", "Impact", "Problem", "Proposed Solution"
            ]:
                val = extract_field(field, body)
                if val:
                    metadata[field] = val

            # Fallback: try to get a link
            link = extract_field("Application Link", body)
            if not link:
                m = re.search(r"https://airtable.com/app[\w]+/pag[\w]+/form", body)
                if m:
                    link = m.group(0)

            # Compose WatcherItem
            items.append(
                WatcherItem(
                    id=f"{title.lower().replace(' ', '_')}",
                    title=title,
                    url=link or AIRTABLE_SHARED_VIEW_URL,
                    metadata=metadata,
                )
            )
        return items

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
