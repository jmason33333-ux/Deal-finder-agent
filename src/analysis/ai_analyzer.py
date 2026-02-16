"""
AI listing summarizer using Gemini.

For every qualified listing, reads the title + description + financials
and produces a concise summary with:
  - What the business sells
  - Key strengths and risks
  - Growth potential / AI optimization angle
  - A verdict (STRONG BUY / INVESTIGATE / PASS)
"""

from __future__ import annotations

import requests

from src.config import GEMINI_API_KEY, GEMINI_MODEL
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
- Goal: Generate $5-10K/month net profit, use as AI optimization lab

Read the listing title, description, and financials carefully. Provide a structured analysis:

1. **SUMMARY** (2-3 sentences): What does this business sell? What's the business model?
2. **STRENGTHS** (2-3 bullet points): Key positives from the listing with specific data points
3. **RISKS** (2-3 bullet points): Concerns, red flags, or unknowns
4. **GROWTH MOVES** (2-3 bullet points): Specific things the buyer could do to grow this, especially AI/automation opportunities
5. **VERDICT**: STRONG BUY / INVESTIGATE / PASS — with a 1-sentence rationale

Keep the total response under 400 words. Be direct and specific, not generic."""


def analyze_listing(listing: Listing) -> Listing:
    """Run Gemini AI analysis on a single qualified listing."""
    if not GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY not set — skipping AI analysis")
        return listing

    prompt_text = _build_prompt(listing)
    log.info("Requesting AI summary for [%s]...", listing.business_name[:50])

    try:
        url = GEMINI_API_URL.format(model=GEMINI_MODEL)
        payload = {
            "system_instruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "contents": [
                {
                    "parts": [{"text": prompt_text}]
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 1500,
                "temperature": 0.5,
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
        _parse_response(listing, response_text)
        log.info("AI summary complete for [%s]", listing.business_name[:50])

    except (KeyError, IndexError) as e:
        log.error("Failed to parse Gemini response for [%s]: %s", listing.business_name, e)
    except requests.RequestException as e:
        log.error("Gemini request failed for [%s]: %s", listing.business_name, e)

    return listing


def analyze_batch(listings: list[Listing]) -> list[Listing]:
    """Run AI analysis on all qualified listings."""
    qualified = [l for l in listings if l.qualified]
    log.info(
        "%d of %d listings qualified for AI analysis",
        len(qualified),
        len(listings),
    )
    for listing in qualified:
        analyze_listing(listing)
    return listings


def _build_prompt(listing: Listing) -> str:
    """Build the prompt from listing data."""
    desc_section = ""
    if listing.description:
        # Truncate description to keep prompt reasonable
        desc = listing.description[:3000]
        desc_section = f"\nLISTING DESCRIPTION:\n{desc}\n"

    return f"""Analyze this e-commerce business listing for acquisition:

LISTING TITLE: {listing.listing_title or listing.business_name}
SOURCE: {listing.source}
URL: {listing.url}
NICHE: {listing.niche or "Not specified"}
LOCATION: {listing.seller_location or "Not specified"}
{desc_section}
FINANCIALS:
- Asking Price: ${listing.asking_price:,.0f}
- Monthly Revenue: ${listing.monthly_revenue:,.0f}
- Monthly Net Profit: ${listing.monthly_net_profit:,.0f}
- Annual Revenue: ${listing.annual_revenue:,.0f}
- Annual Net Profit: ${listing.annual_net_profit:,.0f}
- Profit Multiple: {listing.profit_multiple:.1f}x
- Net Margin: {listing.net_margin_pct:.1f}%
- Revenue Trend (YoY): {_format_trend(listing.revenue_trend_yoy)}

BUSINESS AGE: {listing.business_age_months} months ({listing.business_age_months // 12} years)
PLATFORM: {listing.platform or "Not specified"}"""


def _format_trend(trend) -> str:
    if trend is None:
        return "Unknown"
    return f"{trend:+.0%}"


def _parse_response(listing: Listing, response: str) -> None:
    """Parse the AI response and populate listing fields."""
    listing.ai_summary = response

    # Extract verdict
    verdict_start = response.upper().find("VERDICT")
    if verdict_start != -1:
        verdict_line = response[verdict_start:verdict_start + 200]
        if "STRONG BUY" in verdict_line.upper():
            listing.ai_verdict = "STRONG BUY"
        elif "INVESTIGATE" in verdict_line.upper():
            listing.ai_verdict = "INVESTIGATE"
        elif "PASS" in verdict_line.upper():
            listing.ai_verdict = "PASS"

    # Extract strengths section
    strengths_start = response.upper().find("STRENGTH")
    risks_start = response.upper().find("RISK")
    growth_start = response.upper().find("GROWTH")

    if strengths_start != -1:
        end = risks_start if risks_start != -1 else growth_start if growth_start != -1 else strengths_start + 500
        listing.ai_strengths = response[strengths_start:end].strip()[:500]

    if risks_start != -1:
        end = growth_start if growth_start != -1 else verdict_start if verdict_start != -1 else risks_start + 500
        listing.ai_risks = response[risks_start:end].strip()[:500]

    if growth_start != -1:
        end = verdict_start if verdict_start != -1 else growth_start + 500
        listing.ai_growth_moves = response[growth_start:end].strip()[:500]
