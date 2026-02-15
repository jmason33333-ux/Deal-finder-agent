"""
Deal Finder Agent — main orchestrator.

Runs the full pipeline:
  1. Fetch listings from Flippa (broad funnel)
  2. Enrich qualifying listings with detail page data
  3. Score each listing (100-point scale)
  4. Send qualifying listings for AI analysis
  5. Write results to Google Sheets
"""

from __future__ import annotations

from src.scrapers.flippa import FlippaClient
from src.analysis.scoring import score_listing
from src.analysis.ai_analyzer import analyze_batch
from src.sheets.writer import SheetsWriter
from src.config import SCORE_THRESHOLD_SHEET, SCORE_THRESHOLD_AI_ANALYSIS, GEMINI_API_KEY, GOOGLE_APPS_SCRIPT_URL
from src.logger import get_logger

log = get_logger("main")


def run_pipeline() -> list:
    """Execute the full deal-finding pipeline."""
    log.info("=== Deal Finder Agent — starting run ===")

    # Step 1: Fetch listings (broad funnel → client-side filtering)
    log.info("Step 1: Fetching listings from Flippa...")
    flippa = FlippaClient()
    listings = flippa.fetch_listings(max_pages=20)
    log.info("Fetched %d listings from Flippa", len(listings))

    if not listings:
        log.warning("No listings fetched after filtering. Exiting.")
        return []

    # Step 2: Enrich with detail pages
    log.info("Step 2: Enriching %d listings with detail data...", len(listings))
    for listing in listings:
        flippa.fetch_listing_details(listing)

    # Step 3: Score all listings
    log.info("Step 3: Scoring listings...")
    for listing in listings:
        score_listing(listing)

    # Sort by score descending
    listings.sort(key=lambda l: l.total_score, reverse=True)

    # Step 4: AI analysis for listings scoring >= 65
    if GEMINI_API_KEY:
        log.info("Step 4: Running Gemini AI analysis on qualifying listings...")
        analyze_batch(listings)
    else:
        log.warning("Step 4: Skipping AI analysis — GEMINI_API_KEY not set")

    # Filter to STRONG BUY candidates (score >= 80)
    qualified = [l for l in listings if l.total_score >= SCORE_THRESHOLD_SHEET]
    log.info(
        "%d of %d listings are STRONG BUY (score >= %d)",
        len(qualified),
        len(listings),
        SCORE_THRESHOLD_SHEET,
    )

    # Step 5: Write STRONG BUY listings to Google Sheets
    if GOOGLE_APPS_SCRIPT_URL and qualified:
        log.info("Step 5: Writing %d listings to Google Sheet...", len(qualified))
        writer = SheetsWriter()
        written = writer.write_listings(qualified)
        log.info("Wrote %d rows to sheet", written)
    elif not GOOGLE_APPS_SCRIPT_URL:
        log.warning("Step 5: Skipping Google Sheets — GOOGLE_APPS_SCRIPT_URL not set")
    else:
        log.info("Step 5: No STRONG BUY listings to write")

    # Print summary of all scored listings
    log.info("--- Full results (top 20) ---")
    for listing in listings[:20]:
        label = "STRONG BUY" if listing.total_score >= 80 else (
            "INVESTIGATE" if listing.total_score >= 65 else (
                "CONDITIONAL" if listing.total_score >= 50 else "PASS"
            )
        )
        # Use f-string to avoid Python % formatting issues with $, commas
        price_str = "${:,.0f}".format(listing.asking_price)
        profit_str = "${:,.0f}/mo".format(listing.monthly_net_profit)
        msg = "  {:3d}  {:<12s}  {:<40s}  {:>10s}  {:>12s}  {}".format(
            listing.total_score,
            label,
            listing.business_name[:40],
            price_str,
            profit_str,
            listing.niche or "—",
        )
        log.info("%s", msg)

    log.info("=== Deal Finder Agent — run complete ===")
    return qualified


if __name__ == "__main__":
    run_pipeline()
