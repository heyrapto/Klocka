"""SSRF-safe, budgeted HTTP retrieval for approved research sources.

The fetcher follows redirects manually, revalidating every target, and caps
response bytes while streaming. It never submits forms, authenticates, or
performs state-changing requests.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

import httpx
import structlog

from prediction_ai.research.budget import BudgetExceededError, BudgetLedger
from prediction_ai.research.enums import BudgetCategory, SourceFailureCode
from prediction_ai.research.models import SourceRegistryEntry
from prediction_ai.research.ssrf import (
    BlockedDestinationError,
    assert_resolution_stable,
    resolve_and_validate,
)
from prediction_ai.research.time import Clock

logger = structlog.get_logger(__name__)

_ALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/xml",
    "text/xml",
    "application/rss+xml",
    "application/atom+xml",
)


class FetchError(Exception):
    code = SourceFailureCode.SOURCE_UNAVAILABLE
    transient = False

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(message)


class SsrfBlockedError(FetchError):
    code = SourceFailureCode.SSRF_BLOCKED


class RedirectBlockedError(FetchError):
    code = SourceFailureCode.REDIRECT_BLOCKED


class SourceRateLimited(FetchError):
    code = SourceFailureCode.SOURCE_RATE_LIMITED
    transient = True


class SourceAccessDenied(FetchError):
    code = SourceFailureCode.SOURCE_ACCESS_DENIED


class SourceUnavailable(FetchError):
    code = SourceFailureCode.SOURCE_UNAVAILABLE


class SourceTooLarge(FetchError):
    code = SourceFailureCode.SOURCE_TOO_LARGE


class UnsupportedContentType(FetchError):
    code = SourceFailureCode.SOURCE_UNSUPPORTED_CONTENT


class TransientFetchError(FetchError):
    code = SourceFailureCode.SOURCE_UNAVAILABLE
    transient = True


@dataclass(frozen=True, slots=True)
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    text: str
    captured_at: datetime
    redirect_chain: tuple[str, ...]


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, min(float(value), 30.0))
    except ValueError:
        return None


class SafeFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        budget: BudgetLedger,
        clock: Clock,
        timeout_seconds: float = 12.0,
        user_agent: str = "prediction-intelligence-research/0.1 (+https://example.invalid/contact)",
    ) -> None:
        self._client = client
        self._budget = budget
        self._clock = clock
        self._timeout = timeout_seconds
        self._user_agent = user_agent

    async def fetch(self, url: str, *, source: SourceRegistryEntry) -> FetchResult:
        """Fetch one approved URL with manual redirect validation."""

        allowed = (source.domain_pattern,)
        current = url
        redirects: list[str] = []
        max_redirects = self._budget.limits.max_redirects

        for _ in range(max_redirects + 1):
            try:
                validated = await resolve_and_validate(current, allowed_domains=allowed)
                second = await resolve_and_validate(validated.url, allowed_domains=allowed)
                assert_resolution_stable(validated, second)
            except BlockedDestinationError as err:
                raise (
                    RedirectBlockedError(str(err))
                    if redirects
                    else SsrfBlockedError(str(err))
                ) from err

            self._budget.authorize(
                BudgetCategory.SOURCE_DOCUMENTS, provider=source.provider_key
            )

            try:
                async with self._client.stream(
                    "GET",
                    validated.url,
                    headers={
                        "User-Agent": self._user_agent,
                        "Accept": ", ".join(_ALLOWED_CONTENT_TYPES),
                        "Accept-Language": "en",
                    },
                    follow_redirects=False,
                    timeout=self._timeout,
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise SourceUnavailable("Redirect response had no Location header.")
                        if len(redirects) >= max_redirects:
                            raise RedirectBlockedError("Redirect limit exceeded.")
                        self._budget.authorize(BudgetCategory.REDIRECTS)
                        next_url = str(httpx.URL(validated.url).join(location))
                        redirects.append(next_url)
                        current = next_url
                        continue

                    if response.status_code == 429:
                        raise SourceRateLimited(
                            "Source rate limited the request.",
                            retry_after=_parse_retry_after(response.headers.get("Retry-After")),
                        )
                    if response.status_code in {401, 403}:
                        raise SourceAccessDenied(
                            "Source denied access; the runtime does not bypass access controls."
                        )
                    if response.status_code in {404, 410}:
                        raise SourceUnavailable("Source document is no longer available.")
                    if response.status_code >= 500:
                        raise TransientFetchError(
                            "Source returned a transient server error.",
                            retry_after=_parse_retry_after(response.headers.get("Retry-After")),
                        )
                    if response.status_code >= 400:
                        raise SourceUnavailable(
                            f"Source returned HTTP {response.status_code}."
                        )

                    content_type = response.headers.get("content-type", "application/octet-stream")
                    media_type = content_type.split(";", 1)[0].strip().lower()
                    if media_type not in _ALLOWED_CONTENT_TYPES:
                        raise UnsupportedContentType(
                            f"Content type '{media_type}' is not supported for Phase 1 extraction."
                        )

                    declared_length = response.headers.get("content-length")
                    if declared_length and declared_length.isdigit():
                        if int(declared_length) > source.max_response_bytes:
                            raise SourceTooLarge(
                                "Declared response size exceeds the source limit."
                            )

                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > source.max_response_bytes:
                            raise SourceTooLarge("Response exceeded the source byte limit.")
                        self._budget.authorize(
                            BudgetCategory.BYTES_DOWNLOADED,
                            units=len(chunk),
                        )
                        chunks.append(chunk)

                    body = b"".join(chunks)
                    captured_at = self._clock.now()
                    return FetchResult(
                        requested_url=url,
                        final_url=validated.url,
                        status_code=response.status_code,
                        content_type=content_type,
                        body=body,
                        text=body.decode("utf-8", errors="replace"),
                        captured_at=captured_at,
                        redirect_chain=tuple(redirects),
                    )
            except httpx.TimeoutException as err:
                raise TransientFetchError("Source request timed out.") from err
            except httpx.HTTPError as err:
                raise TransientFetchError("Source request failed.") from err

        raise RedirectBlockedError("Redirect limit exceeded.")

    async def fetch_with_retry(
        self,
        url: str,
        *,
        source: SourceRegistryEntry,
        max_attempts: int = 2,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> FetchResult:
        """Retry only transient failures, within the retry budget."""

        attempts = max(1, min(max_attempts, 3))
        last_error: FetchError | None = None
        for attempt in range(attempts):
            try:
                return await self.fetch(url, source=source)
            except FetchError as err:
                last_error = err
                if not err.transient or attempt + 1 >= attempts:
                    raise
                try:
                    self._budget.authorize(BudgetCategory.RETRIES, provider=source.provider_key)
                except BudgetExceededError:
                    raise err from None
                delay = err.retry_after
                if delay is None:
                    delay = min(4.0, 0.35 * (2**attempt)) + random.uniform(0, 0.2)
                if sleeper is not None:
                    await sleeper(delay)
                else:
                    await asyncio.sleep(delay)
        if last_error is not None:
            raise last_error
        raise SourceUnavailable("Source request failed without a recorded error.")
