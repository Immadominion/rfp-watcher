"""
Watches *public* Airtable shared pages **and** shared views for new records.

Two share types are supported (auto-detected from ``window.initData``):

* **Shared page** — uses ``/v0.3/application/{appId}/readForSharedPages``
* **Shared view** — uses ``/v0.3/view/{viewId}/readSharedViewData``

No API key is required; only the public share URL.
"""
import json
import logging
from urllib.parse import urlparse

import requests

from watchers.base import BaseWatcher, WatcherItem

logger = logging.getLogger(__name__)

_TITLE_FIELDS = (
    "Project Name",
    "RFP Title",
    "Title",
    "Name",
    "Project",
)
_LINK_FIELDS = (
    "Application Link",
    "Application Form",
    "Link",
    "URL",
    "Project Link",
)

# Rich-text / bulky fields we skip from the metadata summary.
_SKIP_METADATA_FIELDS = {"Context", "Problem", "Proposed Solution",
                         "Impact", "Deliverables", "Payout breakdown",
                         "Overview", "Desirable Features"}


def _extract_plain_text(value) -> str:
    """Return plain text from a rich-text (OT document) cell value."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for segment in value.get("documentValue", []):
            text = segment.get("insert")
            if text:
                parts.append(text)
        return "".join(parts).strip()
    return str(value)


def _parse_initdata(html: str) -> dict:
    """Extract ``window.initData = { … }`` from the Airtable HTML shell."""
    marker = "window.initData = {"
    idx = html.find(marker)
    if idx == -1:
        raise ValueError("window.initData not found in page HTML")
    start = html.index("{", idx)
    depth = 0
    in_str = False
    esc = False
    end = start
    for i in range(start, len(html)):
        c = html[i]
        if esc:
            esc = False
            continue
        if c == "\\" and in_str:
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    return json.loads(html[start:end])


class AirtableWatcher(BaseWatcher):
    """Watches a public Airtable shared page or shared view for new records."""

    def __init__(self, url: str, watcher_id: str, label: str) -> None:
        self.watcher_id = watcher_id
        self.label = label
        self._url = url

        parts = urlparse(url).path.strip("/").split("/")
        self._share_id = next((p for p in parts if p.startswith("shr")), None)
        self._base_id  = next((p for p in parts if p.startswith("app")), None)

        if not self._share_id:
            raise ValueError(
                f"No share ID (shr…) found in URL: {url}\n"
                "Expected format: https://airtable.com/appXXX/shrXXX"
            )

    # ── public API ───────────────────────────────────────────────

    def fetch_items(self) -> list[WatcherItem]:
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        })

        # Step 1 — load the page shell to get cookies + initData
        page_resp = session.get(self._url, timeout=20)
        page_resp.raise_for_status()
        init_data = _parse_initdata(page_resp.text)

        access_policy = init_data["accessPolicy"]
        page_load_id  = init_data["pageLoadId"]
        app_id        = self._base_id or init_data.get(
            "sharedModelParentApplicationId", ""
        )

        # Auto-detect: shared page vs shared view
        if init_data.get("sharedPageId"):
            return self._fetch_shared_page(
                session, init_data, access_policy, page_load_id, app_id,
            )
        if init_data.get("sharedViewId"):
            return self._fetch_shared_view(
                session, init_data, access_policy, page_load_id, app_id,
            )
        raise RuntimeError(
            f"Could not detect share type for {self._url} — "
            "neither sharedPageId nor sharedViewId found in initData."
        )

    # ── shared-page fetch (e.g. Solana Mobile) ───────────────────

    def _fetch_shared_page(
        self, session, init_data, access_policy, page_load_id, app_id,
    ) -> list[WatcherItem]:
        page_id = init_data["sharedPageId"]
        api_url = (
            f"https://airtable.com/v0.3/application/{app_id}"
            "/readForSharedPages"
        )
        headers = self._common_headers(page_load_id, app_id)
        params = {
            "stringifiedObjectParams": json.dumps({
                "includeDataForPageId": page_id,
                "shouldIncludeSchemaChecksum": True,
                "expectedPageLayoutSchemaVersion": 26,
                "shouldPreloadQueries": True,
                "shouldPreloadAllPossibleContainerElementQueries": True,
                "urlSearch": "",
                "includePageLayoutTypeInfo": True,
                "includeDataForExpandedRowPageFromQueryContainer": True,
                "includeDataForAllReferencedExpandedRowPagesInLayout": True,
                "navigationMode": "view",
            }),
            "requestId": f"req{page_load_id}",
            "accessPolicy": access_policy,
        }

        resp = session.get(api_url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()

        if body.get("msg") != "SUCCESS":
            raise RuntimeError(f"Airtable API error: {body}")

        data = body["data"]
        col_map, select_choices = self._build_col_maps_from_table_schemas(
            data.get("tableSchemas", [])
        )

        pq = data.get("preloadPageQueryResults", {})
        table_data_by_id = pq.get("tableDataById", {})

        items: list[WatcherItem] = []
        for _table_id, table_data in table_data_by_id.items():
            rows_by_id = table_data.get("partialRowById", {})
            for rec_id, row in rows_by_id.items():
                items.append(self._to_item(row, col_map, select_choices))
        return items

    # ── shared-view fetch (e.g. Walrus) ──────────────────────────

    def _fetch_shared_view(
        self, session, init_data, access_policy, page_load_id, app_id,
    ) -> list[WatcherItem]:
        view_id = init_data["sharedViewId"]
        api_url = (
            f"https://airtable.com/v0.3/view/{view_id}"
            "/readSharedViewData"
        )
        headers = self._common_headers(page_load_id, app_id)
        params = {
            "stringifiedObjectParams": json.dumps({
                "includeVisibleFieldIdsIfSharedView": True,
            }),
            "requestId": f"req{page_load_id}",
            "accessPolicy": access_policy,
        }

        resp = session.get(api_url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()

        if body.get("msg") != "SUCCESS":
            raise RuntimeError(f"Airtable API error: {body}")

        data = body["data"]
        col_map, select_choices = self._build_col_maps_from_columns(
            data.get("columns", [])
        )

        items: list[WatcherItem] = []
        for row in data.get("rows", []):
            items.append(self._to_item(row, col_map, select_choices))
        return items

    # ── helpers shared by both paths ─────────────────────────────

    def _common_headers(self, page_load_id: str, app_id: str) -> dict:
        return {
            "Accept": "application/json",
            "x-airtable-inter-service-client": "webClient",
            "x-airtable-page-load-id": page_load_id,
            "x-airtable-application-id": app_id,
            "x-time-zone": "UTC",
            "x-user-locale": "en",
            "x-requested-with": "XMLHttpRequest",
            "Referer": self._url,
            "Origin": "https://airtable.com",
        }

    @staticmethod
    def _build_col_maps_from_table_schemas(table_schemas):
        col_map: dict[str, str] = {}
        select_choices: dict[str, dict[str, str]] = {}
        for ts in table_schemas:
            for col in ts.get("columns", []):
                col_map[col["id"]] = col.get("name", col["id"])
                if col.get("type") in ("select", "multiSelect"):
                    choices = col.get("typeOptions", {}).get("choices", {})
                    select_choices[col["id"]] = {
                        cid: c.get("name", cid) for cid, c in choices.items()
                    }
        return col_map, select_choices

    @staticmethod
    def _build_col_maps_from_columns(columns):
        col_map: dict[str, str] = {}
        select_choices: dict[str, dict[str, str]] = {}
        for col in columns:
            col_map[col["id"]] = col.get("name", col["id"])
            if col.get("type") in ("select", "multiSelect"):
                choices = col.get("typeOptions", {}).get("choices", {})
                select_choices[col["id"]] = {
                    cid: c.get("name", cid) for cid, c in choices.items()
                }
        return col_map, select_choices

    # ── private helpers ──────────────────────────────────────────

    def _to_item(
        self,
        row: dict,
        col_map: dict[str, str],
        select_choices: dict[str, dict[str, str]],
    ) -> WatcherItem:
        record_id = row.get("id", "")
        cells = row.get("cellValuesByColumnId", {})

        values_by_name: dict[str, str] = {}
        for field_id, value in cells.items():
            if value is None or value == "":
                continue
            name = col_map.get(field_id, field_id)

            # Resolve select option IDs to human names
            if field_id in select_choices and isinstance(value, str):
                value = select_choices[field_id].get(value, value)

            # Button fields → extract URL
            if isinstance(value, dict) and "label" in value and "url" in value:
                if value.get("url"):
                    value = value["url"]
                else:
                    continue  # button with no URL set

            # Rich-text → plain text (skip bulky fields from metadata)
            if isinstance(value, dict) and "documentValue" in value:
                if name in _SKIP_METADATA_FIELDS:
                    continue
                value = _extract_plain_text(value)

            str_val = (
                ", ".join(str(v) for v in value)
                if isinstance(value, list)
                else str(value)
            )
            values_by_name[name] = str_val

        # Extract and remove title
        title_key = next(
            (f for f in _TITLE_FIELDS if values_by_name.get(f)), None
        )
        if title_key:
            title_val = values_by_name.pop(title_key)
        elif values_by_name:
            first_key = next(iter(values_by_name))
            title_val = values_by_name.pop(first_key)
        else:
            title_val = None

        # Extract and remove link
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
