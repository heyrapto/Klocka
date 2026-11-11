from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RecommendationType = Literal["BUY_YES", "BUY_NO", "SELL_YES", "SELL_NO", "NO_TRADE"]


class DecisionMetrics(BaseModel):
    """Execution-cost, depth, and edge diagnostics for one forecast."""

    model_config = ConfigDict(extra="forbid")

    executable_ask_yes: Decimal | None = None
    executable_ask_no: Decimal | None = None
    effective_cost_yes: Decimal | None = None
    effective_cost_no: Decimal | None = None
    estimated_net_edge_yes: Decimal | None = None
    estimated_net_edge_no: Decimal | None = None
    minimum_required_edge: Decimal
    spread: Decimal
    slippage_bps: int = Field(ge=0)
    # Historical field name retained for API compatibility.  It holds the
    # fractional-Kelly stake actually used for the recommended side.
    quarter_kelly_fraction: Decimal = Field(ge=Decimal("0.0"), le=Decimal("1.0"))
    kelly_fraction_yes: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0.0"), le=Decimal("1.0"))
    kelly_fraction_no: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0.0"), le=Decimal("1.0"))
    kelly_fraction_used: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0.0"), le=Decimal("1.0"))
    taker_fee_rate_yes: Decimal = Field(default=Decimal("0.01"), ge=Decimal("0.0"))
    taker_fee_rate_no: Decimal = Field(default=Decimal("0.01"), ge=Decimal("0.0"))
    depth_ask_usd_yes: Decimal | None = Field(default=None, ge=Decimal("0.0"))
    depth_ask_usd_no: Decimal | None = Field(default=None, ge=Decimal("0.0"))
    target_order_usd: Decimal = Field(default=Decimal("100.0"), gt=Decimal("0.0"))
    minimum_depth_usd: Decimal = Field(default=Decimal("100.0"), gt=Decimal("0.0"))
    fill_complete_yes: bool = True
    fill_complete_no: bool = True
    depth_known: bool = True
    quote_age_seconds: Decimal | None = None
    max_quote_age_seconds: Decimal = Field(default=Decimal("15.0"), gt=Decimal("0.0"))
    fee_model: str = "variance"
    resolution_risk_flags: list[str] = Field(default_factory=list)
    blocking_resolution_flags: list[str] = Field(default_factory=list)


class DecisionEvaluation(BaseModel):
    """Final decision output with explicit abstention gating."""

    model_config = ConfigDict(extra="forbid")

    recommendation: RecommendationType
    metrics: DecisionMetrics
    reason: str
    is_abstention: bool = False
    gating_flags: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
