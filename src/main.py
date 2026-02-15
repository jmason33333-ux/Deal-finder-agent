"""
Deal Finder Agent — main orchestrator.

Runs the full pipeline:
  1. Fetch listings from Flippa (and eventually Empire Flippers)
  2. Score each listing
  3. Send qualifying listings for AI analysis
  4. Write results to Google Sheets
"""

from __future__ import annotations

from src.scrapers.flippa import FlippaClient
from src.analysis.scoring import score_listing
from src.analysis.ai_analyzer import analyze_batch
from src.sheets.writer import SheetsWriter
from src.config import SCORE_THRESHOLD_SHEET, SCORE_THRESHOLD_AI_ANALYSIS, ANTHROPIC_API_KEY, GOOGLE_APPS_SCRIPT_URL
from src.logger import get_logger

log = get_logger("main")


def run_pipeline() -> list:
    """Execute the full deal-finding pipeline."""
    log.info("=== Deal Finder Agent — starting run ===")

    # Step 1: Fetch listings
    log.info("Step 1: Fetching listings from Flippa...")
    flippa = FlippaClient()
    listings = flippa.fetch_listings(max_pages=5)
    log.info("Fetched %d listings from Flippa", len(listings))

    if not listings:
        log.warning("No listings fetched. Exiting.")
        return []

    # Step 2: Enrich with detail pages
    log.info("Step 2: Enriching listings with detail data...")
    for listing in listings:
        flippa.fetch_listing_details(listing)

    # Step 3: Score all listings
    log.info("Step 3: Scoring listings...")
    for listing in listings:
        score_listing(listing)

    # Sort by score descending
    listings.sort(key=lambda l: l.total_score, reverse=True)

    # Step 4: AI analysis for listings scoring >= 65
    if ANTHROPIC_API_KEY:
        log.info("Step 4: Running Claude AI analysis on qualifying listings...")
        analyze_batch(listings)
    else:
        log.warning("Step 4: Skipping AI analysis — ANTHROPIC_API_KEY not set")

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
        log.info(
            "  %3d  %-12s  %-35s  %s  $%,.0f",
            listing.total_score,
            label,
            listing.business_name[:35],
            listing.source,
            listing.asking_price,
        )

    log.info("=== Deal Finder Agent — run complete ===")
    return qualified


if __name__ == "__main__":
    run_pipeline()
