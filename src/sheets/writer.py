"""
Google Sheets writer via Apps Script Web App.

Instead of service accounts and API libraries, this uses a simple approach:
  1. You paste a small Apps Script into your Google Sheet
  2. Deploy it as a web app (runs as you, anyone can access)
  3. Set the deployed URL in GOOGLE_APPS_SCRIPT_URL
  4. The agent POSTs JSON data to it — done.

No pip packages, no credentials files, no Google Cloud project needed.
"""

from __future__ import annotations

import json
import time
import requests

from src.config import GOOGLE_APPS_SCRIPT_URL
from src.models import Listing
from src.logger import get_logger

log = get_logger("sheets")

HEADER_ROW = [
    "Listing URL",
    "Source",
    "Business Name",
    "Niche",
    "Asking Price",
    "Monthly Revenue",
    "Monthly Net Profit",
    "Profit Multiple",
    "Net Margin %",
    "Platform",
    "Business Age",
    "Traffic Sources",
    "Owner Hours/Week",
    "TOTAL SCORE",
    "Score Breakdown",
    "AI Analysis",
    "Top 3 Growth Moves",
    "Estimated 12-Month ROI",
    "Date Found",
]


class SheetsWriter:
    """Writes scored listings to Google Sheets via an Apps Script web app."""

    def __init__(self, webhook_url: str = ""):
        self.webhook_url = webhook_url or GOOGLE_APPS_SCRIPT_URL

    def write_listings(self, listings: list[Listing]) -> int:
        """POST listings to the Apps Script web app. Returns rows written."""
        if not self.webhook_url:
            log.error("GOOGLE_APPS_SCRIPT_URL not configured — cannot write to sheet")
            return 0

        if not listings:
            log.info("No listings to write")
            return 0

        rows = [listing.to_sheet_row() for listing in listings]
        # Ensure everything is JSON-serializable
        rows = [
            [str(cell) if not isinstance(cell, (str, int, float)) else cell for cell in row]
            for row in rows
        ]

        payload = {
            "action": "append",
            "headers": HEADER_ROW,
            "rows": rows,
        }

        # Retry with backoff (Apps Script can be slow on cold start)
        for attempt in range(1, 4):
            try:
                resp = requests.post(
                    self.webhook_url,
                    json=payload,
                    timeout=30,
                    headers={"Content-Type": "application/json"},
                )
                # Apps Script redirects on success — follow it
                if resp.status_code == 200:
                    result = resp.json()
                    written = result.get("rows_written", len(rows))
                    log.info("Wrote %d listings to Google Sheet", written)
                    return written
                else:
                    log.warning(
                        "Sheets webhook returned %d (attempt %d): %s",
                        resp.status_code,
                        attempt,
                        resp.text[:200],
                    )
            except requests.RequestException as e:
                log.warning("Sheets webhook failed (attempt %d): %s", attempt, e)

            if attempt < 3:
                wait = 2 ** attempt
                log.info("Retrying in %ds...", wait)
                time.sleep(wait)

        log.error("All attempts to write to Google Sheet failed")
        return 0

    def clear_sheet(self) -> bool:
        """Send a clear command to the Apps Script web app."""
        if not self.webhook_url:
            log.error("GOOGLE_APPS_SCRIPT_URL not configured")
            return False

        try:
            resp = requests.post(
                self.webhook_url,
                json={"action": "clear"},
                timeout=30,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                log.info("Cleared sheet data")
                return True
            else:
                log.error("Clear failed: %d %s", resp.status_code, resp.text[:200])
                return False
        except requests.RequestException as e:
            log.error("Clear request failed: %s", e)
            return False
