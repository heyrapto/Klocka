from datetime import UTC, datetime

import httpx
import pytest

from prediction_ai.specialists.public_sports_sources import PublicSportsSourceClient


@pytest.mark.asyncio
async def test_public_sources_keep_only_as_of_results():
    async def handler(request: httpx.Request) -> httpx.Response:
        if "gdeltproject" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "articles": [
                        {
                            "title": "Carrick future reported by football desk",
                            "url": "https://example.com/carrick",
                            "seendate": "20261105100000",
                            "domain": "example.com",
                        },
                        {
                            "title": "Future report after cutoff",
                            "url": "https://example.com/future",
                            "seendate": "20261105120000",
                            "domain": "example.com",
                        },
                    ]
                },
            )
        return httpx.Response(
            200,
            content=b"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>"
            b"<entry><title>Social discussion about Carrick</title>"
            b"<link href='https://reddit.com/r/football/post'/><updated>2026-11-05T09:30:00Z</updated>"
            b"</entry></feed>",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        results = await PublicSportsSourceClient(client).search(
            "Michael Carrick Manchester United",
            datetime(2026, 11, 5, 11, 0, tzinfo=UTC),
        )
    finally:
        await client.aclose()

    assert len(results) == 2
    assert {result["source_id"] for result in results} == {
        "gdelt_public_news",
        "reddit_public_search",
    }
    assert all(result["as_of_cutoff"] is True for result in results)


@pytest.mark.asyncio
async def test_public_source_claim_ids_are_bounded_for_long_urls():
    long_url = "https://news.google.com/articles/" + ("token-" * 80)

    async def handler(request: httpx.Request) -> httpx.Response:
        if "gdeltproject" in str(request.url):
            return httpx.Response(200, json="provider rate limited")
        if "news.google.com" in str(request.url):
            return httpx.Response(
                200,
                content=(
                    "<rss><channel><item><title>Long URL report</title>"
                    f"<link>{long_url}</link>"
                    "<pubDate>Wed, 05 Nov 2026 10:00:00 GMT</pubDate>"
                    "</item></channel></rss>"
                ).encode(),
            )
        return httpx.Response(200, content=b"<feed xmlns='http://www.w3.org/2005/Atom'/>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        results = await PublicSportsSourceClient(client).search(
            "Carrick", datetime(2026, 11, 5, 11, 0, tzinfo=UTC)
        )
    finally:
        await client.aclose()

    google_result = next(result for result in results if result["source_id"] == "google_news_public_rss")
    assert len(google_result["claim_id"]) <= 128
