"""Typed contracts for historical market prices.

Prices are probabilities in ``[0, 1]`` and are stored as decimal strings, never
binary floats.  Every point carries its source, kind, and observation time so a
downstream baseline can distinguish a mid-price from a last trade and can refuse
to use a point that was not actually available at the evaluation cutoff.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from prediction_ai.schemas.market import Platform

PriceKind = Literal["mid", "last_trade", "displayed"]
PriceSource = Literal[
    "polymarket_clob_prices_history",
    "polymarket_clob_snapshot",
    "bayse_clob_snapshot",
]

MAX_HISTORY_POINTS = 5000


class PricePoint(BaseModel):
    """One observed market probability at a point in time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    price: Decimal = Field(ge=Decimal("0.0"), le=Decimal("1.0"))
    kind: PriceKind = "mid"
    source: PriceSource

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware UTC")
        return value.astimezone(UTC)


class OutcomePriceHistory(BaseModel):
    """Bounded price path for one outcome token."""

    model_config = ConfigDict(extra="forbid")

    platform: Platform
    platform_market_id: str = Field(min_length=1, max_length=200)
    outcome_id: str = Field(min_length=1, max_length=200)
    token_id: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=100)
    granularity_seconds: int = Field(default=60, ge=1, le=86_400)
    points: list[PricePoint] = Field(default_factory=list, max_length=MAX_HISTORY_POINTS)

    @model_validator(mode="after")
    def sort_and_deduplicate(self) -> OutcomePriceHistory:
        unique: dict[tuple[datetime, str], PricePoint] = {}
        for point in self.points:
            unique[(point.observed_at, point.source)] = point
        ordered = sorted(
            unique.values(),
            key=lambda point: (point.observed_at, point.source),
        )
        object.__setattr__(self, "points", ordered)
        return self


__all__ = [
    "MAX_HISTORY_POINTS",
    "OutcomePriceHistory",
    "PriceKind",
    "PricePoint",
    "PriceSource",
]
