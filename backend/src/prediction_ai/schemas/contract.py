from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from prediction_ai.schemas.market import NormalizedOutcome

ResolutionSource = Literal[
    "chainlink_twap",
    "chainlink_data_stream",
    "uma",
    "platform_api",
    "exchange_candle_proxy",
    "unknown",
]
Comparator = Literal["gte", "gt", "lte", "lt", "unknown"]
StrikeSource = Literal[
    "start_twap",
    "start_price",
    "fixed_price",
    "platform_field",
    "unknown",
]
ContractDirection = Literal["up", "down", "above", "below", "unknown"]
AmbiguitySeverity = Literal["critical", "warning"]


class ContractAmbiguity(BaseModel):
    """One reason a contract specification is not safe to forecast against."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=96)
    message: str = Field(min_length=1, max_length=500)
    severity: AmbiguitySeverity


class ContractSpec(BaseModel):
    """Typed, auditable interpretation of a prediction-market contract.

    The spec is deliberately explicit about the settlement statistic.  A
    forecast is only actionable when the platform market identity, the strike
    definition, the settlement timestamp, the oracle, and the comparator are
    known.
    """

    model_config = ConfigDict(extra="forbid")

    platform: str
    platform_market_id: str
    platform_event_id: str | None = None
    slug: str | None = None
    title: str
    category: str
    market_type: str
    asset: str | None = None
    direction: ContractDirection = "unknown"

    outcomes: list[NormalizedOutcome] = Field(default_factory=list, max_length=2)
    affirmative_outcome_id: str | None = None
    negative_outcome_id: str | None = None
    # Token IDs are the unambiguous way to map executable quotes.  Polymarket
    # uses one condition ID for both outcomes, so outcome_id alone cannot
    # distinguish YES from NO.
    affirmative_token_id: str | None = None
    negative_token_id: str | None = None

    strike_value: Decimal | None = Field(default=None, gt=0)
    strike_source: StrikeSource = "unknown"

    settlement_source: ResolutionSource = "unknown"
    settlement_source_url: str | None = None
    settlement_timestamp: datetime | None = None
    twap_window_seconds: int | None = Field(default=None, gt=0)

    comparator: Comparator = "unknown"
    tie_rule: str | None = None
    resolution_text: str | None = None

    ambiguities: list[ContractAmbiguity] = Field(default_factory=list)
    provenance: dict = Field(default_factory=dict)
    spec_hash: str = ""

    tradeable: bool = False
    tradeability_reason: str | None = None

    def critical_ambiguities(self) -> list[ContractAmbiguity]:
        return [item for item in self.ambiguities if item.severity == "critical"]

    def critical_flags(self) -> list[str]:
        return [item.code for item in self.critical_ambiguities()]

    def warning_flags(self) -> list[str]:
        return [item.code for item in self.ambiguities if item.severity == "warning"]
