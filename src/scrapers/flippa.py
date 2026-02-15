"""
Flippa data collection module.

Uses Flippa's search API endpoint to fetch e-commerce listings.
Falls back to HTML scraping if the API is unavailable or rate-limited.
"""

from __future__ import annotations

import re
import time
import requests
from bs4 import BeautifulSoup
from typing import Optional

from src.config import FLIPPA_API_KEY, FILTERS
from src.models import Listing
from src.logger import get_logger

log = get_logger("flippa")

# Flippa's public search API endpoint
FLIPPA_SEARCH_URL = "https://flippa.com/search"
FLIPPA_API_URL = "https://api.flippa.com/v3/listings"

# Retry config
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds, doubles each retry

# Request headers
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class FlippaClient:
    """Fetches and parses e-commerce listings from Flippa."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        if FLIPPA_API_KEY:
            self.session.headers["Authorization"] = f"Bearer {FLIPPA_API_KEY}"

    def fetch_listings(self, max_pages: int = 5) -> list[Listing]:
        """Fetch listings using the API, falling back to scraping."""
        listings = self._fetch_via_api(max_pages)
        if not listings:
            log.info("API returned no results, falling back to web scraping")
            listings = self._fetch_via_scraping(max_pages)
        log.info("Fetched %d total listings from Flippa", len(listings))
        return listings

    # ── API-based fetching ──────────────────────────────────────────────

    def _fetch_via_api(self, max_pages: int) -> list[Listing]:
        """Fetch listings from the Flippa search API."""
        all_listings: list[Listing] = []

        for page in range(1, max_pages + 1):
            params = self._build_api_params(page)
            data = self._api_request(FLIPPA_API_URL, params)

            # If price filters are rejected, retry without them (filter client-side)
            if data is None and page == 1:
                log.info("Retrying API without price filters...")
                params.pop("filter[price][min]", None)
                params.pop("filter[price][max]", None)
                data = self._api_request(FLIPPA_API_URL, params)

            if data is None:
                break

            results = data.get("data", [])
            if not results:
                log.info("No more results at page %d", page)
                break

            # On first page, log a sample response so we can see available fields
            if page == 1 and results:
                sample = results[0]
                log.info("Sample API listing keys: %s", list(sample.keys()))
                attrs = sample.get("attributes", sample)
                log.info("Sample attributes keys: %s", list(attrs.keys()) if isinstance(attrs, dict) else "N/A")
                log.info("Sample listing: %s", {k: attrs.get(k) for k in list(attrs.keys())[:20]} if isinstance(attrs, dict) else str(sample)[:500])

            for item in results:
                listing = self._parse_api_listing(item)
                if listing and self._passes_basic_filters(listing):
                    all_listings.append(listing)

            # Respect rate limits
            total_pages = data.get("meta", {}).get("total_pages", page)
            if page >= total_pages:
                break
            time.sleep(1)

        return all_listings

    def _build_api_params(self, page: int) -> dict:
        """Build query parameters for the Flippa API."""
        return {
            "filter[property_type]": "ecommerce",
            "filter[sitetype]": "established",
            "filter[price][min]": FILTERS["min_price"],
            "filter[price][max]": FILTERS["max_price"],
            "page[number]": page,
            "page[size]": 50,
        }

    def _api_request(self, url: str, params: dict) -> Optional[dict]:
        """Make an API request with retry logic."""
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.get(url, params=params, timeout=30)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 429:
                    wait = RETRY_BACKOFF * (2 ** attempt)
                    log.warning("Rate limited, retrying in %ds", wait)
                    time.sleep(wait)
                    continue
                log.error("API returned status %d: %s", resp.status_code, resp.text[:200])
                return None
            except requests.RequestException as e:
                wait = RETRY_BACKOFF * (2 ** attempt)
                log.warning("Request failed (attempt %d): %s, retrying in %ds", attempt + 1, e, wait)
                time.sleep(wait)
        log.error("All API request attempts failed")
        return None

    def _parse_api_listing(self, item: dict) -> Optional[Listing]:
        """Parse a single listing from the Flippa API response."""
        try:
            attrs = item.get("attributes", item)

            # Extract financial data
            asking_price = _to_float(attrs.get("price", attrs.get("current_price", 0)))
            monthly_revenue = _to_float(attrs.get("average_monthly_revenue", 0))
            monthly_profit = _to_float(attrs.get("average_monthly_profit", 0))

            # Build listing URL
            listing_id = item.get("id", "")
            slug = attrs.get("slug", attrs.get("title", "")).lower().replace(" ", "-")
            url = f"https://flippa.com/listing/{listing_id}" if listing_id else ""

            # Extract traffic breakdown if available
            traffic_sources = {}
            traffic_data = attrs.get("traffic_sources", {})
            if isinstance(traffic_data, dict):
                traffic_sources = {
                    k: float(v) for k, v in traffic_data.items() if _to_float(v) > 0
                }

            listing = Listing(
                url=url,
                source="flippa",
                business_name=attrs.get("title", "Unknown"),
                niche=attrs.get("category", attrs.get("industry", "")),
                asking_price=asking_price,
                monthly_revenue=monthly_revenue,
                monthly_net_profit=monthly_profit,
                annual_revenue=monthly_revenue * 12,
                annual_net_profit=monthly_profit * 12,
                revenue_trend_yoy=_to_float(attrs.get("revenue_trend", None)),
                platform=attrs.get("platform", attrs.get("site_type", "")),
                business_age_months=_to_int(attrs.get("age_months", attrs.get("established_for", 0))),
                traffic_sources=traffic_sources,
                organic_traffic_pct=_to_float(attrs.get("organic_traffic_percentage", 0)),
                email_list_size=_to_int(attrs.get("email_subscribers", 0)),
                email_open_rate=_to_float(attrs.get("email_open_rate", 0)),
                owner_hours_per_week=_to_float(attrs.get("owner_hours_per_week", 0)),
                fulfillment_type=_normalize_fulfillment(attrs.get("fulfillment", "")),
                has_documented_sops=bool(attrs.get("has_sops", False)),
                has_team=bool(attrs.get("has_team", attrs.get("employees", 0))),
                support_tickets_per_month=_to_int(attrs.get("support_tickets_monthly", 0)),
                sku_count=_to_int(attrs.get("sku_count", attrs.get("products_count", 0))),
                repeat_customer_rate=_to_float(attrs.get("repeat_customer_rate", 0)),
                is_regulated=bool(attrs.get("is_regulated", False)),
            )
            return listing
        except Exception as e:
            log.warning("Failed to parse API listing: %s", e)
            return None

    # ── Web scraping fallback ───────────────────────────────────────────

    def _fetch_via_scraping(self, max_pages: int) -> list[Listing]:
        """Fall back to scraping the Flippa search results page."""
        all_listings: list[Listing] = []

        for page in range(1, max_pages + 1):
            params = {
                "filter[property_type]": "ecommerce",
                "filter[sitetype]": "established",
                "filter[price][min]": FILTERS["min_price"],
                "filter[price][max]": FILTERS["max_price"],
                "page": page,
            }
            html = self._scrape_request(FLIPPA_SEARCH_URL, params)
            if html is None:
                break

            page_listings = self._parse_search_page(html)
            if not page_listings:
                break

            for listing in page_listings:
                if self._passes_basic_filters(listing):
                    all_listings.append(listing)

            time.sleep(2)  # be polite

        return all_listings

    def _scrape_request(self, url: str, params: dict) -> Optional[str]:
        """Make a scraping request with retries."""
        scrape_headers = {**HEADERS, "Accept": "text/html"}
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.get(url, params=params, headers=scrape_headers, timeout=30)
                if resp.status_code == 200:
                    return resp.text
                log.warning("Scrape returned status %d", resp.status_code)
                if resp.status_code == 429:
                    time.sleep(RETRY_BACKOFF * (2 ** attempt))
                    continue
                return None
            except requests.RequestException as e:
                log.warning("Scrape failed (attempt %d): %s", attempt + 1, e)
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
        return None

    def _parse_search_page(self, html: str) -> list[Listing]:
        """Parse listings from a Flippa search results HTML page."""
        soup = BeautifulSoup(html, "html.parser")
        listings = []

        # Flippa uses listing cards with data attributes
        cards = soup.select("[data-listing-id], .ListingCard, .Listing__card")
        for card in cards:
            listing = self._parse_html_card(card)
            if listing:
                listings.append(listing)

        log.info("Parsed %d listings from HTML page", len(listings))
        return listings

    def _parse_html_card(self, card) -> Optional[Listing]:
        """Parse a single listing card from HTML."""
        try:
            # Extract listing URL
            link = card.select_one("a[href*='/listing/'], a[href*='flippa.com']")
            url = ""
            if link:
                href = link.get("href", "")
                url = href if href.startswith("http") else f"https://flippa.com{href}"

            # Extract title
            title_el = card.select_one(
                ".ListingCard__title, .Listing__title, h3, h2, [class*='title']"
            )
            title = title_el.get_text(strip=True) if title_el else "Unknown"

            # Extract price
            price_el = card.select_one(
                "[class*='price'], [data-price], .ListingCard__price"
            )
            asking_price = _extract_dollar_amount(
                price_el.get_text(strip=True) if price_el else "0"
            )

            # Extract revenue
            revenue_el = card.select_one("[class*='revenue'], [data-revenue]")
            monthly_revenue = _extract_dollar_amount(
                revenue_el.get_text(strip=True) if revenue_el else "0"
            )

            # Extract profit
            profit_el = card.select_one("[class*='profit'], [data-profit]")
            monthly_profit = _extract_dollar_amount(
                profit_el.get_text(strip=True) if profit_el else "0"
            )

            # Determine if it's a yearly or monthly figure based on label context
            # Flippa typically shows monthly figures in cards
            listing = Listing(
                url=url,
                source="flippa",
                business_name=title,
                asking_price=asking_price,
                monthly_revenue=monthly_revenue,
                monthly_net_profit=monthly_profit,
                annual_revenue=monthly_revenue * 12,
                annual_net_profit=monthly_profit * 12,
            )
            return listing
        except Exception as e:
            log.warning("Failed to parse HTML card: %s", e)
            return None

    def fetch_listing_details(self, listing: Listing) -> Listing:
        """Enrich a listing with detailed data from its individual page."""
        if not listing.url:
            return listing

        html = self._scrape_request(listing.url, {})
        if html is None:
            return listing

        soup = BeautifulSoup(html, "html.parser")

        # Try to extract additional detail fields
        listing.platform = _extract_text_by_label(soup, "platform") or listing.platform
        listing.niche = _extract_text_by_label(soup, "category", "industry", "niche") or listing.niche

        age_text = _extract_text_by_label(soup, "age", "established")
        if age_text:
            listing.business_age_months = _parse_age_to_months(age_text)

        hours_text = _extract_text_by_label(soup, "hours", "owner time")
        if hours_text:
            listing.owner_hours_per_week = _to_float(re.sub(r"[^\d.]", "", hours_text))

        fulfillment_text = _extract_text_by_label(soup, "fulfillment", "shipping")
        if fulfillment_text:
            listing.fulfillment_type = _normalize_fulfillment(fulfillment_text)

        log.info("Enriched listing: %s", listing.business_name)
        return listing

    # ── Filtering ───────────────────────────────────────────────────────

    def _passes_basic_filters(self, listing: Listing) -> bool:
        """Check if listing passes basic price/model filters."""
        if listing.asking_price < FILTERS["min_price"]:
            return False
        if listing.asking_price > FILTERS["max_price"]:
            return False
        # Platform filter — skip if platform unknown (will be checked after enrichment)
        return True


# ── Utility functions ───────────────────────────────────────────────────


def _to_float(val) -> float:
    if val is None:
        return 0.0
    try:
        if isinstance(val, str):
            val = re.sub(r"[,$%]", "", val)
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _to_int(val) -> int:
    return int(_to_float(val))


def _extract_dollar_amount(text: str) -> float:
    """Extract a numeric dollar amount from text like '$250,000' or '250K'."""
    if not text:
        return 0.0
    text = text.replace(",", "").replace("$", "").strip()
    # Handle K/M suffixes
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


def _normalize_fulfillment(text: str) -> str:
    """Normalize fulfillment type strings."""
    if not text:
        return ""
    text = text.lower()
    if "3pl" in text or "third" in text:
        return "3pl"
    if "drop" in text:
        return "dropship"
    if "fba" in text or "amazon" in text:
        return "fba"
    if "owner" in text or "self" in text or "in-house" in text:
        return "owner_packed"
    if "hybrid" in text:
        return "hybrid"
    return text


def _extract_text_by_label(soup: BeautifulSoup, *labels: str) -> str:
    """Find a value on a detail page by looking for label text."""
    for label in labels:
        # Look for dt/dd pairs, label/value pairs, table rows
        for el in soup.find_all(string=re.compile(label, re.IGNORECASE)):
            parent = el.parent
            if parent:
                # Try next sibling
                sibling = parent.find_next_sibling()
                if sibling:
                    return sibling.get_text(strip=True)
                # Try parent's next element
                next_el = parent.find_next()
                if next_el and next_el != parent:
                    return next_el.get_text(strip=True)
    return ""


def _parse_age_to_months(text: str) -> int:
    """Parse age text like '3 years 2 months' to total months."""
    months = 0
    year_match = re.search(r"(\d+)\s*y", text, re.IGNORECASE)
    month_match = re.search(r"(\d+)\s*m", text, re.IGNORECASE)
    if year_match:
        months += int(year_match.group(1)) * 12
    if month_match:
        months += int(month_match.group(1))
    if not year_match and not month_match:
        # Try bare number
        num_match = re.search(r"(\d+)", text)
        if num_match:
            months = int(num_match.group(1))
    return months
