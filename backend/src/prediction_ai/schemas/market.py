from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["polymarket", "bayse"]
MarketCategory = Literal["crypto", "sports", "news", "finance", "politics", "other"]
MarketStatus = Literal["active", "paused", "closed", "resolved", "void", "disputed"]
FeeModel = Literal["none", "variance_floor_half", "variance"]


class NormalizedOutcome(BaseModel):
    """Normalized individual market outcome."""

    model_config = ConfigDict(extra="forbid")

    outcome_id: str
    token_id: str
    label: str


class NormalizedMarket(BaseModel):
    """Authoritative normalized prediction market metadata."""

    model_config = ConfigDict(extra="forbid")

    platform: Platform
    platform_market_id: str
    platform_event_id: str | None = None
    slug: str | None = None
    url: str | None = None
    title: str
    description: str | None = None
    category: MarketCategory
    market_type: str
    underlying_symbol: str | None = None
    outcomes: list[NormalizedOutcome] = Field(min_length=2, max_length=2)
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    resolves_at: datetime | None = None
    resolution_source: str | None = None
    resolution_criteria: str | None = None
    status: MarketStatus
    order_book_enabled: bool = True
    taker_fee_rate: Decimal = Field(default=Decimal("0.0"), ge=0, le=1)
    fee_model: FeeModel = "none"
    raw_metadata: dict = Field(default_factory=dict)


class BookLevel(BaseModel):
    """One price level of an order book side."""

    model_config = ConfigDict(extra="forbid")

    price: Decimal = Field(gt=Decimal("0.0"), lt=Decimal("1.0"))
    size: Decimal = Field(ge=Decimal("0.0"))


class OutcomeQuote(BaseModel):
    """Point-in-time executable quote and depth for one outcome token."""

    model_config = ConfigDict(extra="forbid")

    outcome_id: str
    token_id: str
    label: str
    best_bid: Decimal | None = Field(default=None, ge=Decimal("0.0"), le=Decimal("1.0"))
    best_ask: Decimal | None = Field(default=None, ge=Decimal("0.0"), le=Decimal("1.0"))
    mid_price: Decimal | None = Field(default=None, ge=Decimal("0.0"), le=Decimal("1.0"))
    bids: list[BookLevel] = Field(default_factory=list)
    asks: list[BookLevel] = Field(default_factory=list)
    depth_bid_usd: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0.0"))
    depth_ask_usd: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0.0"))


class NormalizedOrderBook(BaseModel):
    """Normalized snapshot of order book depth across outcomes."""

    model_config = ConfigDict(extra="forbid")

    platform: Platform
    platform_market_id: str
    observed_at: datetime
    quotes: list[OutcomeQuote] = Field(min_length=1, max_length=2)
    depth_known: bool = True
    raw_sequence: str | None = None
    quality_flags: list[str] = Field(default_factory=list)


class MarketMatchCandidate(BaseModel):
    """Ranked verification candidate returned to the API/UI."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    platform: str
    title: str
    url: str | None = None
    resolves_at: datetime | None = None
    similarity_score: float = Field(ge=0.0, le=1.0)
    match_status: str
    discrepancies: list[str] = Field(default_factory=list)
