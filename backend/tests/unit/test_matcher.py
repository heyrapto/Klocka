from datetime import UTC, datetime

from prediction_ai.markets.matcher import match_candidate_to_platform_market
from prediction_ai.schemas.analysis import ScreenshotMarketCandidate
from prediction_ai.schemas.market import NormalizedMarket, NormalizedOutcome


def test_matcher_verified_exact():
    now = datetime(2026, 11, 15, 23, 59, 59, tzinfo=UTC)
    candidate = ScreenshotMarketCandidate(
        platform="polymarket",
        market_title="Will Bitcoin hit $100k by November 15, 2026?",
        asset_or_entity="BTC",
        displayed_expiry=now,
    )
    platform_market = NormalizedMarket(
        platform="polymarket",
        platform_market_id="poly_123",
        title="Will Bitcoin hit $100k by November 15, 2026?",
        category="crypto",
        market_type="binary_threshold",
        underlying_symbol="BTC/USD",
        outcomes=[
            NormalizedOutcome(outcome_id="1", token_id="tok_yes", label="YES"),
            NormalizedOutcome(outcome_id="2", token_id="tok_no", label="NO"),
        ],
        resolves_at=now,
        resolution_source="Binance 1m close",
        resolution_criteria="Resolves to YES if BTC touches $100,000",
        status="active",
    )

    status, score, discrepancies = match_candidate_to_platform_market(candidate, platform_market)
    assert status == "VERIFIED_EXACT"
    assert score >= 0.90
    assert len(discrepancies) == 0


def test_matcher_expiry_mismatch():
    candidate_date = datetime(2026, 11, 15, 23, 59, 59, tzinfo=UTC)
    market_date = datetime(2026, 11, 30, 23, 59, 59, tzinfo=UTC)

    candidate = ScreenshotMarketCandidate(
        platform="polymarket",
        market_title="Will Bitcoin hit $100k?",
        displayed_expiry=candidate_date,
    )
    platform_market = NormalizedMarket(
        platform="polymarket",
        platform_market_id="poly_123",
        title="Will Bitcoin hit $100k?",
        category="crypto",
        market_type="binary_threshold",
        outcomes=[
            NormalizedOutcome(outcome_id="1", token_id="tok_yes", label="YES"),
            NormalizedOutcome(outcome_id="2", token_id="tok_no", label="NO"),
        ],
        resolves_at=market_date,
        resolution_source="Binance",
        resolution_criteria="Touches 100k",
        status="active",
    )

    status, score, discrepancies = match_candidate_to_platform_market(candidate, platform_market)
    assert status == "NEEDS_CONFIRMATION"
    assert "Expiration date mismatch." in discrepancies
