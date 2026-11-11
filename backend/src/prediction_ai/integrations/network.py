"""Resilient outbound HTTP for public market-data APIs.

Why this exists
---------------
The application must keep gathering market data even when the *system resolver*
is unreliable.  A common failure mode on hotspots, corporate networks, and
some cloud egress gateways is that DNS lookups for financial APIs time out or
return ``SERVFAIL`` while the API itself is perfectly reachable.  In that
situation every ``httpx`` attempt fails as ``ConnectTimeout`` and the failure
looks like an API outage when it is really a resolver outage.

This module adds a narrow, auditable fallback:

1. Try the operating-system resolver first (fast path).
2. If that fails or times out, resolve the same host over DNS-over-HTTPS
   (DoH) against independent providers.  The default endpoints are IP
   literals (Cloudflare and Google) so the fallback can bootstrap even when
   hostname resolution is the broken component.  Hostname endpoints may be
   configured when a deployment can already resolve them.
3. Connect to the returned IP while preserving the original ``Host`` header
   and TLS ``server_hostname``.  Certificate verification is therefore still
   performed against the real exchange hostname; TLS is never disabled.
4. Cache positive answers and short-lived negative answers so an analysis does
   not pay the resolver penalty more than once.

The fallback is intentionally limited to the HTTP client used for external
market data.  It is disabled automatically when an explicit HTTP proxy is
configured, because in that deployment the proxy owns name resolution.

No private, loopback, link-local, or otherwise non-global address is accepted
from either resolver.  This keeps the transport from becoming an SSRF pivot if
a resolver response is ever tampered with.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import httpx
import structlog

from prediction_ai.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)

# Prefer IP-literal DoH endpoints.  When the system resolver is the thing that
# is broken, a DoH endpoint addressed by hostname cannot be bootstrapped.
# The endpoints below need no DNS lookup and are operated by independent
# providers (Google, Cloudflare, and Quad9).
_DEFAULT_DOH_ENDPOINTS: tuple[str, ...] = (
    "https://8.8.8.8/resolve",
    "https://1.1.1.1/dns-query",
)

_USER_AGENT = "prediction-intelligence/0.2 (+market-data)"
_MAX_CACHE_ENTRIES = 256


@dataclass(frozen=True, slots=True)
class _DnsCacheEntry:
    ips: tuple[str, ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class _DohAnswer:
    ips: tuple[str, ...]
    ttl_seconds: float
    is_nxdomain: bool = False


# Process-wide cache.  Only immutable tuples are stored, and updates are simple
# assignments, so the cache is safe to share across event loops (Celery
# prefork workers, tests) without binding an asyncio primitive to one loop.
_DNS_CACHE: dict[str, _DnsCacheEntry] = {}
_SYSTEM_DNS_FAILED_UNTIL: dict[str, float] = {}
_LAST_KNOWN_IPS: dict[str, tuple[str, ...]] = {}
_DOH_ENDPOINT_BACKOFF: dict[str, float] = {}
_DOH_ENDPOINT_FAILURES: dict[str, int] = {}


def clear_dns_cache() -> None:
    """Clear cached answers (used by tests and by connectivity diagnostics)."""

    _DNS_CACHE.clear()
    _SYSTEM_DNS_FAILED_UNTIL.clear()
    _LAST_KNOWN_IPS.clear()
    _DOH_ENDPOINT_BACKOFF.clear()
    _DOH_ENDPOINT_FAILURES.clear()


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.is_global)


def _dedupe_public_ips(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen or not _is_public_ip(value):
            continue
        seen.add(value)
        result.append(value)
    # Prefer IPv4.  Many container and hotspot networks advertise a broken
    # IPv6 route, and trying IPv4 first avoids a needless connect timeout.
    result.sort(key=lambda item: 0 if ":" not in item else 1)
    return tuple(result)


def _cache_get(host: str) -> tuple[str, ...] | None:
    entry = _DNS_CACHE.get(host)
    if entry is None:
        return None
    if entry.expires_at <= time.monotonic():
        _DNS_CACHE.pop(host, None)
        return None
    return entry.ips


def _cache_set(host: str, ips: tuple[str, ...], ttl_seconds: float) -> None:
    if ips:
        _LAST_KNOWN_IPS[host] = ips
    if len(_DNS_CACHE) >= _MAX_CACHE_ENTRIES:
        now = time.monotonic()
        for key, entry in list(_DNS_CACHE.items()):
            if entry.expires_at <= now:
                _DNS_CACHE.pop(key, None)
        if len(_DNS_CACHE) >= _MAX_CACHE_ENTRIES:
            _DNS_CACHE.pop(next(iter(_DNS_CACHE)), None)
    _DNS_CACHE[host] = _DnsCacheEntry(
        ips=ips,
        expires_at=time.monotonic() + max(1.0, ttl_seconds),
    )


async def _default_system_resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(
        host,
        443,
        type=socket.SOCK_STREAM,
        family=socket.AF_UNSPEC,
    )
    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if not isinstance(sockaddr, tuple) or not sockaddr:
            continue
        address = sockaddr[0]
        if isinstance(address, str) and address:
            addresses.append(address)
    return addresses


SystemResolver = Callable[[str], Awaitable[list[str]]]


class DnsResolver:
    """System resolver with an authenticated DoH fallback and a small cache."""

    def __init__(
        self,
        *,
        doh_endpoints: Sequence[str] = _DEFAULT_DOH_ENDPOINTS,
        system_timeout_seconds: float = 1.5,
        doh_timeout_seconds: float = 2.5,
        cache_ttl_seconds: float = 300.0,
        negative_cache_seconds: float = 30.0,
        system_resolver: SystemResolver | None = None,
        doh_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoints = tuple(endpoint for endpoint in doh_endpoints if endpoint.strip())
        self._system_timeout = system_timeout_seconds
        self._doh_timeout = doh_timeout_seconds
        self._cache_ttl = cache_ttl_seconds
        self._negative_cache_ttl = negative_cache_seconds
        self._system_resolve = system_resolver or _default_system_resolve
        self._owns_doh_client = doh_client is None
        self._doh_client = doh_client or httpx.AsyncClient(
            timeout=httpx.Timeout(doh_timeout_seconds, connect=min(1.5, doh_timeout_seconds)),
            follow_redirects=True,
            headers={
                "Accept": "application/dns-json",
                "User-Agent": _USER_AGENT,
            },
            limits=httpx.Limits(
                max_connections=64,
                max_keepalive_connections=32,
                keepalive_expiry=60.0,
            ),
        )
        self._inflight: dict[str, asyncio.Task[tuple[str, ...]]] = {}

    async def aclose(self) -> None:
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight.clear()
        if self._owns_doh_client:
            await self._doh_client.aclose()

    async def resolve(self, host: str) -> tuple[str, ...]:
        """Return globally routable IPs for ``host`` or an empty tuple."""

        if _is_public_ip(host):
            return (host,)

        cached = _cache_get(host)
        if cached is not None:
            return cached

        existing = self._inflight.get(host)
        if existing is not None:
            return await asyncio.shield(existing)

        task = asyncio.create_task(self._resolve_uncached(host))

        def _discard(completed: asyncio.Task[tuple[str, ...]], key: str = host) -> None:
            if self._inflight.get(key) is completed:
                self._inflight.pop(key, None)

        task.add_done_callback(_discard)
        self._inflight[host] = task
        return await asyncio.shield(task)

    async def _resolve_uncached(self, host: str) -> tuple[str, ...]:
        ips: tuple[str, ...] = ()
        system_blocked_until = _SYSTEM_DNS_FAILED_UNTIL.get(host, 0.0)

        if system_blocked_until <= time.monotonic():
            try:
                raw_ips = await asyncio.wait_for(
                    self._system_resolve(host),
                    timeout=self._system_timeout,
                )
                ips = _dedupe_public_ips(raw_ips)
                if ips:
                    _SYSTEM_DNS_FAILED_UNTIL.pop(host, None)
                    _cache_set(host, ips, self._cache_ttl)
                    return ips
                logger.warning(
                    "System DNS returned no public addresses",
                    host=host,
                )
            except (TimeoutError, OSError) as exc:
                logger.warning(
                    "System DNS resolution failed; falling back to DNS-over-HTTPS",
                    host=host,
                    error=type(exc).__name__,
                    error_detail=str(exc) or None,
                )
            # Transient system DNS failure: only back off briefly (15s) instead of 300s
            _SYSTEM_DNS_FAILED_UNTIL[host] = time.monotonic() + 15.0

        if not self._endpoints:
            stale = _LAST_KNOWN_IPS.get(host)
            if stale:
                logger.warning(
                    "Serving stale cached IP addresses after system DNS failure (no DoH endpoints)",
                    host=host,
                    stale_ips=stale,
                )
                return stale
            _cache_set(host, (), self._negative_cache_ttl)
            return ()

        answer, errors = await self._resolve_doh(host)
        if answer is not None and answer.ips:
            _cache_set(host, answer.ips, min(answer.ttl_seconds, self._cache_ttl))
            return answer.ips

        # If live resolvers failed, serve stale cached IP if available (stale-while-revalidate)
        stale = _LAST_KNOWN_IPS.get(host)
        if stale:
            logger.warning(
                "Serving stale cached IP addresses after DNS resolver failure",
                host=host,
                stale_ips=stale,
            )
            _cache_set(host, stale, 30.0)
            return stale

        logger.warning(
            "DNS resolution failed for market-data host",
            host=host,
            doh_endpoints=list(self._endpoints),
            doh_errors=errors or None,
        )
        # Only negative-cache if the domain was explicitly confirmed as NXDOMAIN
        if answer is not None and answer.is_nxdomain:
            _cache_set(host, (), self._negative_cache_ttl)
        return ()

    async def _resolve_doh(
        self, host: str
    ) -> tuple[_DohAnswer | None, dict[str, str]]:
        now = time.monotonic()
        # Circuit breaker: prioritize healthy endpoints that are not in backoff
        healthy = [ep for ep in self._endpoints if _DOH_ENDPOINT_BACKOFF.get(ep, 0.0) <= now]
        endpoints_to_query = healthy if healthy else list(self._endpoints)

        tasks: dict[asyncio.Task[tuple[_DohAnswer | None, str | None]], str] = {
            asyncio.create_task(self._query_doh(endpoint, host)): endpoint
            for endpoint in endpoints_to_query
        }
        pending = set(tasks)
        errors: dict[str, str] = {}
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    timeout=self._doh_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    break
                for task in done:
                    endpoint = tasks[task]
                    try:
                        answer, error = task.result()
                    except Exception as exc:  # pragma: no cover - defensive
                        answer, error = None, f"{type(exc).__name__}: {exc}"
                    if error:
                        errors[endpoint] = error
                        fails = _DOH_ENDPOINT_FAILURES.get(endpoint, 0) + 1
                        _DOH_ENDPOINT_FAILURES[endpoint] = fails
                        if fails >= 2:
                            _DOH_ENDPOINT_BACKOFF[endpoint] = time.monotonic() + 60.0
                    if answer is not None and answer.ips:
                        # Reset failure counter on success
                        _DOH_ENDPOINT_FAILURES[endpoint] = 0
                        _DOH_ENDPOINT_BACKOFF.pop(endpoint, None)
                        logger.info(
                            "Resolved market-data host via DNS-over-HTTPS",
                            host=host,
                            endpoint=endpoint,
                            ip_count=len(answer.ips),
                            ttl_seconds=round(answer.ttl_seconds, 1),
                        )
                        return answer, errors
            for task in pending:
                endpoint = tasks[task]
                errors[endpoint] = "timeout"
                fails = _DOH_ENDPOINT_FAILURES.get(endpoint, 0) + 1
                _DOH_ENDPOINT_FAILURES[endpoint] = fails
                if fails >= 2:
                    _DOH_ENDPOINT_BACKOFF[endpoint] = time.monotonic() + 60.0
            return None, errors
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _query_doh(
        self, endpoint: str, host: str
    ) -> tuple[_DohAnswer | None, str | None]:
        try:
            response = await self._doh_client.get(
                endpoint,
                params={"name": host, "type": "A"},
            )
        except httpx.HTTPError as exc:
            return None, f"{type(exc).__name__}: {exc}"

        if response.status_code != 200:
            return None, f"HTTP {response.status_code}"
        try:
            payload = response.json()
        except ValueError as exc:
            return None, f"invalid JSON: {exc}"

        if not isinstance(payload, dict):
            return None, "invalid JSON: payload not a dict"

        status = payload.get("Status")
        if status == 3:  # NXDOMAIN
            return _DohAnswer(ips=(), ttl_seconds=60.0, is_nxdomain=True), "NXDOMAIN"
        if status != 0:
            return None, f"DNS status {status if status is not None else 'unknown'}"

        answers = payload.get("Answer")
        if not isinstance(answers, list):
            return None, "missing Answer section"

        ips: list[str] = []
        ttls: list[float] = []
        for answer in answers:
            if not isinstance(answer, dict) or answer.get("type") != 1:
                continue
            value = answer.get("data")
            if isinstance(value, str) and _is_public_ip(value):
                ips.append(value)
            try:
                ttls.append(float(answer.get("TTL", 300)))
            except (TypeError, ValueError):
                ttls.append(300.0)

        deduped = _dedupe_public_ips(ips)
        if not deduped:
            return None, "no public A records"
        return _DohAnswer(
            ips=deduped,
            ttl_seconds=min(ttls) if ttls else 300.0,
        ), None


class DnsResolvingTransport(httpx.AsyncBaseTransport):
    """Wrap an httpx transport and connect to DoH-resolved IPs.

    The original hostname is kept in the ``Host`` header and in the
    ``sni_hostname`` request extension, so virtual hosting and certificate
    verification continue to work exactly as before.
    """

    def __init__(
        self,
        resolver: DnsResolver,
        *,
        inner_transport: httpx.AsyncBaseTransport | None = None,
        limits: httpx.Limits | None = None,
        retries: int = 1,
    ) -> None:
        self._resolver = resolver
        self._inner = inner_transport or httpx.AsyncHTTPTransport(
            verify=True,
            http1=True,
            http2=False,
            retries=retries,
            limits=limits or httpx.Limits(
                max_connections=40,
                max_keepalive_connections=20,
                keepalive_expiry=15.0,
            ),
        )

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        host = request.url.host
        if not host or _is_public_ip(host):
            return await self._inner.handle_async_request(request)

        ips = await self._resolver.resolve(host)
        if not ips:
            raise httpx.ConnectError(
                f"DNS resolution failed for {host} (system resolver and DoH fallback)",
                request=request,
            )

        # Retrying across IPs is only safe for requests without a one-shot
        # streaming body.  All market-data calls are GETs.
        candidates = ips[:3] if request.method in {"GET", "HEAD"} else ips[:1]
        last_error: httpx.HTTPError | None = None
        for ip in candidates:
            try:
                return await self._inner.handle_async_request(
                    self._rewrite(request, host, ip)
                )
            except httpx.HTTPError as exc:
                last_error = exc
                continue

        if last_error is not None:
            raise last_error
        raise httpx.ConnectError(
            f"Could not connect to any resolved address for {host}",
            request=request,
        )

    @staticmethod
    def _rewrite(request: httpx.Request, host: str, ip: str) -> httpx.Request:
        url = request.url.copy_with(host=ip)
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        return httpx.Request(
            method=request.method,
            url=url,
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )

    async def aclose(self) -> None:
        await self._inner.aclose()
        await self._resolver.aclose()


def _settings_doh_endpoints(settings: Settings) -> tuple[str, ...]:
    raw = settings.MARKET_DATA_DOH_ENDPOINTS.strip()
    if not raw:
        return ()
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _environment_proxy() -> str | None:
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(key)
        if value:
            return value
    return None


KNOWN_MARKET_DATA_HOSTS: tuple[str, ...] = (
    "api.binance.com",
    "fapi.binance.com",
    "api.binance.us",
    "api.coinbase.com",
    "api.exchange.coinbase.com",
    "api.kraken.com",
    "www.okx.com",
    "api.bybit.com",
    "api.gateio.ws",
    "api.mexc.com",
    "api.coingecko.com",
    "coins.llama.fi",
    "benchmarks.pyth.network",
    "api.alternative.me",
)


async def prewarm_market_data_dns(
    resolver: DnsResolver | None = None,
    hosts: Sequence[str] = KNOWN_MARKET_DATA_HOSTS,
) -> dict[str, int]:
    """Pre-resolve known market data hosts concurrently into in-memory cache.

    Pre-warming ensures that live pipeline analyses never pay cold DNS lookup
    penalties and encounter zero connect latency on critical-path exchange API calls.
    """
    resolved_resolver = resolver
    owns = False
    if resolved_resolver is None:
        resolved_resolver = DnsResolver()
        owns = True

    results: dict[str, int] = {}
    try:
        tasks = [resolved_resolver.resolve(h) for h in hosts]
        resolved = await asyncio.gather(*tasks, return_exceptions=True)
        for h, res in zip(hosts, resolved, strict=False):
            if isinstance(res, tuple):
                results[h] = len(res)
            else:
                results[h] = 0
        resolved_count = sum(1 for v in results.values() if v > 0)
        logger.info(
            "Market data DNS pre-warming completed",
            total_hosts=len(hosts),
            resolved_hosts=resolved_count,
        )
    finally:
        if owns:
            await resolved_resolver.aclose()
    return results


def build_resilient_client(
    settings: Settings | None = None,
    *,
    timeout: httpx.Timeout | float | None = None,
    limits: httpx.Limits | None = None,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """Build the HTTP client used for external market-data access.

    Falls back to a plain client when DNS fallback is disabled or when an
    explicit environment proxy is configured (the proxy performs resolution).
    """

    resolved = settings or get_settings()
    request_timeout = timeout or httpx.Timeout(
        resolved.MARKET_DATA_READ_TIMEOUT_SECONDS,
        connect=resolved.MARKET_DATA_CONNECT_TIMEOUT_SECONDS,
        pool=2.0,
    )
    request_limits = limits or httpx.Limits(
        max_connections=40,
        max_keepalive_connections=20,
        keepalive_expiry=15.0,
    )
    request_headers = {"User-Agent": _USER_AGENT, **(headers or {})}

    proxy = _environment_proxy()
    if proxy:
        logger.info(
            "Environment proxy detected; market-data DNS fallback is disabled",
            proxy_scheme=proxy.split("://", 1)[0],
        )
        return httpx.AsyncClient(
            timeout=request_timeout,
            limits=request_limits,
            headers=request_headers,
            follow_redirects=follow_redirects,
        )

    if not resolved.MARKET_DATA_DNS_FALLBACK_ENABLED:
        return httpx.AsyncClient(
            timeout=request_timeout,
            limits=request_limits,
            headers=request_headers,
            follow_redirects=follow_redirects,
        )

    resolver = DnsResolver(
        doh_endpoints=_settings_doh_endpoints(resolved),
        system_timeout_seconds=resolved.MARKET_DATA_DNS_SYSTEM_TIMEOUT_SECONDS,
        doh_timeout_seconds=resolved.MARKET_DATA_DNS_DOH_TIMEOUT_SECONDS,
        cache_ttl_seconds=resolved.MARKET_DATA_DNS_CACHE_TTL_SECONDS,
        negative_cache_seconds=resolved.MARKET_DATA_DNS_NEGATIVE_CACHE_SECONDS,
    )
    transport = DnsResolvingTransport(
        resolver,
        limits=request_limits,
        retries=1,
    )
    return httpx.AsyncClient(
        transport=transport,
        timeout=request_timeout,
        headers=request_headers,
        follow_redirects=follow_redirects,
    )
