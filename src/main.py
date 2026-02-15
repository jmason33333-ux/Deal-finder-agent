"""
Deal Finder Agent — main orchestrator.

Progressive funnel:
  1. Fetch  — API: ecommerce_store, open, $50k-$500k → ~hundreds of raw listings
  2. Filter — Client-side: profit >= $5k/mo, age >= 2yr, no excluded industries
  3. Rank   — Pre-enrichment score (API data only) → take top 50
  4. Enrich — Scrape detail pages for top 50 only (platform, hours, fulfillment)
  5. Score  — Full 100-point scoring with enriched data
  6. AI     — Gemini analysis for score >= 65
  7. Output — Google Sheets for score >= 80 (STRONG BUY)
"""

from __future__ import annotations

from src.scrapers.flippa import FlippaClient
from src.analysis.scoring import score_listing
from src.analysis.ai_analyzer import analyze_batch
from src.sheets.writer import SheetsWriter
from src.config import SCORE_THRESHOLD_SHEET, SCORE_THRESHOLD_AI_ANALYSIS, GEMINI_API_KEY, GOOGLE_APPS_SCRIPT_URL
from src.logger import get_logger

log = get_logger("main")


# Maximum candidates to enrich (scraping individual detail pages is slow)
MAX_ENRICH = 50


def run_pipeline() -> list:
    """Execute the full deal-finding pipeline.

    Funnel stages:
      1. Fetch  — API query with server-side filters (type, status, price)
      2. Filter — client-side: profit, age, industry
      3. Rank   — pre-enrichment score (API data only) → take top N
      4. Enrich — scrape detail pages for top N only
      5. Score  — full 100-point scoring with enriched data
      6. AI     — Gemini analysis for score >= 65
      7. Output — Google Sheets for score >= 80 (STRONG BUY)
    """
    log.info("=== Deal Finder Agent — starting run ===")

    # ── Step 1: Fetch + filter ────────────────────────────────────────
    log.info("Step 1: Fetching listings from Flippa...")
    flippa = FlippaClient()
    listings = flippa.fetch_listings(max_pages=20)
    log.info("Fetched %d listings after API + client-side filters", len(listings))

    if not listings:
        log.warning("No listings fetched after filtering. Exiting.")
        return []

    # ── Step 2: Pre-enrichment score to rank candidates ───────────────
    # Score with API data only. Unknown fields get neutral scores, so the
    # differentiation comes mainly from financials + niche (the data we have).
    # This lets us avoid enriching hundreds of listings.
    log.info("Step 2: Pre-scoring %d listings (API data only)...", len(listings))
    for listing in listings:
        score_listing(listing)
    listings.sort(key=lambda l: l.total_score, reverse=True)

    # Log the pre-enrichment distribution
    tiers = {"STRONG BUY": 0, "INVESTIGATE": 0, "CONDITIONAL": 0, "PASS": 0}
    for l in listings:
        if l.total_score >= 80:
            tiers["STRONG BUY"] += 1
        elif l.total_score >= 65:
            tiers["INVESTIGATE"] += 1
        elif l.total_score >= 50:
            tiers["CONDITIONAL"] += 1
        else:
            tiers["PASS"] += 1
    log.info(
        "Pre-enrichment distribution: %d STRONG BUY, %d INVESTIGATE, %d CONDITIONAL, %d PASS",
        tiers["STRONG BUY"], tiers["INVESTIGATE"], tiers["CONDITIONAL"], tiers["PASS"],
    )

    # Take top candidates for enrichment
    candidates = listings[:MAX_ENRICH]
    log.info(
        "Taking top %d candidates for enrichment (score range: %d–%d)",
        len(candidates),
        candidates[-1].total_score if candidates else 0,
        candidates[0].total_score if candidates else 0,
    )

    # ── Step 3: Enrich top candidates with detail page data ───────────
    log.info("Step 3: Enriching %d listings with detail data...", len(candidates))
    for listing in candidates:
        flippa.fetch_listing_details(listing)

    # ── Step 4: Re-score with enriched data ───────────────────────────
    log.info("Step 4: Re-scoring with enriched data...")
    for listing in candidates:
        # Reset score so it's recalculated cleanly
        listing.total_score = 0
        listing.score_breakdown = {}
        score_listing(listing)
    candidates.sort(key=lambda l: l.total_score, reverse=True)

    # ── Step 5: AI analysis for listings scoring >= 65 ────────────────
    if GEMINI_API_KEY:
        ai_eligible = [l for l in candidates if l.total_score >= SCORE_THRESHOLD_AI_ANALYSIS]
        log.info(
            "Step 5: Running Gemini AI analysis on %d qualifying listings (score >= %d)...",
            len(ai_eligible), SCORE_THRESHOLD_AI_ANALYSIS,
        )
        if ai_eligible:
            analyze_batch(ai_eligible)
    else:
        log.warning("Step 5: Skipping AI analysis — GEMINI_API_KEY not set")

    # ── Step 6: Write STRONG BUY listings to Google Sheets ────────────
    qualified = [l for l in candidates if l.total_score >= SCORE_THRESHOLD_SHEET]
    log.info(
        "%d of %d enriched listings are STRONG BUY (score >= %d)",
        len(qualified), len(candidates), SCORE_THRESHOLD_SHEET,
    )

    if GOOGLE_APPS_SCRIPT_URL and qualified:
        log.info("Step 6: Writing %d listings to Google Sheet...", len(qualified))
        writer = SheetsWriter()
        written = writer.write_listings(qualified)
        log.info("Wrote %d rows to sheet", written)
    elif not GOOGLE_APPS_SCRIPT_URL:
        log.warning("Step 6: Skipping Google Sheets — GOOGLE_APPS_SCRIPT_URL not set")
    else:
        log.info("Step 6: No STRONG BUY listings to write")

    # ── Summary ───────────────────────────────────────────────────────
    log.info("--- Top 20 results (enriched + scored) ---")
    for listing in candidates[:20]:
        label = "STRONG BUY" if listing.total_score >= 80 else (
            "INVESTIGATE" if listing.total_score >= 65 else (
                "CONDITIONAL" if listing.total_score >= 50 else "PASS"
            )
        )
        price_str = "${:,.0f}".format(listing.asking_price)
        profit_str = "${:,.0f}/mo".format(listing.monthly_net_profit)
        age_str = "{}yr".format(listing.business_age_months // 12) if listing.business_age_months else "?"
        msg = "  {:3d}  {:<12s}  {:<40s}  {:>10s}  {:>12s}  {:>4s}  {}".format(
            listing.total_score,
            label,
            listing.business_name[:40],
            price_str,
            profit_str,
            age_str,
            listing.niche or "—",
        )
        log.info("%s", msg)

    log.info("=== Deal Finder Agent — run complete ===")
    return qualified


if __name__ == "__main__":
    run_pipeline()
