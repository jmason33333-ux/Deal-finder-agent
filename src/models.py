"""Data models for business listings."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Listing:
    """Represents a single e-commerce business listing."""

    # Identity
    url: str
    source: str  # "flippa" or "empire_flippers"
    business_name: str
    niche: str = ""

    # Financials
    asking_price: float = 0.0
    monthly_revenue: float = 0.0
    monthly_net_profit: float = 0.0
    annual_revenue: float = 0.0
    annual_net_profit: float = 0.0
    revenue_trend_yoy: Optional[float] = None  # percentage, e.g. 0.15 = +15%

    # Derived financials
    @property
    def profit_multiple(self) -> float:
        if self.annual_net_profit and self.annual_net_profit > 0:
            return self.asking_price / self.annual_net_profit
        return float("inf")

    @property
    def net_margin_pct(self) -> float:
        if self.monthly_revenue and self.monthly_revenue > 0:
            return (self.monthly_net_profit / self.monthly_revenue) * 100
        return 0.0

    # Platform / age
    platform: str = ""
    business_age_months: int = 0

    # Traffic
    traffic_sources: dict[str, float] = field(default_factory=dict)
    organic_traffic_pct: float = 0.0

    # Email
    email_list_size: int = 0
    email_open_rate: float = 0.0
    email_sophistication: str = ""  # "basic", "moderate", "advanced"

    # Operations
    owner_hours_per_week: float = 0.0
    fulfillment_type: str = ""  # "3pl", "dropship", "owner_packed", "hybrid"
    has_documented_sops: bool = False
    has_team: bool = False

    # AI optimization signals
    support_tickets_per_month: int = 0
    sku_count: int = 0
    repeat_customer_rate: float = 0.0

    # Regulated products flag
    is_regulated: bool = False

    # Scoring results (filled by scoring engine)
    total_score: int = 0
    score_breakdown: dict[str, int] = field(default_factory=dict)

    # AI analysis (filled by Claude analysis)
    ai_analysis: str = ""
    top_growth_moves: str = ""
    estimated_12mo_roi: str = ""

    # Metadata
    date_found: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))

    def to_sheet_row(self) -> list:
        """Convert to a row for Google Sheets output."""
        traffic_str = ", ".join(
            f"{src}: {pct:.0f}%" for src, pct in self.traffic_sources.items()
        )
        breakdown_str = " | ".join(
            f"{k}: {v}" for k, v in self.score_breakdown.items()
        )
        return [
            self.url,
            self.source,
            self.business_name,
            self.niche,
            f"${self.asking_price:,.0f}",
            f"${self.monthly_revenue:,.0f}",
            f"${self.monthly_net_profit:,.0f}",
            f"{self.profit_multiple:.1f}x",
            f"{self.net_margin_pct:.1f}%",
            self.platform,
            f"{self.business_age_months} mo",
            traffic_str,
            f"{self.owner_hours_per_week:.0f}",
            self.total_score,
            breakdown_str,
            self.ai_analysis,
            self.top_growth_moves,
            self.estimated_12mo_roi,
            self.date_found,
        ]
