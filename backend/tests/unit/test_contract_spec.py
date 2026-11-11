from datetime import UTC, datetime
from decimal import Decimal

from prediction_ai.markets.contract_spec import build_contract_spec
from prediction_ai.schemas.market import NormalizedMarket, NormalizedOutcome

RESOLVES_AT = datetime(2026, 11, 10, 18, 15, tzinfo=UTC)


def _market(
    *,
    platform: str = "polymarket",
    title: str = "Bitcoin Up or Down - November 10, 6:00PM-6:15PM ET",
    description: str = (
        "This market resolves Up if the Bitcoin price at the end of the range is "
        "greater than or equal to the price at the beginning. Resolution source is "
        "the Chainlink BTC/USD data stream."
    ),
    labels: tuple[str, str] = ("Up", "Down"),
    outcome_ids: tuple[str, str] = ("condition-1", "condition-1"),
    token_ids: tuple[str, str] = ("token-up", "token-down"),
) -> NormalizedMarket:
    return NormalizedMarket(
        platform=platform,  # type: ignore[arg-type]
        platform_market_id="market-1",
        title=title,
        description=description,
        category="crypto",
        market_type="binary_threshold",
        underlying_symbol="BTC",
        outcomes=[
            NormalizedOutcome(outcome_id=outcome_ids[0], token_id=token_ids[0], label=labels[0]),
            NormalizedOutcome(outcome_id=outcome_ids[1], token_id=token_ids[1], label=labels[1]),
        ],
        closes_at=RESOLVES_AT,
        resolves_at=RESOLVES_AT,
        resolution_source="https://data.chain.link/streams/btc-usd",
        resolution_criteria=description,
        status="active",
        order_book_enabled=True,
        taker_fee_rate=Decimal("0.07"),
        fee_model="variance",
    )


def test_polymarket_updown_spec_maps_tokens_not_shared_condition_id() -> None:
    spec = build_contract_spec(_market(), settlement_timestamp=RESOLVES_AT)

    assert spec.affirmative_outcome_id == "condition-1"
    assert spec.negative_outcome_id == "condition-1"
    # Both outcomes share a condition ID on Polymarket; tokens disambiguate.
    assert spec.affirmative_token_id == "token-up"
    assert spec.negative_token_id == "token-down"
    assert spec.direction == "up"
    assert spec.settlement_source == "chainlink_data_stream"
    assert spec.comparator == "gte"
    assert spec.tie_rule == "affirmative_on_tie"
    # Up/down markets are struck at the start price, which the caller must
    # resolve explicitly rather than fabricate.
    assert spec.strike_source == "start_price"
    assert "UNKNOWN_STRIKE" in spec.critical_flags()
    assert spec.tradeable is False


def test_strike_override_makes_verified_updown_contract_tradeable() -> None:
    spec = build_contract_spec(
        _market(),
        settlement_timestamp=RESOLVES_AT,
        strike_override=Decimal("73892.99"),
    )

    assert spec.strike_value == Decimal("73892.99")
    assert spec.tradeable is True
    assert spec.critical_flags() == []
    assert spec.spec_hash


def test_bayse_chainlink_twap_spec_captures_window_and_tie_rule() -> None:
    description = (
        "This market will resolve to Up if the Chainlink 60-second TWAP of BTC/USD "
        "at the end of the range is greater than or equal to the 60-second TWAP at "
        "the beginning of the range. Otherwise it resolves Down."
    )
    spec = build_contract_spec(
        _market(
            platform="bayse",
            description=description,
            labels=("Buy Up", "Buy Down"),
            outcome_ids=("outcome-up", "outcome-down"),
            token_ids=("bayse-token-up", "bayse-token-down"),
        ),
        settlement_timestamp=RESOLVES_AT,
        strike_override=Decimal("73000"),
    )

    assert spec.settlement_source == "chainlink_twap"
    assert spec.twap_window_seconds == 60
    assert spec.comparator == "gte"
    assert spec.affirmative_outcome_id == "outcome-up"
    assert spec.negative_outcome_id == "outcome-down"


def test_unresolved_oracle_and_comparator_are_critical() -> None:
    """UNKNOWN_SETTLEMENT_SOURCE is now a warning (not critical): many standard
    Polymarket price-bracket markets resolve via the platform itself without
    naming a named oracle in the resolution text.  UNKNOWN_COMPARATOR remains
    critical because without it we cannot determine which side wins."""
    market = _market(
        title="Will BTC finish at 74000 today?",
        description="Resolution follows the official market rules.",
        labels=("Yes", "No"),
        outcome_ids=("yes-id", "no-id"),
        token_ids=("yes-token", "no-token"),
    )
    market.resolution_source = None
    spec = build_contract_spec(
        market,
        settlement_timestamp=RESOLVES_AT,
        strike_override=Decimal("74000"),
    )

    assert spec.settlement_source == "unknown"
    assert spec.comparator == "unknown"
    # UNKNOWN_SETTLEMENT_SOURCE is a warning — it appears in ambiguities but NOT
    # in critical_flags(), so the market remains potentially tradeable on that
    # dimension alone.
    assert "UNKNOWN_SETTLEMENT_SOURCE" not in spec.critical_flags()
    # UNKNOWN_COMPARATOR is still critical: we cannot evaluate which side wins.
    assert "UNKNOWN_COMPARATOR" in spec.critical_flags()
    assert spec.tradeable is False  # still not tradeable due to unknown comparator


def test_missing_settlement_timestamp_is_critical() -> None:
    market = _market()
    market.resolves_at = None
    market.closes_at = None
    spec = build_contract_spec(market, strike_override=Decimal("74000"))

    assert "MISSING_SETTLEMENT_TIMESTAMP" in spec.critical_flags()
