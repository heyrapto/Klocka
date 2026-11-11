from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from prediction_ai.integrations import network


@pytest.fixture(autouse=True)
def _clear_dns_state() -> Iterator[None]:
    network.clear_dns_cache()
    yield
    network.clear_dns_cache()


def test_public_ip_validation_rejects_private_and_loopback_addresses() -> None:
    assert network._is_public_ip("8.8.8.8")
    assert network._is_public_ip("1.1.1.1")
    assert not network._is_public_ip("127.0.0.1")
    assert not network._is_public_ip("10.0.0.7")
    assert not network._is_public_ip("192.168.1.10")
    assert not network._is_public_ip("169.254.169.254")
    assert not network._is_public_ip("::1")
    assert not network._is_public_ip("not-an-ip")


@pytest.mark.asyncio
async def test_resolver_prefers_system_answer_and_caches_it() -> None:
    calls: list[str] = []

    async def system_resolver(host: str) -> list[str]:
        calls.append(host)
        return ["93.184.216.34", "127.0.0.1"]

    resolver = network.DnsResolver(
        system_resolver=system_resolver,
        doh_endpoints=(),
        system_timeout_seconds=0.5,
    )
    try:
        first = await resolver.resolve("api.binance.test")
        second = await resolver.resolve("api.binance.test")
    finally:
        await resolver.aclose()

    assert first == ("93.184.216.34",)
    assert second == first
    assert calls == ["api.binance.test"]


@pytest.mark.asyncio
async def test_resolver_falls_back_to_doh_and_filters_non_public_answers() -> None:
    async def system_resolver(host: str) -> list[str]:
        raise OSError("system resolver unavailable")

    def doh_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["name"] == "api.binance.test"
        return httpx.Response(
            200,
            json={
                "Status": 0,
                "Answer": [
                    {"type": 1, "data": "10.0.0.5", "TTL": 60},
                    {"type": 1, "data": "93.184.216.34", "TTL": 60},
                ],
            },
        )

    doh_client = httpx.AsyncClient(transport=httpx.MockTransport(doh_handler))
    resolver = network.DnsResolver(
        system_resolver=system_resolver,
        doh_endpoints=("https://doh.test/resolve",),
        system_timeout_seconds=0.2,
        doh_timeout_seconds=1.0,
        doh_client=doh_client,
    )
    try:
        ips = await resolver.resolve("api.binance.test")
    finally:
        await resolver.aclose()
        await doh_client.aclose()

    assert ips == ("93.184.216.34",)


@pytest.mark.asyncio
async def test_transport_rewrites_connection_host_but_preserves_tls_hostname() -> None:
    observed: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["url_host"] = request.url.host
        observed["host_header"] = request.headers.get("Host")
        observed["sni_hostname"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, json={"ok": True})

    async def system_resolver(host: str) -> list[str]:
        return ["93.184.216.34"]

    resolver = network.DnsResolver(
        system_resolver=system_resolver,
        doh_endpoints=(),
        system_timeout_seconds=0.5,
    )
    transport = network.DnsResolvingTransport(
        resolver,
        inner_transport=httpx.MockTransport(handler),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get(
            "https://api.binance.test/api/v3/ticker/price",
            params={"symbol": "BTCUSDT"},
        )

    assert response.status_code == 200
    assert observed["url_host"] == "93.184.216.34"
    assert observed["host_header"] == "api.binance.test"
    assert observed["sni_hostname"] == "api.binance.test"


@pytest.mark.asyncio
async def test_transport_fails_closed_when_no_public_address_is_available() -> None:
    async def system_resolver(host: str) -> list[str]:
        return ["127.0.0.1"]

    resolver = network.DnsResolver(
        system_resolver=system_resolver,
        doh_endpoints=(),
        system_timeout_seconds=0.5,
    )
    transport = network.DnsResolvingTransport(
        resolver,
        inner_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"unexpected": True})
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError, match="DNS resolution failed"):
            await client.get("https://api.binance.test/api/v3/ticker/price")
