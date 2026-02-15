/**
 * Deal Finder Agent — Google Apps Script receiver.
 *
 * SETUP:
 *   1. Open your Google Sheet
 *   2. Extensions > Apps Script
 *   3. Paste this entire file into Code.gs
 *   4. Click Deploy > New deployment
 *   5. Type: Web app
 *   6. Execute as: Me
 *   7. Who has access: Anyone
 *   8. Deploy — copy the URL
 *   9. Paste the URL into config/secrets.env as GOOGLE_APPS_SCRIPT_URL
 *
 * The Python agent will POST JSON to this URL. This script handles:
 *   - "append": adds rows (writes header first if sheet is empty)
 *   - "clear":  clears data rows (keeps header)
 */

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var action = data.action || "append";

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName("Deal Finder");

    // Create tab if it doesn't exist
    if (!sheet) {
      sheet = ss.insertSheet("Deal Finder");
    }

    if (action === "clear") {
      return handleClear(sheet);
    }

    if (action === "append") {
      return handleAppend(sheet, data);
    }

    return jsonResponse({ status: "error", message: "Unknown action: " + action });

  } catch (err) {
    return jsonResponse({ status: "error", message: err.toString() });
  }
}

function handleAppend(sheet, data) {
  var headers = data.headers || [];
  var rows = data.rows || [];

  // Write header if sheet is empty
  if (sheet.getLastRow() === 0 && headers.length > 0) {
    sheet.appendRow(headers);

    // Bold + freeze header
    var headerRange = sheet.getRange(1, 1, 1, headers.length);
    headerRange.setFontWeight("bold");
    headerRange.setBackground("#e8e8e8");
    sheet.setFrozenRows(1);

    // Auto-resize columns
    for (var c = 1; c <= headers.length; c++) {
      sheet.autoResizeColumn(c);
    }
  }

  // Append data rows
  var written = 0;
  for (var i = 0; i < rows.length; i++) {
    sheet.appendRow(rows[i]);
    written++;
  }

  // Highlight STRONG BUY scores (column N = 14) in green
  if (sheet.getLastRow() > 1) {
    var scoreCol = 14; // TOTAL SCORE column
    var dataStart = 2;
    var dataEnd = sheet.getLastRow();
    var scoreRange = sheet.getRange(dataStart, scoreCol, dataEnd - dataStart + 1, 1);
    var scores = scoreRange.getValues();
    for (var r = 0; r < scores.length; r++) {
      var score = parseInt(scores[r][0], 10);
      if (score >= 80) {
        sheet.getRange(dataStart + r, 1, 1, sheet.getLastColumn())
             .setBackground("#d4edda"); // light green
      }
    }
  }

  return jsonResponse({
    status: "ok",
    rows_written: written,
    total_rows: sheet.getLastRow() - 1
  });
}

function handleClear(sheet) {
  var lastRow = sheet.getLastRow();
  if (lastRow > 1) {
    sheet.deleteRows(2, lastRow - 1);
  }
  return jsonResponse({ status: "ok", message: "Cleared" });
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * Test function — run this from the Apps Script editor to verify it works.
 * Check your sheet for a test row after running.
 */
function testAppend() {
  var mockEvent = {
    postData: {
      contents: JSON.stringify({
        action: "append",
        headers: [
          "Listing URL", "Source", "Business Name", "Niche", "Asking Price",
          "Monthly Revenue", "Monthly Net Profit", "Profit Multiple", "Net Margin %",
          "Platform", "Business Age", "Traffic Sources", "Owner Hours/Week",
          "TOTAL SCORE", "Score Breakdown", "AI Analysis", "Top 3 Growth Moves",
          "Estimated 12-Month ROI", "Date Found"
        ],
        rows: [
          [
            "https://flippa.com/example", "flippa", "Test Store", "Health & Wellness",
            "$250,000", "$18,000", "$7,200", "2.9x", "40.0%", "shopify", "36 mo",
            "organic: 45%, paid: 30%, email: 25%", "10", 85,
            "financial: 22 | traffic: 12 | ops: 10 | ai_upside: 8 | strategic: 8",
            "Strong fundamentals with diversified traffic...",
            "1) AI customer support  2) Email automation  3) SEO expansion",
            "35-50%", "2026-02-15"
          ]
        ]
      })
    }
  };
  var result = doPost(mockEvent);
  Logger.log(result.getContent());
}
