"""
Flippa data collection module.

Uses Flippa's search API endpoint to fetch e-commerce listings.
Falls back to HTML scraping if the API is unavailable or rate-limited.

Filter strategy: broad API query → client-side funnel
  1. API: fetch all listings up to $500k (no property_type filter — it returns 0)
  2. Client: price <= $500k, profit >= $5k/month
  3. Client: seller_location in North America
  4. Client: industry not in excluded set
"""

from __future__ import annotations

import re
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from typing import Optional

from src.config import FLIPPA_API_KEY, FILTERS, EXCLUDED_INDUSTRIES
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

    def fetch_listings(self, max_pages: int = 20) -> list[Listing]:
        """Fetch listings using the API, falling back to scraping."""
        listings = self._fetch_via_api(max_pages)
        if not listings:
            log.info("API returned no qualifying results, falling back to web scraping")
            listings = self._fetch_via_scraping(max_pages)
        log.info("Fetched %d total listings from Flippa", len(listings))
        return listings

    # ── API-based fetching ──────────────────────────────────────────────

    def _fetch_via_api(self, max_pages: int) -> list[Listing]:
        """Fetch listings from the Flippa search API with broad query."""
        all_listings: list[Listing] = []
        skipped = {"price": 0, "profit": 0, "industry": 0, "parse": 0}
        # Track notable skips for diagnostics
        notable_skips: list[str] = []

        for page in range(1, max_pages + 1):
            params = self._build_api_params(page)
            data = self._api_request(FLIPPA_API_URL, params)

            if data is None:
                break

            # Log response structure on first page
            if page == 1:
                meta = data.get("meta", {})
                total = meta.get("total_results", "?")
                log.info("API returned %s total results across all pages", total)

            results = data.get("data", [])
            if not results:
                log.info("No more results at page %d", page)
                break

            for item in results:
                listing = self._parse_api_listing(item)
                if listing is None:
                    skipped["parse"] += 1
                    continue

                # Client-side funnel
                reason = self._filter_listing(listing)
                if reason:
                    skipped[reason] += 1
                    # Log notable skips — listings with revenue but filtered out
                    if reason == "profit" and listing.monthly_revenue >= 10_000:
                        notable_skips.append(
                            "  SKIP[profit] rev={:,.0f}/mo profit={:,.0f}/mo price={:,.0f} — {}".format(
                                listing.monthly_revenue, listing.monthly_net_profit,
                                listing.asking_price, listing.business_name[:60],
                            )
                        )
                    continue

                all_listings.append(listing)

            # Check if there are more pages
            total_results = data.get("meta", {}).get("total_results", 0)
            fetched_so_far = page * 50
            if fetched_so_far >= total_results:
                break
            time.sleep(1)  # respect rate limits

        log.info(
            "Funnel: %d passed | skipped — price:%d profit:%d industry:%d parse:%d",
            len(all_listings), skipped["price"], skipped["profit"],
            skipped["industry"], skipped["parse"],
        )

        # Show notable skips so user can see what's being missed
        if notable_skips:
            log.info("Notable skips (had revenue >= $10k/mo but failed profit filter):")
            for skip in notable_skips[:15]:
                log.info("%s", skip)

        return all_listings

    def _build_api_params(self, page: int) -> dict:
        """Build query parameters for Flippa API.

        Based on diagnostic testing (scripts/diagnose_api.py):
        - sort param is IGNORED by the API (all sort values return same order)
        - filter[property_type]=ecommerce_store works and narrows to ecom only
        - filter[status]=open works to get only active listings
        - filter[price][min/max] works to narrow price range
        """
        params = {
            "page[number]": page,
            "page[size]": 50,
            # Only ecommerce stores (confirmed working value from API diagnostics)
            "filter[property_type]": "ecommerce_store",
            # Only active listings
            "filter[status]": "open",
        }
        # Price filter
        max_price = FILTERS.get("max_price")
        if max_price:
            params["filter[price][max]"] = max_price
        params["filter[price][min]"] = FILTERS.get("min_price", 50_000)
        return params

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
        """Parse a single listing from the Flippa API response.

        Actual API fields (from live response):
            id, title, summary, html_url, display_price, current_price,
            profit_per_month, revenue_per_month, average_profit, average_revenue,
            industry, property_type, business_model, seller_location,
            established_at, has_verified_revenue, has_verified_traffic,
            page_views_per_month, uniques_per_month, revenue_sources
        """
        try:
            # The API returns flat objects (no "attributes" wrapper)
            attrs = item.get("attributes", item)

            # Financial data — use monthly fields, fall back to averages
            asking_price = _to_float(attrs.get("display_price") or attrs.get("current_price", 0))
            monthly_revenue = _to_float(attrs.get("revenue_per_month") or attrs.get("average_revenue", 0))
            monthly_profit = _to_float(attrs.get("profit_per_month") or attrs.get("average_profit", 0))

            # URL — use html_url directly (constructing our own gives 404s)
            url = attrs.get("html_url", "")
            if not url and item.get("id"):
                url = "https://flippa.com/{}".format(item["id"])

            # Business age from established_at timestamp
            age_months = 0
            established_at = attrs.get("established_at")
            if established_at:
                try:
                    # Handle ISO format: "2026-01-01T11:00:00+11:00"
                    est_str = established_at.split("T")[0]
                    est_date = datetime.strptime(est_str, "%Y-%m-%d")
                    now = datetime.now()
                    age_months = max(0, (now.year - est_date.year) * 12 + (now.month - est_date.month))
                except (ValueError, IndexError):
                    pass

            # Seller location
            seller_location = attrs.get("seller_location", "") or ""

            # Industry / niche
            industry = attrs.get("industry", "") or ""

            # Property type / business model
            property_type = attrs.get("property_type", "") or ""
            business_model = attrs.get("business_model", "") or ""

            listing = Listing(
                url=url,
                source="flippa",
                business_name=attrs.get("title", "Unknown") or "Unknown",
                niche=industry,
                asking_price=asking_price,
                monthly_revenue=monthly_revenue,
                monthly_net_profit=monthly_profit,
                annual_revenue=monthly_revenue * 12,
                annual_net_profit=monthly_profit * 12,
                platform=property_type,
                business_age_months=age_months,
                seller_location=seller_location,
            )
            return listing
        except Exception as e:
            log.warning("Failed to parse API listing: %s", e)
            return None

    # ── Client-side filtering funnel ────────────────────────────────────

    def _filter_listing(self, listing: Listing) -> str:
        """Apply client-side filters. Returns skip reason or '' if it passes.

        Profit filter strategy: Many Flippa API listings report profit_per_month=0
        even for profitable businesses (the real data is on the detail page).
        So we use a fallback: if profit is 0 but monthly revenue is high enough
        that a 20% margin would clear our threshold, let it through for enrichment.
        """
        # Price cap
        if listing.asking_price > FILTERS.get("max_price", 500_000):
            return "price"

        # Minimum monthly profit — with revenue fallback
        min_profit = FILTERS.get("min_monthly_profit", 5_000)
        has_profit_data = listing.monthly_net_profit > 0
        if has_profit_data:
            # Profit data exists — use it directly
            if listing.monthly_net_profit < min_profit:
                return "profit"
        else:
            # No profit data — use revenue as proxy (assume ~20% margin is possible)
            # Revenue of $25k/mo * 20% = $5k/mo profit potential
            min_revenue_proxy = min_profit / 0.20  # $25k for $5k profit target
            if listing.monthly_revenue < min_revenue_proxy:
                return "profit"

        # NOTE: seller_location is NOT filtered — seller location != customer base
        # (e.g. Australia-based seller can run a US/UK-facing ecommerce business)
        # Location is stored on the listing for reference but not used as a filter.

        # Excluded industries
        if listing.niche:
            niche_lower = listing.niche.lower()
            if any(excl in niche_lower for excl in EXCLUDED_INDUSTRIES):
                return "industry"

        return ""

    # ── Web scraping fallback ───────────────────────────────────────────

    def _fetch_via_scraping(self, max_pages: int) -> list[Listing]:
        """Fall back to scraping the Flippa search results page."""
        all_listings: list[Listing] = []

        for page in range(1, max_pages + 1):
            params = {
                "filter[price][max]": FILTERS.get("max_price", 500_000),
                "page": page,
            }
            html = self._scrape_request(FLIPPA_SEARCH_URL, params)
            if html is None:
                break

            page_listings = self._parse_search_page(html)
            if not page_listings:
                break

            for listing in page_listings:
                reason = self._filter_listing(listing)
                if not reason:
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
                url = href if href.startswith("http") else "https://flippa.com{}".format(href)

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

        log.info("Enriched listing: %s", listing.business_name[:60])
        return listing


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
