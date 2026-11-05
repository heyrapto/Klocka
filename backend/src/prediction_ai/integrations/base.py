from typing import Protocol

from prediction_ai.schemas.market import NormalizedMarket, NormalizedOrderBook


class MarketAdapter(Protocol):
    """Read-only interface implemented by every prediction market provider."""

    platform: str

    async def search_markets(self, query: str, *, limit: int = 10) -> list[NormalizedMarket]: ...

    async def get_market(self, market_id: str) -> NormalizedMarket | None: ...

    async def get_market_by_slug(self, slug: str) -> list[NormalizedMarket]: ...

    async def get_order_book(self, market: NormalizedMarket) -> NormalizedOrderBook: ...

    async def check_health(self) -> bool: ...
