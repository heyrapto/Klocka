import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from prediction_ai.core.config import Settings
from prediction_ai.core.exceptions import UpstreamProviderException
from prediction_ai.integrations.http import ProviderHttpClient
from prediction_ai.schemas.market import (
    BookLevel,
    MarketCategory,
    MarketStatus,
    NormalizedMarket,
    NormalizedOrderBook,
    NormalizedOutcome,
    OutcomeQuote,
)

logger = structlog.get_logger(__name__)

_MAX_BOOK_LEVELS = 25
_CATEGORY_MAP: dict[str, MarketCategory] = {
    "CRYPTO": "crypto",
    "SPORTS": "sports",
    "POLITICS": "politics",
    "FINANCE": "finance",
    "NEWS": "news",
}
_ASSET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("bitcoin", "BTC"),
    ("btcusdt", "BTC"),
    ("ethereum", "ETH"),
    ("ethusdt", "ETH"),
    ("solana", "SOL"),
    ("solusdt", "SOL"),
    ("ripple", "XRP"),
    ("xrpusdt", "XRP"),
    ("dogecoin", "DOGE"),
    ("dogeusdt", "DOGE"),
)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "this",
        "to",
        "will",
        "with",
    }
)


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


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


def _infer_asset(pair: Any, title: str) -> str | None:
    text = f"{pair or ''} {title}".lower()
    for token, symbol in _ASSET_PATTERNS:
        if token in text:
            return symbol
    match = re.search(r"\b([A-Z]{2,6})[/-]?([A-Z]{2,6})\b", str(pair or ""))
    if match:
        return match.group(1)
    return None


def _category(value: Any) -> MarketCategory:
    return _CATEGORY_MAP.get(str(value or "").upper(), "other")


def _status(value: Any) -> MarketStatus:
    normalized = str(value or "").lower()
    if normalized in {"open", "active"}:
        return "active"
    if normalized in {"paused", "suspended"}:
        return "paused"
    if normalized in {"resolved", "settled"}:
        return "resolved"
    if normalized in {"void", "cancelled", "canceled"}:
        return "void"
    if normalized in {"disputed", "challenged"}:
        return "disputed"
    return "closed"


def _search_terms(text: str, *, max_terms: int = 6) -> str:
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if token not in _STOPWORDS and len(token) > 1
    ]
    if not tokens:
        return text.strip()[:200]
    # Prefer longer, more distinctive terms while preserving readable order.
    ranked = sorted(dict.fromkeys(tokens), key=len, reverse=True)[:max_terms]
    return " ".join(ranked)


def _event_list(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, dict):
        events = body.get("events")
        if isinstance(events, list):
            return [event for event in events if isinstance(event, dict)]
    if isinstance(body, list):
        return [event for event in body if isinstance(event, dict)]
    return []


class BayseAdapter:
    """Bayse discovery, metadata, and CLOB order book adapter."""

    platform = "bayse"

    def __init__(self, settings: Settings, client: ProviderHttpClient) -> None:
        self._settings = settings
        self._client = client

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    async def search_markets(self, query: str, *, limit: int = 10) -> list[NormalizedMarket]:
        cleaned = _search_terms(query)
        body = await self._client.get_json(
            "/v1/pm/events",
            params={
                "status": "open",
                "size": min(max(limit * 4, 10), 50),
                "currency": "USD",
                "keyword": cleaned[:200],
            },
        )
        markets = self._markets_from_events(_event_list(body))
        if not markets and cleaned != query.strip():
            body = await self._client.get_json(
                "/v1/pm/events",
                params={
                    "status": "open",
                    "size": min(max(limit * 4, 10), 50),
                    "currency": "USD",
                    "keyword": query.strip()[:200],
                },
            )
            markets = self._markets_from_events(_event_list(body))
        return _dedupe(markets)

    async def get_market(self, market_id: str) -> NormalizedMarket | None:
        try:
            event = await self._client.get_json(
                f"/v1/pm/events/{market_id}", params={"currency": "USD"}
            )
        except UpstreamProviderException:
            return None
        if not isinstance(event, dict):
            return None
        markets = self._markets_from_events([event])
        return markets[0] if len(markets) == 1 else None

    async def get_market_by_slug(self, slug: str) -> list[NormalizedMarket]:
        if _is_uuid(slug):
            market = await self.get_market(slug)
            return [market] if market is not None else []

        query = slug.replace("-", " ").replace("_", " ")
        for keyword in (query, _search_terms(query)):
            try:
                body = await self._client.get_json(
                    "/v1/pm/events",
                    params={
                        "status": "open",
                        "size": 25,
                        "currency": "USD",
                        "keyword": keyword[:200],
                    },
                )
            except UpstreamProviderException:
                continue
            events = _event_list(body)
            exact = [
                event
                for event in events
                if str(event.get("slug") or "").lower() == slug.lower()
                or str(event.get("id") or "").lower() == slug.lower()
            ]
            markets = self._markets_from_events(exact or events)
            if exact:
                return _dedupe(markets)
            if markets:
                return _dedupe(markets)
        return []

    # ------------------------------------------------------------------
    # Order books
    # ------------------------------------------------------------------
    async def get_order_book(self, market: NormalizedMarket) -> NormalizedOrderBook:
        outcome_ids = [outcome.token_id for outcome in market.outcomes if outcome.token_id]
        raw_books: list[dict[str, Any]] = []
        if outcome_ids:
            try:
                body = await self._client.get_json(
                    "/v1/pm/books",
                    params={
                        "outcomeId[]": outcome_ids,
                        "depth": 10,
                        "currency": "USD",
                    },
                )
            except UpstreamProviderException:
                body = []
            if isinstance(body, list):
                raw_books = [item for item in body if isinstance(item, dict)]

        by_outcome: dict[str, dict[str, Any]] = {}
        for raw in raw_books:
            outcome_id = str(raw.get("outcomeId") or "")
            if outcome_id:
                by_outcome[outcome_id] = raw

        quotes: list[OutcomeQuote] = []
        observed_at: datetime | None = None
        quality_flags: list[str] = []
        depth_known = market.order_book_enabled

        for outcome in market.outcomes:
            outcome_raw: dict[str, Any] | None = by_outcome.get(outcome.token_id)
            if outcome_raw is None:
                price = _displayed_price(market, outcome)
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

            bids = _levels(outcome_raw.get("bids"), reverse=True)
            asks = _levels(outcome_raw.get("asks"), reverse=False)
            best_bid = bids[0].price if bids else None
            best_ask = asks[0].price if asks else None
            mid_price = _midpoint(best_bid, best_ask)
            if mid_price is None:
                mid_price = _decimal(outcome_raw.get("lastTradedPrice"))
            timestamp = _parse_datetime(outcome_raw.get("timestamp"))
            if timestamp and (observed_at is None or timestamp > observed_at):
                observed_at = timestamp

            depth_bid = sum((level.price * level.size for level in bids), Decimal("0"))
            depth_ask = sum((level.price * level.size for level in asks), Decimal("0"))
            if depth_ask < Decimal("10"):
                quality_flags.append(f"thin_book:{outcome.label.lower()}")
            quotes.append(
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
                )
            )

        if observed_at is None:
            observed_at = datetime.now(UTC)
            if depth_known:
                quality_flags.append("book_timestamp_missing")
        if len(quotes) < 2:
            quality_flags.append("incomplete_quotes")
        return NormalizedOrderBook(
            platform="bayse",
            platform_market_id=market.platform_market_id,
            observed_at=observed_at,
            quotes=quotes,
            depth_known=depth_known,
            quality_flags=_dedupe_flags(quality_flags),
        )

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------
    def _markets_from_events(self, events: list[dict[str, Any]]) -> list[NormalizedMarket]:
        markets: list[NormalizedMarket] = []
        for event in events:
            event_markets = event.get("markets")
            if not isinstance(event_markets, list):
                continue
            for raw in event_markets:
                if not isinstance(raw, dict):
                    continue
                market = self.normalize_market(raw, event)
                if market is not None and market.status in {"active", "paused"}:
                    markets.append(market)
        return markets

    def normalize_market(
        self, raw: dict[str, Any], event: dict[str, Any]
    ) -> NormalizedMarket | None:
        market_id = raw.get("id")
        market_title = raw.get("title")
        event_title = event.get("title")
        if market_id is None or not isinstance(market_title, str) or not market_title.strip():
            return None
        if not isinstance(event_title, str) or not event_title.strip():
            event_title = market_title

        outcome_ids = [raw.get("outcome1Id"), raw.get("outcome2Id")]
        labels = [raw.get("outcome1Label"), raw.get("outcome2Label")]
        prices = [raw.get("outcome1Price"), raw.get("outcome2Price")]
        if not all(isinstance(item, str) and item.strip() for item in outcome_ids + labels):
            return None

        event_markets = event.get("markets")
        is_multi_market = isinstance(event_markets, list) and len(event_markets) > 1
        title = (
            f"{event_title.strip()} — {market_title.strip()}"
            if is_multi_market and event_title.strip().lower() != market_title.strip().lower()
            else market_title.strip()
        )

        engine = str(event.get("engine") or "").upper()
        order_book_enabled = engine == "CLOB"
        fee_percentage = _decimal(raw.get("feePercentage")) or Decimal("0")
        taker_fee_rate = fee_percentage / Decimal("100") if order_book_enabled else Decimal("0")
        slug = event.get("slug")
        event_id = event.get("id")
        url = (
            f"{self._settings.BAYSE_SITE_URL.rstrip('/')}/events/{event_id}/{slug}"
            if event_id and slug
            else f"{self._settings.BAYSE_SITE_URL.rstrip('/')}/events/{event_id}"
            if event_id
            else None
        )
        resolution_source = event.get("resolutionSource")
        rules = raw.get("rules")
        closes_at = _parse_datetime(event.get("closingDate"))
        resolves_at = _parse_datetime(event.get("resolutionDate")) or closes_at

        # Bayse interval market timestamp normalization:
        # Closing dates on Bayse are stored as the last millisecond of the interval
        # (e.g. 09:14:59.999000Z for a 15-minute interval ending at 09:15:00Z).
        # Snap closingDate ending at :59.999 or :59 to the clean interval second boundary.
        if closes_at is not None:
            if closes_at.microsecond >= 900_000:
                closes_at = (closes_at + timedelta(milliseconds=1000 - (closes_at.microsecond // 1000))).replace(microsecond=0)
            elif closes_at.second == 59 and closes_at.microsecond == 0:
                closes_at = closes_at + timedelta(seconds=1)

            # Bayse resolutionDate is set 60s after the interval candle closes
            # (e.g. 09:16:00Z vs 09:15:00Z) to allow the Chainlink TWAP oracle buffer.
            # The trade countdown and contract resolution correspond to candle close (closes_at).
            if resolves_at is not None and 30 <= (resolves_at - closes_at).total_seconds() <= 120:
                resolves_at = closes_at

        return NormalizedMarket(
            platform="bayse",
            platform_market_id=str(market_id),
            platform_event_id=str(event_id) if event_id else None,
            slug=str(slug) if slug else None,
            url=url,
            title=title,
            description=str(event.get("description") or "") or None,
            category=_category(event.get("category")),
            market_type="crypto_threshold" if engine == "CLOB" else "amm_binary",
            underlying_symbol=_infer_asset(event.get("assetSymbolPair"), title),
            outcomes=[
                NormalizedOutcome(
                    outcome_id=str(outcome_ids[0]),
                    token_id=str(outcome_ids[0]),
                    label=str(labels[0]).strip(),
                ),
                NormalizedOutcome(
                    outcome_id=str(outcome_ids[1]),
                    token_id=str(outcome_ids[1]),
                    label=str(labels[1]).strip(),
                ),
            ],
            opens_at=_parse_datetime(event.get("createdAt")),
            closes_at=closes_at,
            resolves_at=resolves_at,
            resolution_source=str(resolution_source) if resolution_source else None,
            resolution_criteria=str(rules) if rules else None,
            status=_status(raw.get("status") or event.get("status")),
            order_book_enabled=order_book_enabled,
            taker_fee_rate=taker_fee_rate,
            fee_model="variance_floor_half" if order_book_enabled else "none",
            raw_metadata={
                "market_id": str(market_id),
                "event_id": str(event_id) if event_id else None,
                "event_slug": slug,
                "event_title": event_title,
                "market_title": market_title,
                "engine": engine,
                "fee_percentage": str(fee_percentage),
                "minimum_order_amount": raw.get("minimumOrderAmount"),
                "outcome_prices": [_decimal_string(value) for value in prices],
                "liquidity_usd": event.get("liquidity"),
                "volume_usd": event.get("totalVolume"),
                "event_threshold": event.get("eventThreshold"),
                "market_threshold": raw.get("marketThreshold"),
                "asset_symbol_pair": event.get("assetSymbolPair"),
                "resolution_source": resolution_source,
                "series_slug": event.get("seriesSlug"),
            },
        )

    async def check_health(self) -> bool:
        try:
            body = await self._client.get_json(
                "/v1/pm/events", params={"status": "open", "size": 1, "currency": "USD"}
            )
        except Exception:
            return False
        return bool(_event_list(body))


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _decimal_string(value: Any) -> str | None:
    parsed = _decimal(value)
    return str(parsed) if parsed is not None else None


def _midpoint(bid: Decimal | None, ask: Decimal | None) -> Decimal | None:
    if bid is not None and ask is not None:
        return (bid + ask) / Decimal("2")
    return bid if bid is not None else ask


def _levels(raw_levels: Any, *, reverse: bool) -> list[BookLevel]:
    levels: list[BookLevel] = []
    if not isinstance(raw_levels, list):
        return levels
    for item in raw_levels:
        if not isinstance(item, dict):
            continue
        price = _decimal(item.get("price"))
        size = _decimal(item.get("quantity", item.get("size")))
        if price is None or size is None or price <= 0 or price >= 1 or size < 0:
            continue
        levels.append(BookLevel(price=price, size=size))
    levels.sort(key=lambda level: level.price, reverse=reverse)
    return levels


def _displayed_price(market: NormalizedMarket, outcome: NormalizedOutcome) -> Decimal | None:
    prices = market.raw_metadata.get("outcome_prices")
    if not isinstance(prices, list) or len(prices) != len(market.outcomes):
        return None
    for index, item in enumerate(market.outcomes):
        if item.token_id == outcome.token_id:
            value = _decimal(prices[index])
            if value is not None and Decimal("0") <= value <= Decimal("1"):
                return value
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
