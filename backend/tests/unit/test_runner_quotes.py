from datetime import UTC, datetime, timedelta
from decimal import Decimal

from prediction_ai.orchestration.runner import (
    _build_target_definition,
    _find_outcome_quote,
    _quote_age_seconds,
)
from prediction_ai.schemas.contract import ContractSpec
from prediction_ai.schemas.market import (
    NormalizedMarket,
    NormalizedOrderBook,
    NormalizedOutcome,
    OutcomeQuote,
)

OBSERVED_AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def _quote(
    *,
    token_id: str,
    outcome_id: str,
    label: str,
    best_ask: str = "0.50",
) -> OutcomeQuote:
    return OutcomeQuote(
        outcome_id=outcome_id,
        token_id=token_id,
        label=label,
        best_ask=Decimal(best_ask),
    )


def _order_book(*quotes: OutcomeQuote) -> NormalizedOrderBook:
    return NormalizedOrderBook(
        platform="polymarket",
        platform_market_id="condition-1",
        observed_at=OBSERVED_AT,
        quotes=list(quotes),
    )


def _market() -> NormalizedMarket:
    return NormalizedMarket(
        platform="polymarket",
        platform_market_id="condition-1",
        title="Will BTC be up or down in the next 15 minutes?",
        category="crypto",
        market_type="binary",
        underlying_symbol="BTC",
        outcomes=[
            NormalizedOutcome(
                outcome_id="down-id", token_id="down-token", label="Down"
            ),
            NormalizedOutcome(outcome_id="up-id", token_id="up-token", label="Up"),
        ],
        status="active",
    )


def _contract_spec(**overrides: object) -> ContractSpec:
    payload: dict[str, object] = {
        "platform": "polymarket",
        "platform_market_id": "condition-1",
        "title": "Will BTC be up or down in the next 15 minutes?",
        "category": "crypto",
        "market_type": "binary",
        "asset": "BTC",
        "direction": "up",
        "outcomes": [
            NormalizedOutcome(
                outcome_id="down-id", token_id="down-token", label="Down"
            ),
            NormalizedOutcome(outcome_id="up-id", token_id="up-token", label="Up"),
        ],
        "affirmative_outcome_id": "up-id",
        "negative_outcome_id": "down-id",
        "affirmative_token_id": "up-token",
        "negative_token_id": "down-token",
        "strike_value": Decimal("74000"),
        "strike_source": "start_price",
        "settlement_source": "chainlink_data_stream",
        "settlement_timestamp": OBSERVED_AT + timedelta(minutes=15),
        "comparator": "gt",
        "spec_hash": "spec-hash",
        "tradeable": True,
    }
    payload.update(overrides)
    return ContractSpec.model_validate(payload)


def test_find_outcome_quote_prefers_token_id_when_outcome_ids_collide() -> None:
    book = _order_book(
        _quote(token_id="yes-token", outcome_id="condition-1", label="Yes"),
        _quote(token_id="no-token", outcome_id="condition-1", label="No"),
    )

    quote = _find_outcome_quote(
        book,
        token_id="no-token",
        outcome_id="condition-1",
        fallback_labels=frozenset({"no"}),
    )

    assert quote is not None
    assert quote.token_id == "no-token"


def test_find_outcome_quote_uses_unique_outcome_id() -> None:
    book = _order_book(
        _quote(token_id="yes-token", outcome_id="yes-id", label="Yes"),
        _quote(token_id="no-token", outcome_id="no-id", label="No"),
    )

    quote = _find_outcome_quote(
        book,
        token_id=None,
        outcome_id="no-id",
        fallback_labels=frozenset({"yes"}),
    )

    assert quote is not None
    assert quote.token_id == "no-token"


def test_find_outcome_quote_ignores_ambiguous_outcome_id_and_uses_label() -> None:
    book = _order_book(
        _quote(token_id="yes-token", outcome_id="condition-1", label="Yes"),
        _quote(token_id="no-token", outcome_id="condition-1", label=" No "),
    )

    quote = _find_outcome_quote(
        book,
        token_id=None,
        outcome_id="condition-1",
        fallback_labels=frozenset({"no"}),
    )

    assert quote is not None
    assert quote.token_id == "no-token"


def test_find_outcome_quote_returns_none_without_a_safe_match() -> None:
    book = _order_book(
        _quote(token_id="yes-token", outcome_id="yes-id", label="Yes"),
        _quote(token_id="no-token", outcome_id="no-id", label="No"),
    )

    quote = _find_outcome_quote(
        book,
        token_id="missing-token",
        outcome_id="missing-id",
        fallback_labels=frozenset({"higher"}),
    )

    assert quote is None


def test_quote_age_is_zero_for_future_snapshots() -> None:
    age = _quote_age_seconds(
        OBSERVED_AT + timedelta(seconds=2),
        OBSERVED_AT,
    )

    assert age == Decimal("0.0")


def test_quote_age_treats_naive_timestamps_as_utc() -> None:
    naive = datetime(2026, 11, 11, 12, 0, 1, 250_000)
    age = _quote_age_seconds(OBSERVED_AT.replace(tzinfo=None), naive)

    assert age == Decimal("1.25")


def test_build_target_definition_maps_probability_yes_to_affirmative_outcome() -> None:
    definition = _build_target_definition(_market(), _contract_spec())

    assert definition["question"] == "Will BTC be up or down in the next 15 minutes?"
    assert definition["outcomes"] == ["Up", "Down"]
    assert definition["outcome_mapping_complete"] is True
    assert definition["affirmative_label"] == "Up"
    assert definition["negative_label"] == "Down"
    assert definition["affirmative_outcome_id"] == "up-id"
    assert definition["negative_outcome_id"] == "down-id"
    assert definition["affirmative_token_id"] == "up-token"
    assert definition["negative_token_id"] == "down-token"
    assert definition["direction"] == "up"
    assert definition["comparator"] == "gt"
    assert definition["strike_value"] == "74000"
    assert definition["settlement_timestamp"] == (
        OBSERVED_AT + timedelta(minutes=15)
    ).isoformat()
    assert definition["tradeable"] is True


def test_build_target_definition_flags_incomplete_outcome_mapping() -> None:
    spec = _contract_spec(
        affirmative_outcome_id="unknown-id",
        affirmative_token_id="unknown-token",
    )

    definition = _build_target_definition(_market(), spec)

    assert definition["outcome_mapping_complete"] is False
    assert definition["outcomes"] == ["Down", "Up"]
    assert definition["affirmative_label"] is None
    assert definition["negative_label"] == "Down"
