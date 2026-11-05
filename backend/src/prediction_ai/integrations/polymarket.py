import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from prediction_ai.core.config import Settings
from prediction_ai.integrations.http import ProviderHttpClient
from prediction_ai.schemas.market import (
    BookLevel,
    NormalizedMarket,
    NormalizedOrderBook,
    NormalizedOutcome,
    OutcomeQuote,
)

logger = structlog.get_logger(__name__)

_FEE_RATES: dict[str, Decimal] = {
    "crypto": Decimal("0.07"),
    "sports": Decimal("0.05"),
    "finance": Decimal("0.04"),
    "politics": Decimal("0.04"),
    "news": Decimal("0.04"),
    "other": Decimal("0.05"),
}

_ASSET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("bitcoin", "BTC"),
    ("btc", "BTC"),
    ("ethereum", "ETH"),
    ("eth", "ETH"),
    ("solana", "SOL"),
    ("sol", "SOL"),
    ("ripple", "XRP"),
    ("xrp", "XRP"),
    ("dogecoin", "DOGE"),
    ("doge", "DOGE"),
)

_MAX_BOOK_LEVELS = 25


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _infer_category(title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    if any(token in text for token in ("bitcoin", "btc", "ethereum", "eth", "solana", "crypto")):
        return "crypto"
    if any(token in text for token in ("election", "president", "senate", "congress", "politics")):
        return "politics"
    if any(
        token in text
        for token in ("nfl", "nba", "mlb", "soccer", "football", "game", "match", "league")
    ):
        return "sports"
    if any(token in text for token in ("fed", "inflation", "gdp", "stock", "finance", "earnings")):
        return "finance"
    return "other"


def _infer_asset(title: str, description: str) -> str | None:
    text = f"{title} {description}".lower()
    for token, symbol in _ASSET_PATTERNS:
        if token in text:
            return symbol
    return None


class PolymarketAdapter:
    """Polymarket Gamma (metadata) and CLOB (order book) adapter."""

    platform = "polymarket"

    def __init__(
        self,
        settings: Settings,
        gamma_client: ProviderHttpClient,
        clob_client: ProviderHttpClient,
    ) -> None:
        self._settings = settings
        self._gamma = gamma_client
        self._clob = clob_client

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    async def search_markets(self, query: str, *, limit: int = 10) -> list[NormalizedMarket]:
        body = await self._gamma.get_json(
            "/public-search",
            params={
                "q": query[:200],
                "limit_per_type": min(max(limit, 1), 20),
                "events_status": "active",
            },
        )
        markets: list[NormalizedMarket] = []
        if isinstance(body, dict):
            for event in body.get("events") or []:
                if isinstance(event, dict):
                    markets.extend(self._normalize_event_markets(event))
        if markets:
            return _dedupe(markets)

        # Search endpoint differences across Gamma deployments: bounded fallback.
        fallback = await self._gamma.get_json(
            "/markets",
            params={
                "active": "true",
                "closed": "false",
                "limit": min(max(limit * 5, 10), 50),
                "order": "volume24hr",
                "ascending": "false",
            },
        )
        if isinstance(fallback, list):
            for raw in fallback:
                if isinstance(raw, dict):
                    market = self.normalize_market(raw)
                    if market is not None:
                        markets.append(market)
        return _dedupe(markets)

    async def get_market(self, market_id: str) -> NormalizedMarket | None:
        try:
            body = await self._gamma.get_json(f"/markets/{market_id}")
        except Exception:
            body = await self._gamma.get_json("/markets", params={"id": market_id, "limit": 1})
        if isinstance(body, list):
            body = body[0] if body else None
        return self.normalize_market(body) if isinstance(body, dict) else None

    async def get_market_by_slug(self, slug: str) -> list[NormalizedMarket]:
        raw_markets = await self._gamma.get_json(
            "/markets", params={"slug": slug, "limit": 10}
        )
        if isinstance(raw_markets, list) and raw_markets:
            normalized = [
                self.normalize_market(raw) for raw in raw_markets if isinstance(raw, dict)
            ]
            return [market for market in normalized if market is not None]

        events = await self._gamma.get_json("/events", params={"slug": slug, "limit": 5})
        if isinstance(events, list):
            found: list[NormalizedMarket] = []
            for event in events:
                if isinstance(event, dict):
                    found.extend(self._normalize_event_markets(event))
            return _dedupe(found)
        return []

    # ------------------------------------------------------------------
    # Order books
    # ------------------------------------------------------------------
    async def get_order_book(self, market: NormalizedMarket) -> NormalizedOrderBook:
        quotes: list[OutcomeQuote] = []
        observed_at: datetime | None = None
        quality_flags: list[str] = []
        depth_known = True
        raw_sequence: str | None = None

        for outcome in market.outcomes:
            book = await self._get_single_book(outcome.token_id)
            if book is None:
                price = _displayed_price(market, outcome.label)
                quotes.append(
                    OutcomeQuote(
                        outcome_id=outcome.outcome_id,
                        token_id=outcome.token_id,
                        label=outcome.label,
                        mid_price=price,
                        depth_ask_usd=Decimal("0"),
                        depth_bid_usd=Decimal("0"),
                    )
                )
                quality_flags.append(f"order_book_unavailable:{outcome.label.lower()}")
                depth_known = False
                continue

            quote, timestamp, sequence = self._normalize_book(outcome, book)
            quotes.append(quote)
            if timestamp and (observed_at is None or timestamp > observed_at):
                observed_at = timestamp
            if sequence:
                raw_sequence = sequence
            if quote.depth_ask_usd < Decimal("10"):
                quality_flags.append(f"thin_book:{outcome.label.lower()}")

        if observed_at is None:
            observed_at = _parse_datetime(market.raw_metadata.get("updated_at")) or datetime.now(
                UTC
            )
            if market.order_book_enabled:
                quality_flags.append("book_timestamp_missing")
        if len(quotes) < 2:
            quality_flags.append("incomplete_quotes")
        return NormalizedOrderBook(
            platform="polymarket",
            platform_market_id=market.platform_market_id,
            observed_at=observed_at,
            quotes=quotes,
            depth_known=depth_known,
            raw_sequence=raw_sequence,
            quality_flags=_dedupe_flags(quality_flags),
        )

    async def _get_single_book(self, token_id: str) -> dict[str, Any] | None:
        if not token_id:
            return None
        try:
            body = await self._clob.get_json("/book", params={"token_id": token_id})
        except Exception:
            return None
        return body if isinstance(body, dict) else None

    def _normalize_book(
        self, outcome: NormalizedOutcome, raw: dict[str, Any]
    ) -> tuple[OutcomeQuote, datetime | None, str | None]:
        bids = _levels(raw.get("bids"), reverse=True)
        asks = _levels(raw.get("asks"), reverse=False)
        best_bid = bids[0].price if bids else None
        best_ask = asks[0].price if asks else None
        mid_price: Decimal | None = None
        if best_bid is not None and best_ask is not None:
            mid_price = (best_bid + best_ask) / Decimal("2")
        elif best_bid is not None:
            mid_price = best_bid
        elif best_ask is not None:
            mid_price = best_ask
        elif raw.get("last_trade_price") is not None:
            mid_price = _decimal(raw.get("last_trade_price"))

        depth_bid = sum((level.price * level.size for level in bids), Decimal("0"))
        depth_ask = sum((level.price * level.size for level in asks), Decimal("0"))
        timestamp = _parse_epoch(raw.get("timestamp")) or _parse_datetime(raw.get("timestamp"))
        sequence = str(raw.get("hash")) if raw.get("hash") else None
        return (
            OutcomeQuote(
                outcome_id=outcome.outcome_id,
                token_id=outcome.token_id,
                label=outcome.label,
                best_bid=best_bid,
                best_ask=best_ask,
                mid_price=mid_price,
                bids=bids[:_MAX_BOOK_LEVELS],
                asks=asks[:_MAX_BOOK_LEVELS],
                depth_bid_usd=depth_bid,
                depth_ask_usd=depth_ask,
            ),
            timestamp,
            sequence,
        )

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------
    def _normalize_event_markets(self, event: dict[str, Any]) -> list[NormalizedMarket]:
        markets: list[NormalizedMarket] = []
        for raw in event.get("markets") or []:
            if isinstance(raw, dict):
                market = self.normalize_market(raw, event=event)
                if market is not None and market.status in {"active", "paused"}:
                    markets.append(market)
        return markets

    def normalize_market(
        self, raw: dict[str, Any], *, event: dict[str, Any] | None = None
    ) -> NormalizedMarket | None:
        market_id = raw.get("id")
        title = raw.get("question") or raw.get("title")
        if market_id is None or not isinstance(title, str) or not title.strip():
            return None

        outcome_labels = _json_list(raw.get("outcomes"))
        token_ids = _json_list(raw.get("clobTokenIds") or raw.get("clob_token_ids"))
        if len(outcome_labels) != 2 or len(token_ids) != 2:
            return None

        event_meta = event or _first_event(raw)
        description = raw.get("description") or (event_meta or {}).get("description") or ""
        event_title = (event_meta or {}).get("title") or title
        category = _infer_category(f"{title} {event_title}", str(description))
        slug = raw.get("slug") or (event_meta or {}).get("slug")
        url = f"{self._settings.POLYMARKET_SITE_URL}/market/{slug}" if slug else None
        fee_rate = _FEE_RATES.get(category, Decimal("0.05"))

        resolved = bool(raw.get("closed"))
        active = bool(raw.get("active")) and not resolved
        accepting = bool(raw.get("acceptingOrders", active))
        if resolved:
            status: str = "resolved"
        elif active and accepting:
            status = "active"
        elif active:
            status = "paused"
        else:
            status = "closed"

        return NormalizedMarket(
            platform="polymarket",
            platform_market_id=str(market_id),
            platform_event_id=str(
                (event_meta or {}).get("id") or raw.get("eventId") or ""
            )
            or None,
            slug=str(slug) if slug else None,
            url=url,
            title=title.strip(),
            description=str(description) if description else None,
            category=category,  # type: ignore[arg-type]
            market_type="binary_threshold" if category == "crypto" else "binary",
            underlying_symbol=_infer_asset(title, str(description)),
            outcomes=[
                NormalizedOutcome(
                    outcome_id=str(raw.get("conditionId") or market_id),
                    token_id=token_ids[0],
                    label=outcome_labels[0],
                ),
                NormalizedOutcome(
                    outcome_id=str(raw.get("conditionId") or market_id),
                    token_id=token_ids[1],
                    label=outcome_labels[1],
                ),
            ],
            opens_at=_parse_datetime(raw.get("startDate")),
            closes_at=_parse_datetime(raw.get("endDate")),
            resolves_at=_parse_datetime(raw.get("endDate")),
            resolution_source=(
                str(raw.get("resolutionSource") or (event_meta or {}).get("resolutionSource"))
                if (raw.get("resolutionSource") or (event_meta or {}).get("resolutionSource"))
                else None
            ),
            resolution_criteria=str(description) if description else None,
            status=status,  # type: ignore[arg-type]
            order_book_enabled=bool(raw.get("enableOrderBook", True)) and accepting,
            taker_fee_rate=fee_rate,
            fee_model="variance",
            raw_metadata={
                "id": str(market_id),
                "condition_id": raw.get("conditionId"),
                "slug": slug,
                "event_id": (event_meta or {}).get("id"),
                "event_slug": (event_meta or {}).get("slug"),
                "liquidity_usd": raw.get("liquidityNum") or raw.get("liquidity"),
                "volume_usd": raw.get("volumeNum") or raw.get("volume"),
                "spread": raw.get("spread"),
                "best_bid": raw.get("bestBid"),
                "best_ask": raw.get("bestAsk"),
                "last_trade_price": raw.get("lastTradePrice"),
                "outcome_prices": _json_list(raw.get("outcomePrices")),
                "updated_at": raw.get("updatedAt"),
                "end_date": raw.get("endDate"),
                "order_min_size": raw.get("orderMinSize"),
                "tick_size": raw.get("orderPriceMinTickSize"),
                "accepting_orders": accepting,
                "neg_risk": raw.get("negRisk"),
            },
        )

    async def check_health(self) -> bool:
        try:
            body = await self._gamma.get_json("/markets", params={"limit": 1})
        except Exception:
            return False
        return isinstance(body, list)


def _first_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    events = raw.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        return events[0]
    return None


def _displayed_price(market: NormalizedMarket, label: str) -> Decimal | None:
    prices = market.raw_metadata.get("outcome_prices")
    if isinstance(prices, list) and len(prices) == len(market.outcomes):
        try:
            index = [item.label.lower() for item in market.outcomes].index(label.lower())
        except ValueError:
            index = 0 if label.lower() in {"yes", "yes "} else 1
        value = _decimal(prices[index])
        if value is not None and Decimal("0") <= value <= Decimal("1"):
            return value
    return None


def _levels(raw_levels: Any, *, reverse: bool) -> list[BookLevel]:
    levels: list[BookLevel] = []
    if not isinstance(raw_levels, list):
        return levels
    for item in raw_levels:
        if not isinstance(item, dict):
            continue
        price = _decimal(item.get("price"))
        size = _decimal(item.get("size"))
        if price is None or size is None or price <= 0 or price >= 1 or size < 0:
            continue
        levels.append(BookLevel(price=price, size=size))
    levels.sort(key=lambda level: level.price, reverse=reverse)
    return levels


def _parse_epoch(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        numeric = float(str(value))
    except ValueError:
        return None
    if numeric > 10_000_000_000:
        numeric /= 1000.0
    try:
        return datetime.fromtimestamp(numeric, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _dedupe(markets: list[NormalizedMarket]) -> list[NormalizedMarket]:
    seen: set[str] = set()
    result: list[NormalizedMarket] = []
    for market in markets:
        if market.platform_market_id in seen:
            continue
        seen.add(market.platform_market_id)
        result.append(market)
    return result


def _dedupe_flags(flags: list[str]) -> list[str]:
    return list(dict.fromkeys(flags))
