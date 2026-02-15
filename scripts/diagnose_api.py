#!/usr/bin/env python3
"""
Diagnostic script — run locally to understand the Flippa API behavior.

Tests:
  1. Do our 4 target listings appear in the search results?
  2. What sort params work?
  3. What property_type values exist?
  4. What does the individual listing endpoint return?

Usage:
    python3 scripts/diagnose_api.py
"""

from __future__ import annotations

import requests
import json
import time
from typing import Optional

API_URL = "https://api.flippa.com/v3/listings"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

TARGET_IDS = {"11755327", "11670842", "12218817", "12092001"}
TARGET_NAMES = {
    "11755327": "Pocket Watch Brand",
    "11670842": "14yr Ecommerce",
    "12218817": "Outdoor Brand",
    "12092001": "LinkedIn eCommerce",
}


def fetch_page(params: dict) -> Optional[dict]:
    try:
        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        print(f"  API returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"  Request failed: {e}")
    return None


def check_individual_listings():
    """Fetch each target listing individually."""
    print("\n=== TEST 1: Individual listing endpoints ===")
    for lid, name in TARGET_NAMES.items():
        url = f"{API_URL}/{lid}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            print(f"\n  {name} (ID: {lid}) — status {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                listing = data.get("data", data)
                attrs = listing.get("attributes", listing)
                for field in [
                    "display_price", "current_price", "profit_per_month",
                    "average_profit", "revenue_per_month", "average_revenue",
                    "industry", "property_type", "business_model",
                    "seller_location", "status", "established_at",
                    "has_verified_revenue", "page_views_per_month",
                ]:
                    print(f"    {field}: {attrs.get(field)}")
        except Exception as e:
            print(f"    Error: {e}")
        time.sleep(0.5)


def test_sort_params():
    """Try different sort parameters to see what works."""
    print("\n=== TEST 2: Sort parameters ===")
    sort_options = [
        None,  # default
        "-profit_per_month",
        "-revenue_per_month",
        "-display_price",
        "-average_profit",
        "profit_per_month",
        "-current_price",
    ]
    for sort_val in sort_options:
        params = {"page[number]": 1, "page[size]": 5}
        if sort_val:
            params["sort"] = sort_val
        data = fetch_page(params)
        if data:
            results = data.get("data", [])
            total = data.get("meta", {}).get("total_results", "?")
            if results:
                first = results[0]
                attrs = first.get("attributes", first)
                profit = attrs.get("profit_per_month", 0) or 0
                revenue = attrs.get("revenue_per_month", 0) or 0
                price = attrs.get("display_price", 0) or 0
                print(f"  sort={sort_val or '(default)'}: {total} results, "
                      f"first listing: profit=${profit:,.0f}/mo rev=${revenue:,.0f}/mo price=${price:,.0f}")
            else:
                print(f"  sort={sort_val or '(default)'}: 0 results")
        else:
            print(f"  sort={sort_val or '(default)'}: FAILED")
        time.sleep(0.5)


def test_property_types():
    """See what property_type values exist in the data."""
    print("\n=== TEST 3: Property types in first 200 listings ===")
    property_types = {}
    for page in range(1, 5):
        params = {"page[number]": page, "page[size]": 50}
        data = fetch_page(params)
        if not data:
            break
        for item in data.get("data", []):
            attrs = item.get("attributes", item)
            pt = attrs.get("property_type", "MISSING")
            property_types[pt] = property_types.get(pt, 0) + 1
        time.sleep(0.5)
    for pt, count in sorted(property_types.items(), key=lambda x: -x[1]):
        print(f"  {pt}: {count}")


def test_filters():
    """Try different filter combinations."""
    print("\n=== TEST 4: Filter combinations ===")
    filter_tests = [
        {"filter[price][max]": 500000},
        {"filter[price][max]": 500000, "filter[price][min]": 50000},
        {"filter[property_type]": "website"},
        {"filter[property_type]": "ecommerce"},
        {"filter[property_type]": "ecommerce_store"},
        {"filter[property_type]": "online_business"},
        {"filter[status]": "open"},
        {"filter[status]": "live"},
        {"filter[price][max]": 500000, "sort": "-profit_per_month"},
        {"filter[price][max]": 500000, "filter[price][min]": 50000, "sort": "-profit_per_month"},
    ]
    for filters in filter_tests:
        params = {"page[number]": 1, "page[size]": 5, **filters}
        data = fetch_page(params)
        if data:
            total = data.get("meta", {}).get("total_results", 0)
            results = data.get("data", [])
            first_profit = 0
            first_rev = 0
            if results:
                attrs = results[0].get("attributes", results[0])
                first_profit = attrs.get("profit_per_month", 0) or 0
                first_rev = attrs.get("revenue_per_month", 0) or 0
            filter_str = " + ".join(f"{k}={v}" for k, v in filters.items())
            print(f"  {filter_str}: {total} results, "
                  f"first: profit=${first_profit:,.0f}/mo rev=${first_rev:,.0f}/mo")
        else:
            filter_str = " + ".join(f"{k}={v}" for k, v in filters.items())
            print(f"  {filter_str}: FAILED")
        time.sleep(0.5)


def search_for_targets():
    """Search through pages to find our target listings."""
    print("\n=== TEST 5: Searching for target listings ===")
    # Try with sort by profit descending + price filter
    found = set()
    params_base = {"page[size]": 50, "filter[price][max]": 500000}

    # Also try sort
    for sort_val in [None, "-profit_per_month", "-revenue_per_month"]:
        if sort_val:
            params_base["sort"] = sort_val
        sort_label = sort_val or "(default)"
        print(f"\n  Searching with sort={sort_label}...")
        for page in range(1, 11):  # check 10 pages = 500 listings
            params = {**params_base, "page[number]": page}
            data = fetch_page(params)
            if not data:
                break
            results = data.get("data", [])
            if not results:
                break
            for item in results:
                lid = str(item.get("id", ""))
                if lid in TARGET_IDS:
                    attrs = item.get("attributes", item)
                    profit = attrs.get("profit_per_month", 0) or 0
                    revenue = attrs.get("revenue_per_month", 0) or 0
                    price = attrs.get("display_price", 0) or 0
                    print(f"    FOUND on page {page}: {TARGET_NAMES.get(lid, lid)} "
                          f"— profit=${profit:,.0f}/mo rev=${revenue:,.0f}/mo price=${price:,.0f}")
                    found.add(lid)
            time.sleep(0.5)

    missing = TARGET_IDS - found
    if missing:
        print(f"\n  NOT FOUND in 500 listings: {[TARGET_NAMES[m] for m in missing]}")
    else:
        print(f"\n  All 4 targets found!")


if __name__ == "__main__":
    check_individual_listings()
    test_sort_params()
    test_property_types()
    test_filters()
    search_for_targets()
    print("\n=== DONE ===")
