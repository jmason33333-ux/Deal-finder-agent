"""
Flippa data collection module.

Uses Flippa's search API endpoint to fetch e-commerce listings.
Falls back to HTML scraping if the API is unavailable or rate-limited.

Filter strategy (based on API diagnostics in scripts/diagnose_api.py):
  API-side:  property_type=ecommerce_store, status=open, price $50k-$500k
  Client:    profit >= $5k/mo, age >= 2 years, industry not excluded, US location
  Note:      sort param is ignored by the API; we fetch 20 pages to compensate
"""

from __future__ import annotations

import re
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from typing import Optional

from src.config import FLIPPA_API_KEY, FILTERS, EXCLUDED_INDUSTRIES, ALLOWED_LOCATIONS
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
        skipped = {"price": 0, "profit": 0, "age": 0, "industry": 0, "location": 0, "parse": 0}
        notable_skips: list[str] = []

        for page in range(1, max_pages + 1):
            params = self._build_api_params(page)
            data = self._api_request(FLIPPA_API_URL, params)

            if data is None:
                break

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

                reason = self._filter_listing(listing)
                if reason:
                    skipped[reason] += 1
                    if reason == "profit" and listing.monthly_revenue >= 10_000:
                        notable_skips.append(
                            "  SKIP[profit] rev={:,.0f}/mo profit={:,.0f}/mo price={:,.0f} — {}".format(
                                listing.monthly_revenue, listing.monthly_net_profit,
                                listing.asking_price, listing.business_name[:60],
                            )
                        )
                    continue

                all_listings.append(listing)

            total_results = data.get("meta", {}).get("total_results", 0)
            fetched_so_far = page * 50
            if fetched_so_far >= total_results:
                break
            time.sleep(1)

        log.info(
            "Funnel: %d passed | skipped — price:%d profit:%d age:%d industry:%d location:%d parse:%d",
            len(all_listings), skipped["price"], skipped["profit"],
            skipped["age"], skipped["industry"], skipped["location"], skipped["parse"],
        )

        if notable_skips:
            log.info("Notable skips (had revenue >= $10k/mo but failed profit filter):")
            for skip in notable_skips[:15]:
                log.info("%s", skip)

        return all_listings

    def _build_api_params(self, page: int) -> dict:
        """Build query parameters for Flippa API."""
        params = {
            "page[number]": page,
            "page[size]": 50,
            "filter[property_type]": "ecommerce_store",
            "filter[status]": "open",
        }
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
        """Parse a single listing from the Flippa API response."""
        try:
            attrs = item.get("attributes", item)

            asking_price = _to_float(attrs.get("display_price") or attrs.get("current_price", 0))
            monthly_revenue = _to_float(attrs.get("revenue_per_month") or attrs.get("average_revenue", 0))
            monthly_profit = _to_float(attrs.get("profit_per_month") or attrs.get("average_profit", 0))

            url = attrs.get("html_url", "")
            if not url and item.get("id"):
                url = "https://flippa.com/{}".format(item["id"])

            # Business age from established_at timestamp
            age_months = 0
            established_at = attrs.get("established_at")
            if established_at:
                try:
                    est_str = established_at.split("T")[0]
                    est_date = datetime.strptime(est_str, "%Y-%m-%d")
                    now = datetime.now()
                    age_months = max(0, (now.year - est_date.year) * 12 + (now.month - est_date.month))
                except (ValueError, IndexError):
                    pass

            seller_location = attrs.get("seller_location", "") or ""
            industry = attrs.get("industry", "") or ""
            title = attrs.get("title", "Unknown") or "Unknown"

            # Capture listing summary/description from API
            summary = attrs.get("summary", "") or ""

            listing = Listing(
                url=url,
                source="flippa",
                business_name=title,
                listing_title=title,
                description=summary,
                niche=industry,
                asking_price=asking_price,
                monthly_revenue=monthly_revenue,
                monthly_net_profit=monthly_profit,
                annual_revenue=monthly_revenue * 12,
                annual_net_profit=monthly_profit * 12,
                platform=attrs.get("property_type", "") or "",
                business_age_months=age_months,
                seller_location=seller_location,
            )
            return listing
        except Exception as e:
            log.warning("Failed to parse API listing: %s", e)
            return None

    # ── Client-side filtering funnel ────────────────────────────────────

    def _filter_listing(self, listing: Listing) -> str:
        """Apply client-side filters. Returns skip reason or '' if it passes."""
        # Price cap
        if listing.asking_price > FILTERS.get("max_price", 500_000):
            return "price"

        # Minimum monthly profit — with revenue fallback
        min_profit = FILTERS.get("min_monthly_profit", 5_000)
        has_profit_data = listing.monthly_net_profit > 0
        if has_profit_data:
            if listing.monthly_net_profit < min_profit:
                return "profit"
        else:
            min_revenue_proxy = min_profit / 0.20
            if listing.monthly_revenue < min_revenue_proxy:
                return "profit"

        # Minimum business age
        min_age = FILTERS.get("min_business_age_months", 24)
        if 0 < listing.business_age_months < min_age:
            return "age"

        # Excluded industries
        if listing.niche:
            niche_lower = listing.niche.lower()
            if any(excl in niche_lower for excl in EXCLUDED_INDUSTRIES):
                return "industry"

        # Location — must be in the US
        if listing.seller_location:
            location_lower = listing.seller_location.lower()
            if not any(re.search(r'\b' + re.escape(loc) + r'\b', location_lower) for loc in ALLOWED_LOCATIONS):
                return "location"

        return ""

    # ── Detail page enrichment ─────────────────────────────────────────

    def fetch_listing_details(self, listing: Listing) -> Listing:
        """Enrich a listing with description text from its detail page."""
        if not listing.url:
            return listing

        html = self._scrape_request(listing.url, {})
        if html is None:
            return listing

        soup = BeautifulSoup(html, "html.parser")

        # Grab the full description text if we don't have it from the API
        if not listing.description:
            desc = _extract_description(soup)
            if desc:
                listing.description = desc

        # Also grab platform if available
        listing.platform = _extract_text_by_label(soup, "platform") or listing.platform

        log.info("Enriched listing: %s", listing.business_name[:60])
        return listing

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

            time.sleep(2)

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
            link = card.select_one("a[href*='/listing/'], a[href*='flippa.com']")
            url = ""
            if link:
                href = link.get("href", "")
                url = href if href.startswith("http") else "https://flippa.com{}".format(href)

            title_el = card.select_one(
                ".ListingCard__title, .Listing__title, h3, h2, [class*='title']"
            )
            title = title_el.get_text(strip=True) if title_el else "Unknown"

            price_el = card.select_one(
                "[class*='price'], [data-price], .ListingCard__price"
            )
            asking_price = _extract_dollar_amount(
                price_el.get_text(strip=True) if price_el else "0"
            )

            revenue_el = card.select_one("[class*='revenue'], [data-revenue]")
            monthly_revenue = _extract_dollar_amount(
                revenue_el.get_text(strip=True) if revenue_el else "0"
            )

            profit_el = card.select_one("[class*='profit'], [data-profit]")
            monthly_profit = _extract_dollar_amount(
                profit_el.get_text(strip=True) if profit_el else "0"
            )

            listing = Listing(
                url=url,
                source="flippa",
                business_name=title,
                listing_title=title,
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
    """Extract the main listing description text from a Flippa detail page."""
    # Try common description containers
    for selector in [
        ".listing-description", ".Listing__description",
        "[class*='description']", ".listing-content",
        "article", ".main-content",
    ]:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 50:
                return text[:5000]  # cap at 5k chars for AI prompt

    # Fallback: look for the largest text block in the page
    paragraphs = soup.find_all("p")
    if paragraphs:
        longest = max(paragraphs, key=lambda p: len(p.get_text(strip=True)))
        text = longest.get_text(strip=True)
        if len(text) > 50:
            return text[:5000]

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
        num_match = re.search(r"(\d+)", text)
        if num_match:
            months = int(num_match.group(1))
    return months
