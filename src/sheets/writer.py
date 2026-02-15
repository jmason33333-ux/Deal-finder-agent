"""
Google Sheets writer for deal finder results.

Uses a Google Service Account to authenticate. Expects:
  - A service account JSON key file at the path in GOOGLE_SHEETS_CREDENTIALS_FILE
  - The sheet shared with the service account email
  - GOOGLE_SHEET_ID set to the spreadsheet ID from the URL
"""

from __future__ import annotations

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import GOOGLE_SHEETS_CREDENTIALS_FILE, GOOGLE_SHEET_ID
from src.models import Listing
from src.logger import get_logger

log = get_logger("sheets")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

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

SHEET_NAME = "Deal Finder"


class SheetsWriter:
    """Writes scored listings to a Google Sheet."""

    def __init__(self, sheet_id: str = "", credentials_file: str = ""):
        self.sheet_id = sheet_id or GOOGLE_SHEET_ID
        self.credentials_file = credentials_file or GOOGLE_SHEETS_CREDENTIALS_FILE
        self._service = None

    def _get_service(self):
        """Lazily build the Sheets API service."""
        if self._service is None:
            creds = Credentials.from_service_account_file(
                self.credentials_file, scopes=SCOPES
            )
            self._service = build("sheets", "v4", credentials=creds)
        return self._service

    def write_listings(self, listings: list[Listing]) -> int:
        """Write listings to the Google Sheet. Returns number of rows written."""
        if not self.sheet_id:
            log.error("GOOGLE_SHEET_ID not configured — cannot write to sheet")
            return 0

        if not listings:
            log.info("No listings to write")
            return 0

        service = self._get_service()
        sheets = service.spreadsheets()

        # Ensure the target sheet/tab exists
        self._ensure_sheet_tab(sheets)

        # Check if header row exists, write it if not
        self._ensure_header(sheets)

        # Build rows from listings
        rows = [listing.to_sheet_row() for listing in listings]

        # Convert any non-string values to strings for Sheets API
        rows = [[str(cell) if not isinstance(cell, str) else cell for cell in row] for row in rows]

        # Append rows
        body = {"values": rows}
        try:
            result = sheets.values().append(
                spreadsheetId=self.sheet_id,
                range=f"'{SHEET_NAME}'!A:S",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body,
            ).execute()

            updated = result.get("updates", {}).get("updatedRows", len(rows))
            log.info("Wrote %d listings to Google Sheet", updated)
            return updated

        except HttpError as e:
            log.error("Google Sheets API error: %s", e)
            return 0

    def _ensure_sheet_tab(self, sheets):
        """Create the 'Deal Finder' tab if it doesn't exist."""
        try:
            metadata = sheets.get(spreadsheetId=self.sheet_id).execute()
            existing_tabs = [
                s["properties"]["title"] for s in metadata.get("sheets", [])
            ]
            if SHEET_NAME not in existing_tabs:
                body = {
                    "requests": [
                        {
                            "addSheet": {
                                "properties": {"title": SHEET_NAME}
                            }
                        }
                    ]
                }
                sheets.batchUpdate(
                    spreadsheetId=self.sheet_id, body=body
                ).execute()
                log.info("Created sheet tab '%s'", SHEET_NAME)
        except HttpError as e:
            log.warning("Could not check/create sheet tab: %s", e)

    def _ensure_header(self, sheets):
        """Write header row if the sheet is empty."""
        try:
            result = sheets.values().get(
                spreadsheetId=self.sheet_id,
                range=f"'{SHEET_NAME}'!A1:S1",
            ).execute()
            values = result.get("values", [])
            if not values:
                body = {"values": [HEADER_ROW]}
                sheets.values().update(
                    spreadsheetId=self.sheet_id,
                    range=f"'{SHEET_NAME}'!A1:S1",
                    valueInputOption="RAW",
                    body=body,
                ).execute()
                log.info("Wrote header row to sheet")

                # Format header: bold + freeze
                self._format_header(sheets)

        except HttpError as e:
            log.warning("Could not check/write header: %s", e)

    def _format_header(self, sheets):
        """Bold the header row and freeze it."""
        try:
            # Get the sheet ID for the tab
            metadata = sheets.get(spreadsheetId=self.sheet_id).execute()
            sheet_id = None
            for s in metadata.get("sheets", []):
                if s["properties"]["title"] == SHEET_NAME:
                    sheet_id = s["properties"]["sheetId"]
                    break

            if sheet_id is None:
                return

            requests = [
                # Bold header
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"bold": True},
                                "backgroundColor": {
                                    "red": 0.9,
                                    "green": 0.9,
                                    "blue": 0.9,
                                },
                            }
                        },
                        "fields": "userEnteredFormat(textFormat,backgroundColor)",
                    }
                },
                # Freeze header row
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {"frozenRowCount": 1},
                        },
                        "fields": "gridProperties.frozenRowCount",
                    }
                },
            ]
            sheets.batchUpdate(
                spreadsheetId=self.sheet_id, body={"requests": requests}
            ).execute()
        except HttpError as e:
            log.warning("Could not format header: %s", e)

    def clear_sheet(self):
        """Clear all data from the sheet (keeps header)."""
        service = self._get_service()
        sheets = service.spreadsheets()
        try:
            sheets.values().clear(
                spreadsheetId=self.sheet_id,
                range=f"'{SHEET_NAME}'!A2:S",
                body={},
            ).execute()
            log.info("Cleared sheet data (header preserved)")
        except HttpError as e:
            log.error("Could not clear sheet: %s", e)
