from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ClaimStance = Literal["supports_yes", "supports_no", "neutral", "unresolved"]
ResearchGate = Literal["eligible", "abstain"]
EvidenceState = Literal["confirmed", "probable", "contested", "unresolved", "stale"]


class SportsClaim(BaseModel):
    """Point-in-time, source-backed sports claim."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1, max_length=128)
    claim_type: str = Field(min_length=1, max_length=128)
    claim_text: str = Field(min_length=1, max_length=2000)
    stance: ClaimStance
    state: EvidenceState
    source_id: str = Field(min_length=1, max_length=255)
    source_url: str | None = None
    published_at: datetime | None = None
    observed_at: datetime | None = None
    retrieved_at: datetime
    as_of_cutoff: bool
    authority_tier: int = Field(ge=1, le=3)
    independence_group: str = Field(min_length=1, max_length=128)
    quality_flags: list[str] = Field(default_factory=list, max_length=20)


class SportsEvidencePack(BaseModel):
    """Bounded evidence contract consumed by sports forecasting."""

    model_config = ConfigDict(extra="forbid")

    pack_version: str = "sports_evidence_v1"
    proposition_type: str = Field(min_length=1, max_length=128)
    cutoff_at: datetime
    subject: str = Field(min_length=1, max_length=255)
    organization: str | None = Field(default=None, max_length=255)
    claims: list[SportsClaim] = Field(default_factory=list, max_length=20)
    current_status: EvidenceState
    resolution_definition_verified: bool
    deadline_verified: bool
    contradictions: list[str] = Field(default_factory=list, max_length=20)
    independent_source_groups: int = Field(default=0, ge=0, le=20)
    quality_flags: list[str] = Field(default_factory=list, max_length=20)
    recommendation_gate: ResearchGate