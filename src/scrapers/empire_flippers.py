"""
Empire Flippers data collection module.

Empire Flippers is a curated marketplace — they vet listings before publishing.
They expose a public JSON endpoint for their marketplace listings at:
  https://empireflippers.com/marketplace/

This module fetches listings via their public marketplace page and parses
the structured listing data. Unlike Flippa, EF listings are pre-vetted
with verified financials, so the data quality is higher.
"""

from __future__ import annotations

import re
import time
import requests
from bs4 import BeautifulSoup
from typing import Optional

from src.config import FILTERS, EXCLUDED_INDUSTRIES, ALLOWED_LOCATIONS
from src.models import Listing
from src.logger import get_logger

log = get_logger("empire_flippers")

# Empire Flippers marketplace endpoints
EF_MARKETPLACE_URL = "https://empireflippers.com/marketplace/"
EF_LISTING_BASE = "https://empireflippers.com/listing/"

# Retry config
MAX_RETRIES = 3
RETRY_BACKOFF = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}


class EmpireFlippersClient:
    """Fetches and parses e-commerce listings from Empire Flippers."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def fetch_listings(self, max_pages: int = 5) -> list[Listing]:
        """Fetch ecommerce listings from Empire Flippers marketplace."""
        all_listings: list[Listing] = []
        skipped = {"price": 0, "profit": 0, "age": 0, "industry": 0, "location": 0, "type": 0, "parse": 0}

        for page in range(1, max_pages + 1):
            params = {
                "page": page,
            }

            html = self._request(EF_MARKETPLACE_URL, params)
            if html is None:
                break

            page_listings = self._parse_marketplace_page(html)
            if not page_listings:
                log.info("No more listings at page %d", page)
                break

            for listing in page_listings:
                reason = self._filter_listing(listing)
                if reason:
                    skipped[reason] += 1
                    continue
                all_listings.append(listing)

            time.sleep(2)  # be polite

        log.info(
            "Empire Flippers funnel: %d passed | skipped — price:%d profit:%d age:%d industry:%d location:%d type:%d parse:%d",
            len(all_listings), skipped["price"], skipped["profit"],
            skipped["age"], skipped["industry"], skipped["location"],
            skipped["type"], skipped["parse"],
        )
        log.info("Fetched %d total listings from Empire Flippers", len(all_listings))
        return all_listings

    def _request(self, url: str, params: dict) -> Optional[str]:
        """Make an HTTP request with retry logic."""
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 429:
                    wait = RETRY_BACKOFF * (2 ** attempt)
                    log.warning("Rate limited, retrying in %ds", wait)
                    time.sleep(wait)
                    continue
                log.warning("Request returned status %d: %s", resp.status_code, resp.text[:200])
                return None
            except requests.RequestException as e:
                wait = RETRY_BACKOFF * (2 ** attempt)
                log.warning("Request failed (attempt %d): %s, retrying in %ds", attempt + 1, e, wait)
                time.sleep(wait)
        log.error("All request attempts failed for %s", url)
        return None

    def _parse_marketplace_page(self, html: str) -> list[Listing]:
        """Parse listings from the Empire Flippers marketplace page."""
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # EF uses listing cards with structured data
        # Try multiple selectors for their marketplace layout
        cards = soup.select(
            ".listing-card, .ListingCard, [data-listing-id], "
            ".marketplace-listing, article.listing, .listing-item"
        )

        if not cards:
            # Try finding JSON-LD or embedded listing data
            scripts = soup.find_all("script", type="application/ld+json")
            for script in scripts:
                try:
                    import json
                    data = json.loads(script.string)
                    if isinstance(data, list):
                        for item in data:
                            listing = self._parse_json_listing(item)
                            if listing:
                                listings.append(listing)
                    elif isinstance(data, dict):
                        listing = self._parse_json_listing(data)
                        if listing:
                            listings.append(listing)
                except (json.JSONDecodeError, AttributeError):
                    continue

        for card in cards:
            listing = self._parse_listing_card(card)
            if listing:
                listings.append(listing)

        log.info("Parsed %d listings from Empire Flippers page", len(listings))
        return listings

    def _parse_listing_card(self, card) -> Optional[Listing]:
        """Parse a single listing card from HTML."""
        try:
            # Extract URL
            link = card.select_one("a[href]")
            url = ""
            if link:
                href = link.get("href", "")
                if href.startswith("http"):
                    url = href
                elif href.startswith("/"):
                    url = "https://empireflippers.com{}".format(href)

            # Extract title
            title_el = card.select_one("h2, h3, [class*='title'], [class*='name']")
            title = title_el.get_text(strip=True) if title_el else "Unknown"

            # Extract financial data — EF typically shows monthly net profit and listing price
            price = 0.0
            profit = 0.0
            revenue = 0.0

            # Look for price
            price_el = card.select_one("[class*='price'], [data-price]")
            if price_el:
                price = _extract_dollar_amount(price_el.get_text(strip=True))

            # Look for profit (EF prominently displays monthly net profit)
            profit_el = card.select_one("[class*='profit'], [class*='earnings']")
            if profit_el:
                profit = _extract_dollar_amount(profit_el.get_text(strip=True))

            # Look for revenue
            revenue_el = card.select_one("[class*='revenue']")
            if revenue_el:
                revenue = _extract_dollar_amount(revenue_el.get_text(strip=True))

            # Extract niche/category
            niche = ""
            niche_el = card.select_one("[class*='niche'], [class*='category'], [class*='type']")
            if niche_el:
                niche = niche_el.get_text(strip=True)

            # Extract monetization/business type
            type_el = card.select_one("[class*='monetization'], [class*='model']")
            biz_type = type_el.get_text(strip=True) if type_el else ""

            # Extract description snippet
            desc_el = card.select_one("[class*='description'], [class*='summary'], p")
            description = desc_el.get_text(strip=True) if desc_el else ""

            listing = Listing(
                url=url,
                source="empire_flippers",
                business_name=title,
                listing_title=title,
                description=description,
                niche=niche or biz_type,
                asking_price=price,
                monthly_revenue=revenue,
                monthly_net_profit=profit,
                annual_revenue=revenue * 12,
                annual_net_profit=profit * 12,
            )
            return listing
        except Exception as e:
            log.warning("Failed to parse EF listing card: %s", e)
            return None

    def _parse_json_listing(self, data: dict) -> Optional[Listing]:
        """Parse a listing from JSON-LD or embedded JSON data."""
        try:
            url = data.get("url", "")
            title = data.get("name", data.get("title", "Unknown"))
            description = data.get("description", "")
            price = _extract_dollar_amount(str(data.get("price", data.get("offers", {}).get("price", 0))))

            listing = Listing(
                url=url,
                source="empire_flippers",
                business_name=title,
                listing_title=title,
                description=description,
                asking_price=price,
            )
            return listing
        except Exception as e:
            log.warning("Failed to parse EF JSON listing: %s", e)
            return None

    def fetch_listing_details(self, listing: Listing) -> Listing:
        """Enrich a listing with full description from its detail page."""
        if not listing.url:
            return listing

        html = self._request(listing.url, {})
        if html is None:
            return listing

        soup = BeautifulSoup(html, "html.parser")

        # EF detail pages have rich descriptions
        if not listing.description:
            desc = _extract_description(soup)
            if desc:
                listing.description = desc

        # Try to get more financial details
        for label, field in [("niche", "niche"), ("location", "seller_location")]:
            val = _extract_text_by_label(soup, label)
            if val:
                setattr(listing, field, val)

        log.info("Enriched EF listing: %s", listing.business_name[:60])
        return listing

    def _filter_listing(self, listing: Listing) -> str:
        """Apply client-side filters. Returns skip reason or '' if it passes."""
        # Price range
        if listing.asking_price > 0:
            if listing.asking_price > FILTERS.get("max_price", 500_000):
                return "price"
            if listing.asking_price < FILTERS.get("min_price", 50_000):
                return "price"

        # Profit filter
        min_profit = FILTERS.get("min_monthly_profit", 5_000)
        if listing.monthly_net_profit > 0:
            if listing.monthly_net_profit < min_profit:
                return "profit"
        elif listing.monthly_revenue > 0:
            min_revenue_proxy = min_profit / 0.20
            if listing.monthly_revenue < min_revenue_proxy:
                return "profit"

        # Excluded industries
        if listing.niche:
            niche_lower = listing.niche.lower()
            if any(excl in niche_lower for excl in EXCLUDED_INDUSTRIES):
                return "industry"

        # EF is US-focused but check location if available
        if listing.seller_location:
            location_lower = listing.seller_location.lower()
            if not any(re.search(r'\b' + re.escape(loc) + r'\b', location_lower) for loc in ALLOWED_LOCATIONS):
                return "location"

        return ""


# ── Utility functions ───────────────────────────────────────────────────


def _extract_dollar_amount(text: str) -> float:
    """Extract a numeric dollar amount from text."""
    if not text:
        return 0.0
    text = text.replace(",", "").replace("$", "").strip()
    match = re.search(r"([\d.]+)\s*([KkMm])?", text)
    if match:
        num = float(match.group(1))
        suffix = (match.group(2) or "").upper()
        if suffix == "K":
            num *= 1_000
        elif suffix == "M":
            num *= 1_000_000
        return num
    return 0.0


def _extract_text_by_label(soup: BeautifulSoup, *labels: str) -> str:
    """Find a value on a detail page by looking for label text."""
    for label in labels:
        for el in soup.find_all(string=re.compile(label, re.IGNORECASE)):
            parent = el.parent
            if parent:
                sibling = parent.find_next_sibling()
                if sibling:
                    return sibling.get_text(strip=True)
                next_el = parent.find_next()
                if next_el and next_el != parent:
                    return next_el.get_text(strip=True)
    return ""


def _extract_description(soup: BeautifulSoup) -> str:
    """Extract the main listing description from an EF detail page."""
    for selector in [
        ".listing-description", ".listing-summary",
        "[class*='description']", "[class*='summary']",
        "article", ".main-content", ".content-area",
    ]:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 50:
                return text[:5000]

    paragraphs = soup.find_all("p")
    if paragraphs:
        longest = max(paragraphs, key=lambda p: len(p.get_text(strip=True)))
        text = longest.get_text(strip=True)
        if len(text) > 50:
            return text[:5000]

    return ""
