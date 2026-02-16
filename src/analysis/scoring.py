"""
Qualification + deal scoring for e-commerce business listings.

Two stages:
  1. Qualify: hard pass/fail filters (financials, age, niche, location)
  2. Score:   0-100 deal score ranking qualified listings by attractiveness

The deal score uses only data we already have from the API — no extra
calls needed. It ranks listings so the pipeline can cap how many get
sent to Gemini for AI analysis (e.g. top 25 per run).
"""

from __future__ import annotations

import re

from src.models import Listing
from src.config import FILTERS, EXCLUDED_INDUSTRIES, ALLOWED_LOCATIONS
from src.logger import get_logger

log = get_logger("qualifier")


def qualify_listing(listing: Listing) -> Listing:
    """Run pass/fail qualification filters. Sets listing.qualified and disqualify_reason."""
    reason = _check_filters(listing)
    if reason:
        listing.qualified = False
        listing.disqualify_reason = reason
        log.info("FILTERED [%s]: %s", listing.business_name[:50], reason)
    else:
        listing.qualified = True
        listing.disqualify_reason = ""
        log.info(
            "QUALIFIED [%s]: $%.0f asking, $%.0f/mo profit, %.1fx, %s",
            listing.business_name[:50],
            listing.asking_price,
            listing.monthly_net_profit,
            listing.profit_multiple if listing.profit_multiple != float("inf") else 0,
            listing.seller_location or "location unknown",
        )
    return listing


def _check_filters(listing: Listing) -> str:
    """Return a reason string if listing fails any filter, else ''."""

    # ── Financial filters ──────────────────────────────────────────────

    # Minimum monthly profit (with revenue fallback for missing profit data)
    min_profit = FILTERS.get("min_monthly_profit", 5_000)
    if listing.monthly_net_profit > 0:
        if listing.monthly_net_profit < min_profit:
            return "Profit ${:,.0f}/mo below ${:,.0f} minimum".format(
                listing.monthly_net_profit, min_profit
            )
    else:
        # No profit data — use revenue as proxy (assume ~20% margin possible)
        min_revenue_proxy = min_profit / 0.20
        if listing.monthly_revenue < min_revenue_proxy:
            return "No profit data and revenue ${:,.0f}/mo too low (need ${:,.0f}+ for proxy)".format(
                listing.monthly_revenue, min_revenue_proxy
            )

    # Profit multiple must be reasonable (<=4x)
    max_multiple = FILTERS.get("max_profit_multiple", 4.0)
    if listing.annual_net_profit > 0:
        if listing.profit_multiple > max_multiple:
            return "Profit multiple {:.1f}x exceeds {:.1f}x max".format(
                listing.profit_multiple, max_multiple
            )

    # Net margin must be at least 10%
    min_margin = FILTERS.get("min_net_margin_pct", 10.0)
    if listing.monthly_revenue > 0 and listing.monthly_net_profit > 0:
        if listing.net_margin_pct < min_margin:
            return "Net margin {:.1f}% below {:.0f}% minimum".format(
                listing.net_margin_pct, min_margin
            )

    # Price range
    min_price = FILTERS.get("min_price", 50_000)
    max_price = FILTERS.get("max_price", 500_000)
    if listing.asking_price < min_price:
        return "Asking price ${:,.0f} below ${:,.0f} minimum".format(
            listing.asking_price, min_price
        )
    if listing.asking_price > max_price:
        return "Asking price ${:,.0f} exceeds ${:,.0f} maximum".format(
            listing.asking_price, max_price
        )

    # ── Age filter ─────────────────────────────────────────────────────

    min_age = FILTERS.get("min_business_age_months", 24)
    if 0 < listing.business_age_months < min_age:
        return "Business age {} months, need {}+ months".format(
            listing.business_age_months, min_age
        )
    # age_months == 0 means unknown — let through, AI can flag it

    # ── Niche filter ───────────────────────────────────────────────────

    if listing.niche:
        niche_lower = listing.niche.lower()
        if any(excl in niche_lower for excl in EXCLUDED_INDUSTRIES):
            return "Excluded industry: {}".format(listing.niche)

    # ── Location filter ────────────────────────────────────────────────

    if listing.seller_location:
        location_lower = listing.seller_location.lower()
        # Use word-boundary matching to avoid "Australia" matching "us"
        if not any(re.search(r'\b' + re.escape(loc) + r'\b', location_lower) for loc in ALLOWED_LOCATIONS):
            return "Seller location '{}' not in allowed regions".format(
                listing.seller_location
            )
    # Empty location = unknown — let through, can be checked manually

    return ""


# ── Deal Score (0-100) ───────────────────────────────────────────────


def score_listing(listing: Listing) -> int:
    """Compute a 0-100 deal score for a qualified listing.

    Used to rank-and-cap which listings get sent to Gemini.
    Only call this on listings that already passed qualify_listing().

    Components (total = 100):
      - Profit multiple:  0-30 pts  (lower multiple = better value)
      - Net margin:       0-25 pts  (higher margin = more efficient)
      - Monthly profit:   0-25 pts  (closer to $10K/mo target = better)
      - Business age:     0-20 pts  (older = more proven)
    """
    score = 0

    # ── Profit multiple (30 pts) — lower is better ───────────────────
    # 1.5x or less = 30, 4x = 0, linear between
    if listing.profit_multiple != float("inf") and listing.profit_multiple > 0:
        multiple = listing.profit_multiple
        if multiple <= 1.5:
            score += 30
        elif multiple >= 4.0:
            score += 0
        else:
            # Linear: 30 at 1.5x, 0 at 4.0x
            score += int(30 * (4.0 - multiple) / 2.5)

    # ── Net margin (25 pts) — higher is better ───────────────────────
    # 40%+ = 25, 10% = 0, linear between
    margin = listing.net_margin_pct
    if margin >= 40:
        score += 25
    elif margin >= 10:
        score += int(25 * (margin - 10) / 30)

    # ── Monthly profit (25 pts) — closer to target = better ─────────
    # $10K+/mo = 25, $5K = 5, linear between
    profit = listing.monthly_net_profit
    if profit <= 0 and listing.monthly_revenue > 0:
        # Estimate from revenue at assumed 20% margin
        profit = listing.monthly_revenue * 0.20
    if profit >= 10_000:
        score += 25
    elif profit >= 5_000:
        score += 5 + int(20 * (profit - 5_000) / 5_000)

    # ── Business age (20 pts) — older is more proven ─────────────────
    # 5+ years = 20, 2 years = 5, linear between
    age = listing.business_age_months
    if age == 0:
        # Unknown age — give a neutral middle score
        score += 10
    elif age >= 60:
        score += 20
    elif age >= 24:
        score += 5 + int(15 * (age - 24) / 36)

    listing.deal_score = score
    return score
