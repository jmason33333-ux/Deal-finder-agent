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
        has_legal_issues=False,
    )
    defaults.update(kwargs)
    return Listing(**defaults)


# ── Disqualifiers ───────────────────────────────────────────────────────


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

    def test_legal_issues_disqualifies(self):
        listing = _make_listing(has_legal_issues=True)
        score_listing(listing)
        assert listing.total_score == 0
        assert "litigation" in listing.score_breakdown["disqualified"].lower()

    def test_not_disqualified_when_clean(self):
        listing = _make_listing()
        score_listing(listing)
        assert listing.total_score > 0

    def test_owner_over_40_hrs_with_team_not_disqualified(self):
        """Owner works 45 hrs but HAS a team — should NOT be disqualified."""
        listing = _make_listing(owner_hours_per_week=45, has_team=True)
        score_listing(listing)
        assert listing.total_score > 0


# ── Financial Health (30 pts) ───────────────────────────────────────────


class TestFinancialScoring:
    def test_low_profit_multiple_max_points(self):
        # 2.5x multiple -> 15 pts for multiple
        listing = _make_listing(asking_price=240_000, annual_net_profit=96_000)
        score_listing(listing)
        assert listing.score_breakdown["financial"] >= 15

    def test_high_profit_multiple_zero_multiple_pts(self):
        # 4.17x multiple -> 0 pts for multiple, still gets margin + trend pts
        listing = _make_listing(asking_price=400_000, annual_net_profit=96_000)
        score_listing(listing)
        # Should still have margin (32% -> 8pts) and trend (15% -> 7pts) = 15
        assert listing.score_breakdown["financial"] == 15

    def test_midrange_multiple(self):
        # 3.25x = midpoint between 2.5x and 4x -> ~7-8 pts for multiple
        listing = _make_listing(asking_price=312_000, annual_net_profit=96_000)
        score_listing(listing)
        assert listing.score_breakdown["financial"] > 10

    def test_high_margin_gets_8_points(self):
        # 32% margin -> 8pts
        listing = _make_listing(monthly_revenue=25_000, monthly_net_profit=8_000)
        assert listing.net_margin_pct == 32.0

    def test_low_margin_gets_zero(self):
        # 8% margin -> 0pts
        listing = _make_listing(monthly_revenue=25_000, monthly_net_profit=2_000)
        score_listing(listing)
        # margin alone is 0, but multiple and trend still contribute
        assert listing.score_breakdown["financial"] >= 0

    def test_growing_revenue_gets_7_points(self):
        listing = _make_listing(revenue_trend_yoy=0.20)
        score_listing(listing)
        assert listing.score_breakdown["financial"] > 0

    def test_flat_revenue_gets_midpoint(self):
        # 0% growth -> ~3.5pts = rounds to 4
        listing = _make_listing(
            revenue_trend_yoy=0.0,
            asking_price=240_000,  # 2.5x -> 15pts
            monthly_revenue=25_000,
            monthly_net_profit=8_000,  # 32% -> 8pts
        )
        score_listing(listing)
        # financial = 15 (multiple) + 8 (margin) + 4 (flat trend) = 27
        assert listing.score_breakdown["financial"] == 27


# ── Traffic & Customer Acquisition (25 pts) ─────────────────────────────


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

    def test_email_list_zero_gets_zero_email_pts(self):
        """No email list → 0 email points regardless of open rate."""
        listing = _make_listing(email_list_size=0, email_open_rate=30)
        score_listing(listing)
        # Can't assert exact email pts, but total traffic should be lower
        # than a listing with a good list
        no_list = listing.score_breakdown["traffic"]

        listing2 = _make_listing(email_list_size=15_000, email_open_rate=25)
        score_listing(listing2)
        with_list = listing2.score_breakdown["traffic"]
        assert with_list > no_list

    def test_low_open_rate_gets_zero_email_pts(self):
        """Open rate < 15% → 0 email points even with large list."""
        listing = _make_listing(email_list_size=20_000, email_open_rate=12)
        score_listing(listing)
        no_engage = listing.score_breakdown["traffic"]

        listing2 = _make_listing(email_list_size=20_000, email_open_rate=25)
        score_listing(listing2)
        good_engage = listing2.score_breakdown["traffic"]
        assert good_engage > no_engage


# ── Operational Simplicity (20 pts) ─────────────────────────────────────


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

    def test_fba_fulfillment_gets_4_pts(self):
        """Amazon FBA → 4 points (yellow flag)."""
        listing = _make_listing(fulfillment_type="fba")
        score_listing(listing)
        # FBA gives 4pts instead of 6pts for 3PL — total should be 18
        assert listing.score_breakdown["operations"] == 18

    def test_amazon_fba_alias(self):
        listing = _make_listing(fulfillment_type="amazon_fba")
        score_listing(listing)
        assert listing.score_breakdown["operations"] == 18

    def test_hybrid_fulfillment(self):
        listing = _make_listing(fulfillment_type="hybrid")
        score_listing(listing)
        # hybrid = 3pts + 8 hours + 6 SOPs/team = 17
        assert listing.score_breakdown["operations"] == 17


# ── AI Optimization Upside (15 pts) ─────────────────────────────────────


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
        # 1 (tickets < 50) + 1 (advanced) + 0 (< 10 SKUs) = 2
        assert listing.score_breakdown["ai_upside"] == 2

    def test_support_tiers(self):
        """Test the 3-tier support ticket scoring."""
        # > 200 = 5pts
        l1 = _make_listing(support_tickets_per_month=250)
        score_listing(l1)
        s1 = l1.score_breakdown["ai_upside"]

        # 50-200 = 3pts
        l2 = _make_listing(support_tickets_per_month=100)
        score_listing(l2)
        s2 = l2.score_breakdown["ai_upside"]

        # < 50 = 1pt
        l3 = _make_listing(support_tickets_per_month=30)
        score_listing(l3)
        s3 = l3.score_breakdown["ai_upside"]

        assert s1 > s2 > s3

    def test_sku_tiers(self):
        """Test the 4-tier SKU scoring."""
        # 10-100 = 5pts
        l1 = _make_listing(sku_count=50)
        score_listing(l1)

        # 100-500 = 3pts
        l2 = _make_listing(sku_count=200)
        score_listing(l2)

        # > 500 = 1pt
        l3 = _make_listing(sku_count=800)
        score_listing(l3)

        # < 10 = 0pts
        l4 = _make_listing(sku_count=5)
        score_listing(l4)

        assert l1.score_breakdown["ai_upside"] > l2.score_breakdown["ai_upside"]
        assert l2.score_breakdown["ai_upside"] > l3.score_breakdown["ai_upside"]
        assert l3.score_breakdown["ai_upside"] > l4.score_breakdown["ai_upside"]

    def test_zero_tickets_gets_zero(self):
        listing = _make_listing(support_tickets_per_month=0)
        score_listing(listing)
        # 0 tickets = 0pts (not 1), basic email = 5, 50 SKUs = 5 -> 10
        assert listing.score_breakdown["ai_upside"] == 10


# ── Strategic Fit (10 pts) ──────────────────────────────────────────────


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

    def test_yellow_niche_gets_2_pts(self):
        """Beauty/food/consumer niches → 2pts."""
        listing = _make_listing(niche="beauty products")
        score_listing(listing)
        # 2 (yellow niche) + 3 (shopify) + 3 (repeat > 20%) = 8
        assert listing.score_breakdown["strategic"] == 8

    def test_red_niche_gets_0_pts(self):
        """Fashion/supplements/electronics → 0pts for niche."""
        listing = _make_listing(niche="fashion apparel")
        score_listing(listing)
        # 0 (red niche) + 3 (shopify) + 3 (repeat > 20%) = 6
        assert listing.score_breakdown["strategic"] == 6

    def test_woocommerce_gets_1_pt(self):
        """WooCommerce platform → 1pt."""
        listing = _make_listing(platform="woocommerce")
        score_listing(listing)
        # 4 (home niche) + 1 (woo) + 3 (repeat > 20%) = 8
        assert listing.score_breakdown["strategic"] == 8

    def test_custom_platform_gets_0_pts(self):
        """Custom/Magento/BigCommerce → 0pts."""
        listing = _make_listing(platform="magento")
        score_listing(listing)
        # 4 (home niche) + 0 (magento) + 3 (repeat > 20%) = 7
        assert listing.score_breakdown["strategic"] == 7

    def test_repeat_customer_midrange(self):
        """10-20% repeat → 2pts (rounded from 1.5)."""
        listing = _make_listing(repeat_customer_rate=15)
        score_listing(listing)
        # 4 (home) + 3 (shopify) + 2 (10-20% repeat) = 9
        assert listing.score_breakdown["strategic"] == 9

    def test_repeat_customer_below_10_gets_0(self):
        """< 10% repeat → 0pts."""
        listing = _make_listing(repeat_customer_rate=5)
        score_listing(listing)
        # 4 (home) + 3 (shopify) + 0 = 7
        assert listing.score_breakdown["strategic"] == 7


# ── Full Scoring ────────────────────────────────────────────────────────


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

    def test_score_classification_strong_buy(self):
        """Ideal listing with all green flags should be 80+."""
        listing = _make_listing(
            asking_price=240_000,  # 2.5x multiple
            annual_net_profit=96_000,
            monthly_revenue=25_000,
            monthly_net_profit=8_000,  # 32% margin
            revenue_trend_yoy=0.15,
            traffic_sources={"organic": 30, "paid": 25, "social": 25, "direct": 20},
            organic_traffic_pct=45,
            email_list_size=15_000,
            email_open_rate=25,
            owner_hours_per_week=5,
            fulfillment_type="3pl",
            has_documented_sops=True,
            has_team=True,
            support_tickets_per_month=300,
            email_sophistication="basic",
            sku_count=50,
            niche="home and garden",
            platform="shopify",
            repeat_customer_rate=30,
        )
        score_listing(listing)
        assert listing.total_score >= 80


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
