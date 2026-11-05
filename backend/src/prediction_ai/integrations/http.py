import asyncio
import random
import time
from typing import Any

import httpx
import structlog

from prediction_ai.core.config import Settings
from prediction_ai.core.exceptions import (
    CircuitOpenException,
    ProviderTimeoutException,
    UpstreamProviderException,
)

logger = structlog.get_logger(__name__)

_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


class CircuitBreaker:
    """Small per-provider circuit breaker to shed failing dependencies."""

    def __init__(self, failure_threshold: int, reset_seconds: float) -> None:
        self._failure_threshold = failure_threshold
        self._reset_seconds = reset_seconds
        self._failures = 0
        self._open_until = 0.0

    def check(self, provider: str) -> None:
        if self._open_until > time.monotonic():
            raise CircuitOpenException(
                f"{provider} is temporarily unavailable after repeated failures."
            )

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._failure_threshold:
            self._open_until = time.monotonic() + self._reset_seconds
            self._failures = 0


class ProviderHttpClient:
    """Bounded, retrying JSON client for read-only provider APIs."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings,
        *,
        provider: str,
        base_url: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._provider = provider
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Accept": "application/json",
            "User-Agent": "prediction-intelligence/0.2 (+server-side analysis)",
            **(headers or {}),
        }
        self._breaker = CircuitBreaker(
            settings.PROVIDER_CIRCUIT_FAILURE_THRESHOLD,
            settings.PROVIDER_CIRCUIT_RESET_SECONDS,
        )

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, str | int | list[str]] | None = None,
    ) -> Any:
        response = await self._request("GET", path, params=params)
        return self._decode(response)

    async def post_json(self, path: str, *, payload: dict[str, Any]) -> Any:
        response = await self._request("POST", path, json=payload)
        return self._decode(response)

    async def get_raw(
        self,
        path: str,
        *,
        params: dict[str, str | int | list[str]] | None = None,
    ) -> httpx.Response:
        return await self._request("GET", path, params=params)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int | list[str]] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self._base_url}/{path.lstrip('/')}"
        attempts = self._settings.PROVIDER_MAX_RETRIES + 1
        headers = dict(self._headers)
        request_id = structlog.contextvars.get_contextvars().get("request_id")
        if request_id:
            headers["X-Request-ID"] = str(request_id)

        last_error: Exception | None = None
        for attempt in range(attempts):
            self._breaker.check(self._provider)
            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                    timeout=self._settings.PROVIDER_TIMEOUT_SECONDS,
                )
            except httpx.TimeoutException as err:
                last_error = err
                self._breaker.record_failure()
                if attempt + 1 < attempts:
                    await _sleep_backoff(attempt)
                    continue
                raise ProviderTimeoutException(f"{self._provider} request timed out.") from err
            except httpx.HTTPError as err:
                last_error = err
                self._breaker.record_failure()
                if attempt + 1 < attempts:
                    await _sleep_backoff(attempt)
                    continue
                raise UpstreamProviderException(
                    f"{self._provider} request failed."
                ) from err

            if response.status_code in _TRANSIENT_STATUS and attempt + 1 < attempts:
                self._breaker.record_failure()
                await _sleep_backoff(attempt, retry_after=response.headers.get("Retry-After"))
                continue

            if response.status_code in _TRANSIENT_STATUS:
                self._breaker.record_failure()
                raise UpstreamProviderException(
                    f"{self._provider} returned a transient error.",
                    headers=_safe_retry_headers(response),
                )

            if response.status_code >= 400:
                self._breaker.record_success()
                if response.status_code == 404:
                    raise UpstreamProviderException(
                        f"{self._provider} did not find the requested resource."
                    )
                raise UpstreamProviderException(
                    f"{self._provider} rejected the request.",
                    details=[{"status": response.status_code}],
                )

            if len(response.content) > self._settings.PROVIDER_MAX_RESPONSE_BYTES:
                self._breaker.record_failure()
                raise UpstreamProviderException(
                    f"{self._provider} returned an oversized response."
                )

            self._breaker.record_success()
            return response

        raise UpstreamProviderException(
            f"{self._provider} request failed after retries."
        ) from last_error

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as err:
            raise UpstreamProviderException(
                "Provider returned a non-JSON response."
            ) from err


def _safe_retry_headers(response: httpx.Response) -> dict[str, str]:
    retry_after = response.headers.get("Retry-After")
    return {"Retry-After": retry_after} if retry_after else {}


async def _sleep_backoff(attempt: int, retry_after: str | None = None) -> None:
    if retry_after:
        try:
            delay = max(0.0, min(float(retry_after), 10.0))
            await asyncio.sleep(delay)
            return
        except ValueError:
            pass
    delay = min(8.0, 0.35 * (2**attempt)) + random.uniform(0, 0.25)
    await asyncio.sleep(delay)
