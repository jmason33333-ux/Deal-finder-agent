"""
Gemini AI analysis for high-scoring e-commerce listings.

Uses Gemini 2.5 Flash via REST API (no SDK needed) for qualitative
analysis including strengths, weaknesses, AI optimization opportunities,
growth moves, and a buy/pass verdict.
"""

from __future__ import annotations

import requests

from src.config import GEMINI_API_KEY, GEMINI_MODEL, SCORE_THRESHOLD_AI_ANALYSIS
from src.models import Listing
from src.logger import get_logger

log = get_logger("ai_analyzer")

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """\
You are an expert e-commerce acquisition analyst. Analyze this business listing for a buyer with the following profile:
- Senior Program Manager at Stripe with 10+ years building support operations
- Deep expertise in vendor management, AI operations, and workflow automation
- Running this alongside a full-time job (< 15 hours/week available)
- Wife has design/creative skills and works at an e-commerce development agency
- Budget: $100K-$500K acquisition price
- Goal: Generate $5-10K/month net profit, use as AI optimization lab, document playbook for productization

Analyze the listing and provide:

1. **STRENGTHS** (2-3 specific strengths with data points from the listing)
2. **WEAKNESSES** (2-3 specific risks or concerns identified)
3. **AI OPTIMIZATION OPPORTUNITY** (specific workflows that can be automated and estimated monthly savings in dollars)
4. **TOP 3 GROWTH MOVES** (specific, actionable strategies with projected ROI):
   - Move 1: [Immediate win, implementable in 30 days]
   - Move 2: [Medium-term growth lever, 60-90 days]
   - Move 3: [Strategic play, 90-180 days]
5. **VERDICT**: STRONG BUY / INVESTIGATE / CONDITIONAL / PASS with 1-sentence rationale
6. **ESTIMATED 12-MONTH ROI**: Based on asking price and projected improvements"""


def analyze_listing(listing: Listing) -> Listing:
    """Run Gemini AI analysis on a single listing. Populates ai_analysis fields."""
    if listing.total_score < SCORE_THRESHOLD_AI_ANALYSIS:
        log.info(
            "Skipping AI analysis for [%s] — score %d < %d threshold",
            listing.business_name,
            listing.total_score,
            SCORE_THRESHOLD_AI_ANALYSIS,
        )
        return listing

    if not GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY not set — skipping AI analysis")
        return listing

    listing_summary = _build_listing_summary(listing)
    log.info("Requesting Gemini analysis for [%s]...", listing.business_name)

    try:
        url = GEMINI_API_URL.format(model=GEMINI_MODEL)
        payload = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "contents": [
                {
                    "parts": [
                        {
                            "text": f"Analyze this e-commerce business listing for acquisition:\n\n{listing_summary}"
                        }
                    ]
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 2000,
                "temperature": 0.7,
            },
        }

        resp = requests.post(
            url,
            json=payload,
            params={"key": GEMINI_API_KEY},
            timeout=60,
        )

        if resp.status_code != 200:
            log.error(
                "Gemini API error for [%s]: %d %s",
                listing.business_name,
                resp.status_code,
                resp.text[:300],
            )
            return listing

        data = resp.json()
        response_text = data["candidates"][0]["content"]["parts"][0]["text"]
        _parse_ai_response(listing, response_text)
        log.info("AI analysis complete for [%s]", listing.business_name)

    except (KeyError, IndexError) as e:
        log.error("Failed to parse Gemini response for [%s]: %s", listing.business_name, e)
    except requests.RequestException as e:
        log.error("Gemini request failed for [%s]: %s", listing.business_name, e)

    return listing


def analyze_batch(listings: list[Listing]) -> list[Listing]:
    """Run AI analysis on all qualifying listings in a batch."""
    qualifying = [l for l in listings if l.total_score >= SCORE_THRESHOLD_AI_ANALYSIS]
    log.info(
        "%d of %d listings qualify for AI analysis (score >= %d)",
        len(qualifying),
        len(listings),
        SCORE_THRESHOLD_AI_ANALYSIS,
    )
    for listing in qualifying:
        analyze_listing(listing)
    return listings


def _build_listing_summary(listing: Listing) -> str:
    """Build a structured text summary of the listing for the AI prompt."""
    traffic_str = ", ".join(
        f"{src}: {pct:.0f}%" for src, pct in listing.traffic_sources.items()
    )
    breakdown_str = ", ".join(
        f"{k}: {v}pts" for k, v in listing.score_breakdown.items()
    )

    return f"""BUSINESS: {listing.business_name}
SOURCE: {listing.source}
URL: {listing.url}
NICHE: {listing.niche}

FINANCIALS:
- Asking Price: ${listing.asking_price:,.0f}
- Monthly Revenue: ${listing.monthly_revenue:,.0f}
- Monthly Net Profit: ${listing.monthly_net_profit:,.0f}
- Annual Revenue: ${listing.annual_revenue:,.0f}
- Annual Net Profit: ${listing.annual_net_profit:,.0f}
- Profit Multiple: {listing.profit_multiple:.1f}x
- Net Profit Margin: {listing.net_margin_pct:.1f}%
- Revenue Trend (YoY): {_format_trend(listing.revenue_trend_yoy)}

PLATFORM & OPERATIONS:
- Platform: {listing.platform}
- Business Age: {listing.business_age_months} months
- Owner Hours/Week: {listing.owner_hours_per_week:.0f}
- Fulfillment: {listing.fulfillment_type}
- Documented SOPs: {"Yes" if listing.has_documented_sops else "No"}
- Has Team/Contractors: {"Yes" if listing.has_team else "No"}

TRAFFIC & MARKETING:
- Traffic Sources: {traffic_str or "Not specified"}
- Organic Traffic: {listing.organic_traffic_pct:.0f}%
- Email List Size: {listing.email_list_size:,}
- Email Open Rate: {listing.email_open_rate:.0f}%
- Email Sophistication: {listing.email_sophistication or "Unknown"}

PRODUCT & CUSTOMERS:
- SKU Count: {listing.sku_count}
- Repeat Customer Rate: {listing.repeat_customer_rate:.0f}%
- Support Tickets/Month: {listing.support_tickets_per_month}

SCORING:
- Total Score: {listing.total_score}/100
- Breakdown: {breakdown_str}"""


def _format_trend(trend) -> str:
    if trend is None:
        return "Unknown"
    return f"{trend:+.0%}"


def _parse_ai_response(listing: Listing, response: str) -> None:
    """Parse the AI response and populate listing fields."""
    listing.ai_analysis = response

    # Extract growth moves section
    growth_start = response.find("TOP 3 GROWTH MOVES")
    if growth_start == -1:
        growth_start = response.find("GROWTH MOVES")
    verdict_start = response.find("VERDICT")

    if growth_start != -1 and verdict_start != -1:
        listing.top_growth_moves = response[growth_start:verdict_start].strip()
    elif growth_start != -1:
        listing.top_growth_moves = response[growth_start:growth_start + 500].strip()

    # Extract estimated ROI
    roi_start = response.find("12-MONTH ROI")
    if roi_start == -1:
        roi_start = response.find("ESTIMATED")
    if roi_start != -1:
        # Take the rest of the response from ROI marker
        roi_text = response[roi_start:]
        # Limit to a reasonable length
        listing.estimated_12mo_roi = roi_text[:300].strip()
