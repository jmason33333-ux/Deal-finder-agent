"""
Scoring engine for e-commerce business listings.

100-point scale across 5 categories:
  - Financial Health (30 pts)
  - Traffic & Customer Acquisition (25 pts)
  - Operational Simplicity (20 pts)
  - AI Optimization Upside (15 pts)
  - Strategic Fit (10 pts)

Includes automatic disqualifiers that zero out the score.

Missing data strategy: when fields are unknown (0 / empty), award
neutral/mid-range scores rather than 0 so that financially promising
listings aren't killed by missing operational data before enrichment.
"""

from __future__ import annotations

from src.models import Listing
from src.logger import get_logger

log = get_logger("scoring")


def score_listing(listing: Listing) -> Listing:
    """Score a listing and populate total_score + score_breakdown."""
    # Check disqualifiers first
    disqualifier = _check_disqualifiers(listing)
    if disqualifier:
        log.info("DISQUALIFIED [%s]: %s", listing.business_name[:50], disqualifier)
        listing.total_score = 0
        listing.score_breakdown = {"disqualified": disqualifier}
        return listing

    breakdown = {}
    breakdown["financial"] = _score_financial(listing)
    breakdown["traffic"] = _score_traffic(listing)
    breakdown["operations"] = _score_operations(listing)
    breakdown["ai_upside"] = _score_ai_upside(listing)
    breakdown["strategic"] = _score_strategic(listing)

    listing.score_breakdown = breakdown
    listing.total_score = sum(breakdown.values())

    log.info(
        "Scored [%s]: %d/100 — %s",
        listing.business_name[:50],
        listing.total_score,
        breakdown,
    )
    return listing


# ── Disqualifiers ───────────────────────────────────────────────────────


def _check_disqualifiers(listing: Listing) -> str:
    """Return a reason string if listing is automatically disqualified, else ''."""
    # Revenue declining > 25% YoY
    if listing.revenue_trend_yoy is not None and listing.revenue_trend_yoy < -0.25:
        return "Revenue declining {:.0%} YoY".format(listing.revenue_trend_yoy)

    # Single traffic source > 80%
    if listing.traffic_sources:
        max_share = max(listing.traffic_sources.values(), default=0)
        if max_share > 80:
            return "Single traffic source at {:.0f}%".format(max_share)

    # Active litigation, IP disputes, or unresolved tax compliance issues
    if listing.has_legal_issues:
        return "Active litigation, IP disputes, or tax compliance issues"

    # Asking multiple > 5x annual profit
    if listing.annual_net_profit > 0:
        multiple = listing.asking_price / listing.annual_net_profit
        if multiple > 5:
            return "Asking multiple too high: {:.1f}x".format(multiple)

    # Owner involvement > 40 hrs/week with no team or SOPs
    if listing.owner_hours_per_week > 40 and not listing.has_team:
        return "Owner works {:.0f} hrs/wk with no team".format(listing.owner_hours_per_week)

    # Regulated product categories
    if listing.is_regulated:
        return "Regulated product category"

    return ""


# ── Financial Health (30 points) ────────────────────────────────────────


def _score_financial(listing: Listing) -> int:
    score = 0

    # Profit Multiple: < 2.5x = 15pts, > 4x = 0pts (linear)
    pm = listing.profit_multiple
    if pm != float("inf"):
        if pm <= 2.5:
            score += 15
        elif pm >= 4.0:
            score += 0
        else:
            # Linear interpolation: 15 at 2.5x, 0 at 4.0x
            score += int(round(15 * (4.0 - pm) / 1.5))
    # If profit_multiple is inf (no profit data), 0 points — financials must be real

    # Net Profit Margin: > 25% = 8pts, < 10% = 0pts
    margin = listing.net_margin_pct
    if margin >= 25:
        score += 8
    elif margin <= 10:
        score += 0
    else:
        # Linear: 0 at 10%, 8 at 25%
        score += int(round(8 * (margin - 10) / 15))

    # Revenue Trend: Growing > 10% = 7pts, Declining > 10% = 0pts
    # Linear scale: flat (0%) = 3.5pts
    # Unknown trend (None) = 4pts (neutral — don't penalize missing data)
    trend = listing.revenue_trend_yoy
    if trend is not None:
        if trend >= 0.10:
            score += 7
        elif trend <= -0.10:
            score += 0
        else:
            # Linear: 0 at -10%, 7 at +10%
            score += int(round(7 * (trend + 0.10) / 0.20))
    else:
        score += 4  # neutral — unknown trend

    return min(score, 30)


# ── Traffic & Customer Acquisition (25 points) ─────────────────────────


def _score_traffic(listing: Listing) -> int:
    score = 0

    # Traffic Diversification: 3+ channels none > 40% = 10pts, single > 70% = 0pts
    sources = listing.traffic_sources
    if sources:
        num_channels = len(sources)
        max_share = max(sources.values(), default=0)
        if num_channels >= 3 and max_share <= 40:
            score += 10
        elif max_share >= 70:
            score += 0
        else:
            # Partial credit based on channel count and concentration
            channel_score = min(num_channels, 3) / 3 * 5
            concentration_score = max(0, (70 - max_share) / 30) * 5
            score += int(round(channel_score + concentration_score))
    else:
        # Unknown traffic data — neutral score
        score += 5

    # Organic Traffic %: > 40% = 8pts, < 10% = 0pts
    # Unknown (0) with no traffic_sources → neutral
    organic = listing.organic_traffic_pct
    if organic > 0:
        if organic >= 40:
            score += 8
        elif organic <= 10:
            score += 0
        else:
            # Linear between 10% and 40%
            score += int(round(8 * (organic - 10) / 30))
    elif not sources:
        # No traffic data at all — neutral
        score += 4

    # Email List: > 10K subs with > 20% open rate = 7pts
    # Red flag: explicitly 0 list with some data known = 0pts
    # Unknown data (both 0) → neutral
    if listing.email_list_size == 0 and listing.email_open_rate == 0:
        if not sources:
            # Likely all data is unknown — neutral
            score += 3
        else:
            # We have traffic data but no email list — probably really no list
            score += 0
    elif listing.email_list_size == 0 or listing.email_open_rate < 15:
        score += 0
    elif listing.email_list_size >= 10_000 and listing.email_open_rate >= 20:
        score += 7
    else:
        size_score = min(listing.email_list_size / 10_000, 1.0) * 4
        rate_score = min(listing.email_open_rate / 20, 1.0) * 3
        score += int(round(size_score + rate_score))

    return min(score, 25)


# ── Operational Simplicity (20 points) ──────────────────────────────────


def _score_operations(listing: Listing) -> int:
    score = 0

    # Owner Hours/Week: < 10hrs = 8pts, > 30hrs = 0pts
    # Unknown (0) → neutral (we don't know yet)
    hours = listing.owner_hours_per_week
    if hours > 0:
        if hours <= 10:
            score += 8
        elif hours >= 30:
            score += 0
        else:
            score += int(round(8 * (30 - hours) / 20))
    else:
        score += 4  # unknown — neutral

    # Fulfillment: 3PL/dropship = 6pts, Amazon FBA = 4pts, owner-packed = 0pts
    # Unknown → neutral
    ft = listing.fulfillment_type.lower()
    if ft in ("3pl", "dropship"):
        score += 6
    elif ft in ("fba", "amazon_fba"):
        score += 4
    elif ft == "hybrid":
        score += 3
    elif ft == "owner_packed":
        score += 0
    else:
        score += 3  # unknown — neutral

    # Team/SOPs: Documented SOPs + freelancers on contract = 6pts
    # Unknown → small credit (most established businesses have some process)
    if listing.has_documented_sops and listing.has_team:
        score += 6
    elif listing.has_documented_sops or listing.has_team:
        score += 3
    else:
        # If we have other operational data, this is truly "no team" = 0
        # If all operational data is missing, give neutral
        if hours == 0 and ft == "":
            score += 2  # likely all unknown
        # else: confirmed no SOPs/team = 0

    return min(score, 20)


# ── AI Optimization Upside (15 points) ──────────────────────────────────


def _score_ai_upside(listing: Listing) -> int:
    score = 0

    # Support Volume: 3-tier scoring
    # > 200 tickets/month = 5pts, 50-200 = 3pts, < 50 = 1pt
    # Unknown (0) → moderate score (established businesses likely have support)
    tickets = listing.support_tickets_per_month
    if tickets >= 200:
        score += 5
    elif tickets >= 50:
        score += 3
    elif tickets > 0:
        score += 1
    else:
        score += 3  # unknown — moderate upside assumed

    # Email Sophistication — INVERSE scoring (less sophistication = more upside)
    # Basic flows only = 5pts, Some flows not optimized = 3pts, Full optimized = 1pt
    sophistication = listing.email_sophistication.lower()
    if sophistication in ("basic", ""):
        score += 5
    elif sophistication == "moderate":
        score += 3
    elif sophistication == "advanced":
        score += 1

    # Catalog Complexity:
    # 10-100 SKUs with structured data = 5pts
    # 100-500 SKUs = 3pts, > 500 SKUs = 1pt, < 10 SKUs = 0pts
    # Unknown (0) → moderate score
    skus = listing.sku_count
    if 10 <= skus <= 100:
        score += 5
    elif 100 < skus <= 500:
        score += 3
    elif skus > 500:
        score += 1
    elif skus == 0:
        score += 3  # unknown — moderate
    # 1-9 SKUs: 0 points

    return min(score, 15)


# ── Strategic Fit (10 points) ───────────────────────────────────────────

# Green flag niches: home, outdoors, hobby, lifestyle, pets → 4pts
PREFERRED_NICHES = {
    "home", "outdoors", "outdoor", "hobby", "lifestyle",
    "garden", "camping", "fitness", "pet", "pets", "kitchen",
    "sports", "recreation", "craft", "diy",
}

# Yellow flag niches: general consumer goods, beauty, food, electronics → 2pts
# Note: "electronics" is a broad Flippa category that includes legitimate ecom
# (e.g. tech accessories, gadgets), not just risky electronics reselling
YELLOW_NICHES = {
    "beauty", "food", "consumer", "general", "health",
    "wellness", "cosmetics", "skincare",
    "electronics", "design", "style",
}

# Red flag niches: supplements, fashion (high return rates), regulated → 0pts
RED_NICHES = {
    "supplements", "fashion", "apparel", "clothing",
    "cbd", "medical", "pharmaceutical", "firearms", "weapons",
    "gambling", "casino", "tobacco", "vape", "adult",
    "crypto", "cryptocurrency", "forex",
}


def _score_strategic(listing: Listing) -> int:
    score = 0

    # Niche scoring with 3 tiers
    niche_lower = listing.niche.lower()
    if any(n in niche_lower for n in PREFERRED_NICHES):
        score += 4
    elif any(n in niche_lower for n in RED_NICHES):
        score += 0
    elif any(n in niche_lower for n in YELLOW_NICHES):
        score += 2
    elif niche_lower:
        score += 1  # known niche but not in our lists — small credit
    # unknown niche = 0 pts (can't assess strategic fit without knowing the niche)

    # Platform: Shopify/Shopify Plus = 3pts, WooCommerce = 1pt, others = 0pts
    platform_lower = listing.platform.lower()
    if "shopify" in platform_lower:
        score += 3
    elif "woocommerce" in platform_lower or "woo" in platform_lower:
        score += 1

    # Repeat Customer Rate: > 20% = 3pts, 10-20% = 2pts, < 10% = 0pts
    if listing.repeat_customer_rate >= 20:
        score += 3
    elif listing.repeat_customer_rate >= 10:
        score += 2  # round 1.5 up for int scoring
    # < 10% = 0 points

    return min(score, 10)
