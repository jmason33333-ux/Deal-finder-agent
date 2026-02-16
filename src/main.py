"""
Deal Finder Agent — main orchestrator.

Simplified pipeline:
  1. Fetch  — Pull listings from multiple marketplaces (Flippa, Empire Flippers)
  2. Qualify — Pass/fail filters: financials, age > 2yr, US location, niche
  3. Enrich — Scrape detail pages for description text (qualified listings only)
  4. AI     — Gemini summarizes every qualified listing from title + description
  5. Output — All qualified listings go to Google Sheets with AI analysis
"""

from __future__ import annotations

from src.scrapers.flippa import FlippaClient
from src.scrapers.empire_flippers import EmpireFlippersClient
from src.analysis.scoring import qualify_listing
from src.analysis.ai_analyzer import analyze_batch
from src.sheets.writer import SheetsWriter
from src.config import GEMINI_API_KEY, GOOGLE_APPS_SCRIPT_URL
from src.logger import get_logger

log = get_logger("main")

# Max listings to enrich with detail page scraping
MAX_ENRICH = 50


def run_pipeline() -> list:
    """Execute the full deal-finding pipeline."""
    log.info("=== Deal Finder Agent — starting run ===")

    # ── Step 1: Fetch listings from all sources ────────────────────────
    all_listings = []

    log.info("Step 1: Fetching listings from marketplaces...")

    # Flippa
    try:
        flippa = FlippaClient()
        flippa_listings = flippa.fetch_listings(max_pages=20)
        log.info("Flippa: %d listings after client-side filters", len(flippa_listings))
        all_listings.extend(flippa_listings)
    except Exception as e:
        log.error("Flippa fetch failed: %s", e)

    # Empire Flippers
    try:
        ef = EmpireFlippersClient()
        ef_listings = ef.fetch_listings(max_pages=5)
        log.info("Empire Flippers: %d listings after client-side filters", len(ef_listings))
        all_listings.extend(ef_listings)
    except Exception as e:
        log.error("Empire Flippers fetch failed: %s", e)

    log.info("Total listings from all sources: %d", len(all_listings))

    if not all_listings:
        log.warning("No listings fetched from any source. Exiting.")
        return []

    # ── Step 2: Qualify — pass/fail filters ────────────────────────────
    log.info("Step 2: Running qualification filters on %d listings...", len(all_listings))
    for listing in all_listings:
        qualify_listing(listing)

    qualified = [l for l in all_listings if l.qualified]
    filtered = len(all_listings) - len(qualified)
    log.info(
        "Qualification: %d qualified, %d filtered out",
        len(qualified), filtered,
    )

    if not qualified:
        log.warning("No listings passed qualification. Exiting.")
        _log_filter_summary(all_listings)
        return []

    # ── Step 3: Enrich qualified listings with description text ────────
    # Only enrich listings that don't already have description text
    needs_enrichment = [l for l in qualified if not l.description][:MAX_ENRICH]
    if needs_enrichment:
        log.info("Step 3: Enriching %d listings with description text...", len(needs_enrichment))
        flippa = FlippaClient()
        ef = EmpireFlippersClient()
        for listing in needs_enrichment:
            if listing.source == "flippa":
                flippa.fetch_listing_details(listing)
            elif listing.source == "empire_flippers":
                ef.fetch_listing_details(listing)
    else:
        log.info("Step 3: All qualified listings already have descriptions")

    # ── Step 4: AI analysis for all qualified listings ─────────────────
    if GEMINI_API_KEY:
        log.info("Step 4: Running AI analysis on %d qualified listings...", len(qualified))
        analyze_batch(qualified)
    else:
        log.warning("Step 4: Skipping AI analysis — GEMINI_API_KEY not set")

    # ── Step 5: Write to Google Sheets ─────────────────────────────────
    if GOOGLE_APPS_SCRIPT_URL and qualified:
        log.info("Step 5: Writing %d qualified listings to Google Sheet...", len(qualified))
        writer = SheetsWriter()
        written = writer.write_listings(qualified)
        log.info("Wrote %d rows to sheet", written)
    elif not GOOGLE_APPS_SCRIPT_URL:
        log.warning("Step 5: Skipping Google Sheets — GOOGLE_APPS_SCRIPT_URL not set")
    else:
        log.info("Step 5: No qualified listings to write")

    # ── Summary ────────────────────────────────────────────────────────
    log.info("--- Results: %d qualified listings ---", len(qualified))
    for listing in qualified:
        price_str = "${:,.0f}".format(listing.asking_price)
        profit_str = "${:,.0f}/mo".format(listing.monthly_net_profit)
        multiple_str = "{:.1f}x".format(listing.profit_multiple) if listing.profit_multiple != float("inf") else "N/A"
        age_str = "{}yr".format(listing.business_age_months // 12) if listing.business_age_months else "?"
        verdict = listing.ai_verdict or "—"
        msg = "  {:<12s}  {:<6s}  {:<40s}  {:>10s}  {:>12s}  {:>5s}  {:>4s}  {}".format(
            verdict,
            listing.source[:6],
            listing.business_name[:40],
            price_str,
            profit_str,
            multiple_str,
            age_str,
            listing.niche or "—",
        )
        log.info("%s", msg)

    log.info("=== Deal Finder Agent — run complete ===")
    return qualified


def _log_filter_summary(listings: list) -> None:
    """Log a summary of why listings were filtered out."""
    reasons: dict[str, int] = {}
    for l in listings:
        if not l.qualified and l.disqualify_reason:
            # Group by first word of reason
            key = l.disqualify_reason.split(":")[0] if ":" in l.disqualify_reason else l.disqualify_reason[:30]
            reasons[key] = reasons.get(key, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
        log.info("  %d filtered: %s", count, reason)


if __name__ == "__main__":
    run_pipeline()
