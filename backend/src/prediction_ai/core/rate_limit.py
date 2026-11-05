import asyncio
import hashlib
import time
from dataclasses import dataclass

import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    limit: int = 0
    remaining: int = 0


class RateLimiter:
    """Distributed fixed-window limiter with an in-process fallback.

    The fallback keeps a single API process usable without Redis. In a
    multi-instance deployment Redis provides the shared view.
    """

    def __init__(self, redis_client: Redis | None = None) -> None:
        self._redis = redis_client
        self._memory: dict[str, tuple[int, float]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:24]

    async def check(
        self,
        *,
        scope: str,
        identity: str,
        limits: list[tuple[int, int]],
    ) -> RateLimitDecision:
        """Check multiple ``(limit, window_seconds)`` rules for one identity."""
        if not limits:
            return RateLimitDecision(allowed=True)

        identifier = self._digest(identity)
        if self._redis is not None:
            try:
                return await self._check_redis(scope, identifier, limits)
            except Exception:
                logger.warning("Redis rate limiter unavailable; using local fallback")

        return await self._check_memory(scope, identifier, limits)

    async def _check_redis(
        self,
        scope: str,
        identifier: str,
        limits: list[tuple[int, int]],
    ) -> RateLimitDecision:
        assert self._redis is not None
        now = int(time.time())
        denied: list[tuple[int, int]] = []
        tightest_remaining: int | None = None
        tightest_limit = 0

        for limit, window in limits:
            key = f"ratelimit:{scope}:{identifier}:{window}:{now // window}"
            count = int(await self._redis.incr(key))
            if count == 1:
                await self._redis.expire(key, window + 1)
            ttl = int(await self._redis.ttl(key))
            remaining = max(0, limit - count)
            if tightest_remaining is None or remaining < tightest_remaining:
                tightest_remaining = remaining
                tightest_limit = limit
            if count > limit:
                denied.append((count, max(1, ttl)))

        if denied:
            retry_after = max(item[1] for item in denied)
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=retry_after,
                limit=tightest_limit,
                remaining=0,
            )
        return RateLimitDecision(
            allowed=True,
            limit=tightest_limit,
            remaining=tightest_remaining or 0,
        )

    async def _check_memory(
        self,
        scope: str,
        identifier: str,
        limits: list[tuple[int, int]],
    ) -> RateLimitDecision:
        async with self._lock:
            now = time.monotonic()
            denied: list[tuple[int, float]] = []
            tightest_remaining: int | None = None
            tightest_limit = 0

            for limit, window in limits:
                key = f"{scope}:{identifier}:{window}"
                count, reset_at = self._memory.get(key, (0, now + window))
                if reset_at <= now:
                    count, reset_at = 0, now + window
                count += 1
                self._memory[key] = (count, reset_at)
                remaining = max(0, limit - count)
                if tightest_remaining is None or remaining < tightest_remaining:
                    tightest_remaining = remaining
                    tightest_limit = limit
                if count > limit:
                    denied.append((count, reset_at - now))

            if denied:
                return RateLimitDecision(
                    allowed=False,
                    retry_after_seconds=max(1, int(max(item[1] for item in denied))),
                    limit=tightest_limit,
                    remaining=0,
                )
            return RateLimitDecision(
                allowed=True,
                limit=tightest_limit,
                remaining=tightest_remaining or 0,
            )

    def purge_expired_memory_entries(self) -> None:
        now = time.monotonic()
        stale = [key for key, (_, reset_at) in self._memory.items() if reset_at <= now]
        for key in stale:
            self._memory.pop(key, None)
