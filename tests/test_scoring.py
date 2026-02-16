"""Tests for the qualification engine, deal scoring, and data model."""

import pytest
from src.models import Listing
from src.analysis.scoring import qualify_listing, score_listing


def _make_listing(**kwargs) -> Listing:
    """Create a listing that passes all qualification filters by default."""
    defaults = dict(
        url="https://flippa.com/listing/12345",
        source="flippa",
        business_name="Test Store",
        niche="home",
        asking_price=250_000,
        monthly_revenue=25_000,
        monthly_net_profit=8_000,
        annual_revenue=300_000,
        annual_net_profit=96_000,
        platform="shopify",
        business_age_months=36,
        seller_location="United States",
    )
    defaults.update(kwargs)
    return Listing(**defaults)


def _make_minimal_listing(**kwargs) -> Listing:
    """Create a listing with minimal data (just enough to qualify)."""
    defaults = dict(
        url="https://flippa.com/12345-test-store",
        source="flippa",
        business_name="Test Store",
        niche="home",
        asking_price=200_000,
        monthly_revenue=20_000,
        monthly_net_profit=7_000,
        annual_revenue=240_000,
        annual_net_profit=84_000,
        platform="website",
        business_age_months=24,
        seller_location="United States",
    )
    defaults.update(kwargs)
    return Listing(**defaults)


# ── Qualification Filters ──────────────────────────────────────────────


class TestFinancialFilters:
    def test_profit_below_minimum_fails(self):
        listing = _make_listing(monthly_net_profit=3_000, annual_net_profit=36_000)
        qualify_listing(listing)
        assert not listing.qualified
        assert "Profit" in listing.disqualify_reason

    def test_profit_at_minimum_passes(self):
        # asking_price=200k, annual=60k -> 3.3x multiple (under 4x)
        listing = _make_listing(
            asking_price=200_000, monthly_net_profit=5_000,
            annual_net_profit=60_000, monthly_revenue=20_000,
        )
        qualify_listing(listing)
        assert listing.qualified

    def test_profit_above_minimum_passes(self):
        listing = _make_listing(
            asking_price=250_000, monthly_net_profit=10_000,
            annual_net_profit=120_000, monthly_revenue=25_000,
        )
        qualify_listing(listing)
        assert listing.qualified

    def test_no_profit_data_uses_revenue_proxy(self):
        """No profit data but high revenue should pass (assumes 20% margin possible)."""
        listing = _make_listing(
            asking_price=200_000,
            monthly_net_profit=0, annual_net_profit=0,
            monthly_revenue=30_000, annual_revenue=360_000,
        )
        qualify_listing(listing)
        assert listing.qualified

    def test_no_profit_low_revenue_fails(self):
        """No profit data and low revenue should fail."""
        listing = _make_listing(
            monthly_net_profit=0, annual_net_profit=0,
            monthly_revenue=10_000, annual_revenue=120_000,
        )
        qualify_listing(listing)
        assert not listing.qualified

    def test_multiple_above_4x_fails(self):
        listing = _make_listing(asking_price=400_000, annual_net_profit=80_000)
        qualify_listing(listing)
        assert not listing.qualified
        assert "multiple" in listing.disqualify_reason.lower()

    def test_multiple_at_4x_passes(self):
        listing = _make_listing(
            asking_price=320_000, annual_net_profit=80_000,
            monthly_net_profit=6_667, monthly_revenue=25_000,
        )
        qualify_listing(listing)
        assert listing.qualified

    def test_multiple_below_4x_passes(self):
        listing = _make_listing(
            asking_price=200_000, annual_net_profit=80_000,
            monthly_net_profit=6_667, monthly_revenue=25_000,
        )
        qualify_listing(listing)
        assert listing.qualified

    def test_low_margin_fails(self):
        """Net margin below 10% should fail."""
        listing = _make_listing(
            monthly_revenue=100_000, monthly_net_profit=8_000,
            annual_revenue=1_200_000, annual_net_profit=96_000,
        )
        qualify_listing(listing)
        assert not listing.qualified
        assert "margin" in listing.disqualify_reason.lower()

    def test_margin_at_10_pct_passes(self):
        listing = _make_listing(
            asking_price=200_000,
            monthly_revenue=50_000, monthly_net_profit=5_000,
            annual_revenue=600_000, annual_net_profit=60_000,
        )
        qualify_listing(listing)
        assert listing.qualified

    def test_price_too_low_fails(self):
        listing = _make_listing(asking_price=30_000)
        qualify_listing(listing)
        assert not listing.qualified
        assert "price" in listing.disqualify_reason.lower()

    def test_price_too_high_fails(self):
        listing = _make_listing(
            asking_price=600_000,
            annual_net_profit=200_000, monthly_net_profit=16_667,
        )
        qualify_listing(listing)
        assert not listing.qualified
        assert "price" in listing.disqualify_reason.lower()


class TestAgeFilter:
    def test_young_business_fails(self):
        listing = _make_listing(business_age_months=12)
        qualify_listing(listing)
        assert not listing.qualified
        assert "age" in listing.disqualify_reason.lower()

    def test_exactly_2_years_passes(self):
        listing = _make_listing(business_age_months=24)
        qualify_listing(listing)
        assert listing.qualified

    def test_old_business_passes(self):
        listing = _make_listing(business_age_months=60)
        qualify_listing(listing)
        assert listing.qualified

    def test_unknown_age_passes(self):
        """Age of 0 (unknown) should pass — don't penalize missing data."""
        listing = _make_listing(business_age_months=0)
        qualify_listing(listing)
        assert listing.qualified


class TestNicheFilter:
    def test_excluded_industry_fails(self):
        listing = _make_listing(niche="gambling")
        qualify_listing(listing)
        assert not listing.qualified
        assert "industry" in listing.disqualify_reason.lower()

    def test_crypto_fails(self):
        listing = _make_listing(niche="cryptocurrency trading")
        qualify_listing(listing)
        assert not listing.qualified

    def test_cannabis_fails(self):
        listing = _make_listing(niche="cbd products")
        qualify_listing(listing)
        assert not listing.qualified

    def test_normal_niche_passes(self):
        listing = _make_listing(niche="home and garden")
        qualify_listing(listing)
        assert listing.qualified

    def test_unknown_niche_passes(self):
        listing = _make_listing(niche="")
        qualify_listing(listing)
        assert listing.qualified


class TestLocationFilter:
    def test_us_location_passes(self):
        listing = _make_listing(seller_location="United States")
        qualify_listing(listing)
        assert listing.qualified

    def test_usa_passes(self):
        listing = _make_listing(seller_location="USA")
        qualify_listing(listing)
        assert listing.qualified

    def test_us_state_passes(self):
        listing = _make_listing(seller_location="California, US")
        qualify_listing(listing)
        assert listing.qualified

    def test_non_us_fails(self):
        listing = _make_listing(seller_location="Australia")
        qualify_listing(listing)
        assert not listing.qualified
        assert "location" in listing.disqualify_reason.lower()

    def test_uk_fails(self):
        listing = _make_listing(seller_location="United Kingdom")
        qualify_listing(listing)
        assert not listing.qualified

    def test_unknown_location_passes(self):
        """Empty location should pass — don't block unknowns."""
        listing = _make_listing(seller_location="")
        qualify_listing(listing)
        assert listing.qualified


# ── Full Qualification ─────────────────────────────────────────────────


class TestFullQualification:
    def test_ideal_listing_qualifies(self):
        listing = _make_listing()
        qualify_listing(listing)
        assert listing.qualified
        assert listing.disqualify_reason == ""

    def test_minimal_listing_qualifies(self):
        listing = _make_minimal_listing()
        qualify_listing(listing)
        assert listing.qualified

    def test_sheet_row_output(self):
        listing = _make_listing()
        qualify_listing(listing)
        score_listing(listing)
        row = listing.to_sheet_row()
        assert len(row) == 19
        assert row[0] == listing.url
        assert row[1] == "flippa"
        assert row[2] == "Test Store"
        assert isinstance(row[9], int)  # deal_score column

    def test_listing_with_description(self):
        listing = _make_listing(
            listing_title="Premium Home & Garden Store",
            description="Established Shopify store selling premium home decor products...",
        )
        qualify_listing(listing)
        assert listing.qualified
        assert listing.listing_title == "Premium Home & Garden Store"
        assert listing.description.startswith("Established")

    def test_empire_flippers_source(self):
        listing = _make_listing(source="empire_flippers")
        qualify_listing(listing)
        assert listing.qualified
        assert listing.source == "empire_flippers"


# ── Flippa Utility Tests ───────────────────────────────────────────────


class TestFlippaUtilities:
    """Test the utility functions from the flippa module."""

    def test_extract_dollar_amount(self):
        from src.scrapers.flippa import _extract_dollar_amount

        assert _extract_dollar_amount("$250,000") == 250_000
        assert _extract_dollar_amount("250K") == 250_000
        assert _extract_dollar_amount("1.5M") == 1_500_000
        assert _extract_dollar_amount("$0") == 0
        assert _extract_dollar_amount("") == 0

    def test_normalize_fulfillment(self):
        from src.scrapers.flippa import _normalize_fulfillment

        assert _normalize_fulfillment("3PL warehouse") == "3pl"
        assert _normalize_fulfillment("Dropshipping") == "dropship"
        assert _normalize_fulfillment("Owner packed") == "owner_packed"
        assert _normalize_fulfillment("") == ""

    def test_parse_age_to_months(self):
        from src.scrapers.flippa import _parse_age_to_months

        assert _parse_age_to_months("3 years 2 months") == 38
        assert _parse_age_to_months("1 year") == 12
        assert _parse_age_to_months("6 months") == 6
        assert _parse_age_to_months("24") == 24


# ── Deal Score Tests ───────────────────────────────────────────────────


class TestDealScore:
    """Test the 0-100 deal score ranking."""

    def test_score_range(self):
        listing = _make_listing()
        s = score_listing(listing)
        assert 0 <= s <= 100

    def test_excellent_deal_scores_high(self):
        """Low multiple, high margin, high profit, old business = high score."""
        listing = _make_listing(
            asking_price=150_000,
            monthly_revenue=25_000, monthly_net_profit=10_000,
            annual_revenue=300_000, annual_net_profit=120_000,
            business_age_months=60,
        )
        s = score_listing(listing)
        assert s >= 75

    def test_borderline_deal_scores_low(self):
        """Max multiple, min margin, min profit, min age = low score."""
        listing = _make_listing(
            asking_price=230_000,
            monthly_revenue=50_000, monthly_net_profit=5_000,
            annual_revenue=600_000, annual_net_profit=60_000,
            business_age_months=24,
        )
        s = score_listing(listing)
        assert s <= 40

    def test_better_multiple_scores_higher(self):
        """Lower profit multiple should produce a higher score."""
        base = dict(
            monthly_revenue=25_000, monthly_net_profit=8_000,
            annual_revenue=300_000, annual_net_profit=96_000,
            business_age_months=36,
        )
        low_multiple = _make_listing(asking_price=150_000, **base)
        high_multiple = _make_listing(asking_price=350_000, **base)
        assert score_listing(low_multiple) > score_listing(high_multiple)

    def test_higher_profit_scores_higher(self):
        """Higher monthly profit should produce a higher score."""
        low_profit = _make_listing(
            asking_price=200_000,
            monthly_net_profit=5_000, annual_net_profit=60_000,
            monthly_revenue=20_000,
        )
        high_profit = _make_listing(
            asking_price=200_000,
            monthly_net_profit=10_000, annual_net_profit=120_000,
            monthly_revenue=25_000,
        )
        assert score_listing(high_profit) > score_listing(low_profit)

    def test_older_business_scores_higher(self):
        """Older business should score higher than younger (both qualifying)."""
        young = _make_listing(business_age_months=24)
        old = _make_listing(business_age_months=60)
        assert score_listing(old) > score_listing(young)

    def test_unknown_age_gets_neutral_score(self):
        """Unknown age (0) should get a middle-ground score, not 0."""
        listing = _make_listing(business_age_months=0)
        s = score_listing(listing)
        # Should get the neutral 10 pts for age
        assert listing.deal_score > 0

    def test_score_stored_on_listing(self):
        listing = _make_listing()
        s = score_listing(listing)
        assert listing.deal_score == s

    def test_revenue_proxy_for_missing_profit(self):
        """Score should use revenue as profit proxy when profit is 0."""
        listing = _make_listing(
            asking_price=200_000,
            monthly_net_profit=0, annual_net_profit=0,
            monthly_revenue=40_000, annual_revenue=480_000,
        )
        s = score_listing(listing)
        # Revenue proxy: 40K * 0.20 = $8K estimated profit, should get some profit points
        assert s > 10
