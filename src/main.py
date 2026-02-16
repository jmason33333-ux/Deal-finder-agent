"""
Deal Finder Agent — main orchestrator.

Pipeline:
  1. Fetch  — Pull listings from Flippa + Empire Flippers
  2. Qualify — Pass/fail filters: financials, age > 2yr, US location, niche
  3. Score  — Rank qualified listings 0-100 by deal attractiveness
  4. Enrich — Scrape detail pages for description text (top N only)
  5. AI     — Gemini summarizes the top 25 scored listings
  6. Output — All qualified listings go to Google Sheets (AI-analyzed ones first)
"""

from __future__ import annotations

from src.scrapers.flippa import FlippaClient
from src.scrapers.empire_flippers import EmpireFlippersClient
from src.analysis.scoring import qualify_listing, score_listing
from src.analysis.ai_analyzer import analyze_batch
from src.sheets.writer import SheetsWriter
from src.config import GEMINI_API_KEY, GOOGLE_APPS_SCRIPT_URL, MAX_AI_LISTINGS
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

    # ── Step 3: Score qualified listings ───────────────────────────────
    log.info("Step 3: Scoring %d qualified listings...", len(qualified))
    for listing in qualified:
        score_listing(listing)

    # Sort by deal score descending — best deals first
    qualified.sort(key=lambda l: l.deal_score, reverse=True)

    # Select top N for AI analysis
    ai_candidates = qualified[:MAX_AI_LISTINGS]
    log.info(
        "Top %d of %d qualified listings selected for AI analysis (scores: %d-%d)",
        len(ai_candidates), len(qualified),
        ai_candidates[0].deal_score if ai_candidates else 0,
        ai_candidates[-1].deal_score if ai_candidates else 0,
    )

    # ── Step 4: Enrich top listings with description text ──────────────
    needs_enrichment = [l for l in ai_candidates if not l.description]
    if needs_enrichment:
        log.info("Step 4: Enriching %d listings with description text...", len(needs_enrichment))
        flippa = FlippaClient()
        ef = EmpireFlippersClient()
        for listing in needs_enrichment:
            if listing.source == "flippa":
                flippa.fetch_listing_details(listing)
            elif listing.source == "empire_flippers":
                ef.fetch_listing_details(listing)
    else:
        log.info("Step 4: All top listings already have descriptions")

    # ── Step 5: AI analysis for top scored listings only ───────────────
    if GEMINI_API_KEY:
        log.info("Step 5: Running AI analysis on top %d listings...", len(ai_candidates))
        analyze_batch(ai_candidates)
    else:
        log.warning("Step 5: Skipping AI analysis — GEMINI_API_KEY not set")

    # ── Step 6: Write to Google Sheets ─────────────────────────────────
    if GOOGLE_APPS_SCRIPT_URL and qualified:
        log.info("Step 6: Writing %d qualified listings to Google Sheet...", len(qualified))
        writer = SheetsWriter()
        written = writer.write_listings(qualified)
        log.info("Wrote %d rows to sheet", written)
    elif not GOOGLE_APPS_SCRIPT_URL:
        log.warning("Step 6: Skipping Google Sheets — GOOGLE_APPS_SCRIPT_URL not set")
    else:
        log.info("Step 6: No qualified listings to write")

    # ── Summary ────────────────────────────────────────────────────────
    log.info("--- Results: %d qualified listings (top %d AI-analyzed) ---", len(qualified), len(ai_candidates))
    for listing in qualified:
        price_str = "${:,.0f}".format(listing.asking_price)
        profit_str = "${:,.0f}/mo".format(listing.monthly_net_profit)
        multiple_str = "{:.1f}x".format(listing.profit_multiple) if listing.profit_multiple != float("inf") else "N/A"
        age_str = "{}yr".format(listing.business_age_months // 12) if listing.business_age_months else "?"
        verdict = listing.ai_verdict or "—"
        msg = "  {:>3d}  {:<12s}  {:<6s}  {:<40s}  {:>10s}  {:>12s}  {:>5s}  {:>4s}  {}".format(
            listing.deal_score,
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
