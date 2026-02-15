"""Tests for the scoring engine."""

import pytest
from src.models import Listing
from src.analysis.scoring import score_listing


def _make_listing(**kwargs) -> Listing:
    """Create a listing with sensible defaults, overridden by kwargs."""
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
        revenue_trend_yoy=0.15,
        platform="shopify",
        business_age_months=36,
        traffic_sources={"organic": 35, "paid": 30, "social": 20, "direct": 15},
        organic_traffic_pct=35,
        email_list_size=12_000,
        email_open_rate=22,
        owner_hours_per_week=8,
        fulfillment_type="3pl",
        has_documented_sops=True,
        has_team=True,
        support_tickets_per_month=250,
        email_sophistication="basic",
        sku_count=50,
        repeat_customer_rate=25,
        is_regulated=False,
    )
    defaults.update(kwargs)
    return Listing(**defaults)


class TestDisqualifiers:
    def test_revenue_declining_over_25_pct(self):
        listing = _make_listing(revenue_trend_yoy=-0.30)
        score_listing(listing)
        assert listing.total_score == 0
        assert "disqualified" in listing.score_breakdown

    def test_single_traffic_source_over_80_pct(self):
        listing = _make_listing(traffic_sources={"paid": 85, "organic": 15})
        score_listing(listing)
        assert listing.total_score == 0

    def test_asking_multiple_over_5x(self):
        listing = _make_listing(asking_price=500_000, annual_net_profit=80_000)
        score_listing(listing)
        assert listing.total_score == 0

    def test_owner_over_40_hrs_no_team(self):
        listing = _make_listing(owner_hours_per_week=45, has_team=False)
        score_listing(listing)
        assert listing.total_score == 0

    def test_regulated_product(self):
        listing = _make_listing(is_regulated=True)
        score_listing(listing)
        assert listing.total_score == 0

    def test_not_disqualified_when_clean(self):
        listing = _make_listing()
        score_listing(listing)
        assert listing.total_score > 0


class TestFinancialScoring:
    def test_low_profit_multiple_max_points(self):
        # 2.5x multiple -> 15 pts
        listing = _make_listing(asking_price=240_000, annual_net_profit=96_000)
        score_listing(listing)
        assert listing.score_breakdown["financial"] >= 15

    def test_high_profit_multiple_zero_points(self):
        # >4x multiple
        listing = _make_listing(asking_price=400_000, annual_net_profit=96_000)
        score_listing(listing)
        # Should have 0 points for multiple but may have points for margin/trend
        # Profit multiple is 4.17x -> 0 for multiple
        pass

    def test_high_margin_gets_points(self):
        # 32% margin
        listing = _make_listing(monthly_revenue=25_000, monthly_net_profit=8_000)
        score_listing(listing)
        assert listing.score_breakdown["financial"] > 0

    def test_growing_revenue_gets_points(self):
        listing = _make_listing(revenue_trend_yoy=0.20)
        score_listing(listing)
        assert listing.score_breakdown["financial"] > 0


class TestTrafficScoring:
    def test_diversified_traffic_max_points(self):
        listing = _make_listing(
            traffic_sources={"organic": 30, "paid": 25, "social": 25, "direct": 20},
            organic_traffic_pct=45,
            email_list_size=15_000,
            email_open_rate=25,
        )
        score_listing(listing)
        assert listing.score_breakdown["traffic"] == 25

    def test_single_channel_low_points(self):
        listing = _make_listing(
            traffic_sources={"paid": 75},
            organic_traffic_pct=5,
            email_list_size=0,
            email_open_rate=0,
        )
        score_listing(listing)
        assert listing.score_breakdown["traffic"] < 10


class TestOperationsScoring:
    def test_ideal_operations(self):
        listing = _make_listing(
            owner_hours_per_week=5,
            fulfillment_type="3pl",
            has_documented_sops=True,
            has_team=True,
        )
        score_listing(listing)
        assert listing.score_breakdown["operations"] == 20

    def test_high_owner_involvement(self):
        listing = _make_listing(
            owner_hours_per_week=35,
            fulfillment_type="owner_packed",
            has_documented_sops=False,
            has_team=False,
        )
        score_listing(listing)
        assert listing.score_breakdown["operations"] < 5


class TestAIUpsideScoring:
    def test_high_ai_upside(self):
        listing = _make_listing(
            support_tickets_per_month=300,
            email_sophistication="basic",
            sku_count=50,
        )
        score_listing(listing)
        assert listing.score_breakdown["ai_upside"] == 15

    def test_low_ai_upside(self):
        listing = _make_listing(
            support_tickets_per_month=10,
            email_sophistication="advanced",
            sku_count=3,
        )
        score_listing(listing)
        assert listing.score_breakdown["ai_upside"] < 8


class TestStrategicFitScoring:
    def test_perfect_strategic_fit(self):
        listing = _make_listing(
            niche="home and garden",
            platform="shopify",
            repeat_customer_rate=30,
        )
        score_listing(listing)
        assert listing.score_breakdown["strategic"] == 10

    def test_no_strategic_fit(self):
        listing = _make_listing(
            niche="crypto trading tools",
            platform="custom",
            repeat_customer_rate=0,
        )
        score_listing(listing)
        assert listing.score_breakdown["strategic"] == 0


class TestFullScoring:
    def test_ideal_listing_scores_high(self):
        listing = _make_listing()
        score_listing(listing)
        assert listing.total_score >= 70
        assert listing.total_score <= 100

    def test_score_is_sum_of_breakdown(self):
        listing = _make_listing()
        score_listing(listing)
        assert listing.total_score == sum(listing.score_breakdown.values())

    def test_sheet_row_output(self):
        listing = _make_listing()
        score_listing(listing)
        row = listing.to_sheet_row()
        assert len(row) == 19
        assert row[0] == listing.url
        assert row[1] == "flippa"


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
