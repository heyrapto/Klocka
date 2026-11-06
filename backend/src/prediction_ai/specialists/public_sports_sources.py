from __future__ import annotations

import re
from hashlib import sha256
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx
import structlog

logger = structlog.get_logger(__name__)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if value.isdigit() and len(value) == 14:
        try:
            return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _claim_id(source: str, url: str) -> str:
    """Create a stable bounded ID without storing provider URL length in the key."""
    digest = sha256(url.encode("utf-8")).hexdigest()[:32]
    return f"{source}:{digest}"


class PublicSportsSourceClient:
    """Bounded public news and social search adapters.

    Search results are evidence leads, not authoritative settlement facts.
    """

    def __init__(self, client: httpx.AsyncClient, max_results: int = 8) -> None:
        self._client = client
        self._max_results = max(1, min(max_results, 12))

    async def search(self, query: str, cutoff_at: datetime) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        results.extend(await self._search_gdelt(query, cutoff_at))
        if not results:
            results.extend(await self._search_google_news_rss(query, cutoff_at))
        results.extend(await self._search_reddit(query, cutoff_at))
        return results[: self._max_results * 2]

    async def _search_gdelt(self, query: str, cutoff_at: datetime) -> list[dict[str, Any]]:
        try:
            response = await self._client.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={
                    "query": f"{query} sports",
                    "mode": "artlist",
                    "format": "json",
                    "maxrecords": self._max_results,
                    "sort": "datedesc",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as err:
            logger.warning(
                "Public sports news search failed",
                source="gdelt",
                status_code=err.response.status_code,
                retry_after=err.response.headers.get("Retry-After"),
            )
            return []
        except (httpx.HTTPError, ValueError) as err:
            logger.warning("Public sports news search failed", source="gdelt", error=str(err))
            return []

        if not isinstance(payload, dict):
            logger.warning(
                "Public sports news search returned a non-object payload",
                source="gdelt",
                response_type=type(payload).__name__,
            )
            return []

        articles = payload.get("articles", [])
        results: list[dict[str, Any]] = []
        for article in articles[: self._max_results]:
            if not isinstance(article, dict):
                continue
            published_at = _parse_timestamp(article.get("seendate"))
            if published_at is None or published_at > cutoff_at:
                continue
            title = _clean_text(str(article.get("title") or ""))
            url = str(article.get("url") or "").strip()
            if not title or not url:
                continue
            results.append(
                {
                    "claim_id": _claim_id("gdelt", url),
                    "claim_type": "reported_sports_news",
                    "claim_text": title,
                    "stance": "unresolved",
                    "state": "unresolved",
                    "source_id": "gdelt_public_news",
                    "source_url": url,
                    "published_at": published_at.isoformat(),
                    "observed_at": published_at.isoformat(),
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "as_of_cutoff": True,
                    "authority_tier": 3,
                    "independence_group": str(article.get("domain") or "unknown-domain"),
                    "quality_flags": ["SEARCH_RESULT_REQUIRES_SOURCE_REVIEW"],
                }
            )
        return results

    async def _search_google_news_rss(
        self, query: str, cutoff_at: datetime
    ) -> list[dict[str, Any]]:
        try:
            response = await self._client.get(
                f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
            )
            response.raise_for_status()
            root = ElementTree.fromstring(response.text)
        except httpx.HTTPStatusError as err:
            logger.warning(
                "Public sports RSS search failed",
                source="google_news_rss",
                status_code=err.response.status_code,
            )
            return []
        except (httpx.HTTPError, ElementTree.ParseError) as err:
            logger.warning("Public sports RSS search failed", source="google_news_rss", error=str(err))
            return []

        results: list[dict[str, Any]] = []
        for item in root.findall("./channel/item")[: self._max_results]:
            title = _clean_text(item.findtext("title") or "")
            url = (item.findtext("link") or "").strip()
            published_at = _parse_timestamp(item.findtext("pubDate"))
            if not title or not url or published_at is None or published_at > cutoff_at:
                continue
            results.append(
                {
                    "claim_id": _claim_id("google-news", url),
                    "claim_type": "reported_sports_news",
                    "claim_text": title,
                    "stance": "unresolved",
                    "state": "unresolved",
                    "source_id": "google_news_public_rss",
                    "source_url": url,
                    "published_at": published_at.isoformat(),
                    "observed_at": published_at.isoformat(),
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "as_of_cutoff": True,
                    "authority_tier": 3,
                    "independence_group": "google-news-rss",
                    "quality_flags": ["SEARCH_RESULT_REQUIRES_SOURCE_REVIEW"],
                }
            )
        return results

    async def _search_reddit(self, query: str, cutoff_at: datetime) -> list[dict[str, Any]]:
        try:
            response = await self._client.get(
                f"https://www.reddit.com/search.rss?q={quote_plus(query)}&sort=new&limit={self._max_results}",
                headers={"User-Agent": "prediction-intelligence/0.1 public-research"},
            )
            response.raise_for_status()
            root = ElementTree.fromstring(response.text)
        except (httpx.HTTPError, ElementTree.ParseError) as err:
            logger.warning("Public social search failed", source="reddit", error=str(err))
            return []

        results: list[dict[str, Any]] = []
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", namespace)[: self._max_results]:
            title_node = entry.find("atom:title", namespace)
            link_node = entry.find("atom:link", namespace)
            updated_node = entry.find("atom:updated", namespace)
            title = _clean_text(title_node.text or "") if title_node is not None else ""
            url = link_node.attrib.get("href", "") if link_node is not None else ""
            published_at = _parse_timestamp(updated_node.text if updated_node is not None else None)
            if not title or not url or published_at is None or published_at > cutoff_at:
                continue
            results.append(
                {
                    "claim_id": _claim_id("reddit", url),
                    "claim_type": "social_sports_report",
                    "claim_text": title,
                    "stance": "unresolved",
                    "state": "unresolved",
                    "source_id": "reddit_public_search",
                    "source_url": url,
                    "published_at": published_at.isoformat(),
                    "observed_at": published_at.isoformat(),
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "as_of_cutoff": True,
                    "authority_tier": 3,
                    "independence_group": "reddit",
                    "quality_flags": ["SOCIAL_UNVERIFIED", "SEARCH_RESULT_REQUIRES_SOURCE_REVIEW"],
                }
            )
        return results