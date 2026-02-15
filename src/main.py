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
from src.config import SCORE_THRESHOLD_SHEET
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

    # Step 2: Enrich with detail pages (top candidates only to limit requests)
    log.info("Step 2: Enriching listings with detail data...")
    for listing in listings:
        flippa.fetch_listing_details(listing)

    # Step 3: Score all listings
    log.info("Step 3: Scoring listings...")
    for listing in listings:
        score_listing(listing)

    # Sort by score descending
    listings.sort(key=lambda l: l.total_score, reverse=True)

    # Filter to qualifying listings
    qualified = [l for l in listings if l.total_score >= SCORE_THRESHOLD_SHEET]
    log.info(
        "%d of %d listings qualified (score >= %d)",
        len(qualified),
        len(listings),
        SCORE_THRESHOLD_SHEET,
    )

    # Step 4: AI analysis for high-scoring listings (placeholder)
    # TODO: Add Claude API analysis for listings with score >= 65

    # Step 5: Write to Google Sheets (placeholder)
    # TODO: Add Google Sheets integration

    # Print summary
    for listing in qualified:
        log.info(
            "  %3d  %-40s  %s  $%,.0f",
            listing.total_score,
            listing.business_name[:40],
            listing.source,
            listing.asking_price,
        )

    log.info("=== Deal Finder Agent — run complete ===")
    return qualified


if __name__ == "__main__":
    run_pipeline()
