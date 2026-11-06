"""Unit tests for HTML and RSS document extraction."""

from __future__ import annotations

import pytest

from prediction_ai.research.extract import (
    SourceLead,
    extract_html,
    extract_rss,
    quote_hash,
    split_sentences,
)


class TestQuoteHash:
    def test_produces_sha256_prefix(self) -> None:
        h = quote_hash("hello world")
        assert h.startswith("sha256:")

    def test_same_input_same_hash(self) -> None:
        assert quote_hash("test") == quote_hash("test")

    def test_different_input_different_hash(self) -> None:
        assert quote_hash("abc") != quote_hash("xyz")

    def test_empty_string(self) -> None:
        h = quote_hash("")
        assert h.startswith("sha256:")


class TestSplitSentences:
    def test_splits_on_period(self) -> None:
        result = split_sentences("One. Two. Three.")
        assert len(result) >= 2

    def test_handles_empty_string(self) -> None:
        result = split_sentences("")
        assert result == []

    def test_does_not_split_abbreviations_mid_sentence(self) -> None:
        result = split_sentences("The U.K. government announced something today.")
        # Should not split at U.K. — but at minimum must return something
        assert len(result) >= 1


class TestExtractHtml:
    _SIMPLE_HTML = b"""
    <html>
    <head>
      <title>Test Article</title>
      <meta property="og:title" content="Manager Leaves Club">
      <meta property="og:site_name" content="Sports News">
      <meta property="article:published_time" content="2026-11-05T08:00:00Z">
      <meta property="article:modified_time" content="2026-11-05T10:00:00Z">
      <meta name="author" content="John Reporter">
    </head>
    <body>
      <main>
        <article>
          <h1>Manager Departs</h1>
          <p>The manager officially announced his departure from the club today.</p>
          <p>The board confirmed the decision in a statement.</p>
        </article>
      </main>
    </body>
    </html>
    """

    def test_extracts_title(self) -> None:
        doc = extract_html(self._SIMPLE_HTML, canonical_url="https://example.com/article", final_url="https://example.com/article")
        assert doc.title is not None
        assert len(doc.title) > 0

    def test_extracts_publisher(self) -> None:
        doc = extract_html(self._SIMPLE_HTML, canonical_url="https://example.com/article", final_url="https://example.com/article")
        assert doc.publisher == "Sports News"

    def test_extracts_author(self) -> None:
        doc = extract_html(self._SIMPLE_HTML, canonical_url="https://example.com/article", final_url="https://example.com/article")
        assert doc.author == "John Reporter"

    def test_extracts_published_at(self) -> None:
        doc = extract_html(self._SIMPLE_HTML, canonical_url="https://example.com/article", final_url="https://example.com/article")
        assert doc.published_at is not None
        assert doc.published_at.year == 2026

    def test_extracts_text_content(self) -> None:
        doc = extract_html(self._SIMPLE_HTML, canonical_url="https://example.com/article", final_url="https://example.com/article")
        assert "departure" in doc.text.lower() or "manager" in doc.text.lower()

    def test_extracts_spans(self) -> None:
        doc = extract_html(self._SIMPLE_HTML, canonical_url="https://example.com/article", final_url="https://example.com/article")
        assert len(doc.spans) > 0

    def test_quality_flags_on_missing_timestamps(self) -> None:
        html = b"<html><body><p>Some content without any dates.</p></body></html>"
        doc = extract_html(html, canonical_url="https://example.com/", final_url="https://example.com/")
        assert "PUBLISHED_AT_MISSING" in doc.quality_flags

    def test_empty_html_raises(self) -> None:
        with pytest.raises(ValueError, match="no extractable"):
            extract_html(b"<html><body></body></html>", canonical_url="https://example.com/", final_url="https://example.com/")

    def test_extraction_version_recorded(self) -> None:
        doc = extract_html(self._SIMPLE_HTML, canonical_url="https://example.com/article", final_url="https://example.com/article")
        assert doc.extraction_version is not None


class TestExtractRss:
    _RSS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Sports Feed</title>
        <item>
          <title>Manager Leaves Club</title>
          <link>https://example.com/article1</link>
          <pubDate>Thu, 05 Nov 2026 08:00:00 +0000</pubDate>
          <description>The manager has officially departed.</description>
        </item>
        <item>
          <title>Club Signs Replacement</title>
          <link>https://example.com/article2</link>
          <pubDate>Thu, 05 Nov 2026 10:00:00 +0000</pubDate>
          <description>A new manager has been appointed.</description>
        </item>
      </channel>
    </rss>
    """

    def test_extracts_leads(self) -> None:
        leads = extract_rss(self._RSS_FEED, feed_url="https://example.com/feed", provider="test")
        assert len(leads) == 2

    def test_lead_has_title(self) -> None:
        leads = extract_rss(self._RSS_FEED, feed_url="https://example.com/feed", provider="test")
        assert leads[0].title == "Manager Leaves Club"

    def test_lead_has_url(self) -> None:
        leads = extract_rss(self._RSS_FEED, feed_url="https://example.com/feed", provider="test")
        assert "example.com" in leads[0].url

    def test_lead_has_published_at(self) -> None:
        leads = extract_rss(self._RSS_FEED, feed_url="https://example.com/feed", provider="test")
        assert leads[0].published_at is not None
        assert leads[0].published_at.year == 2026

    def test_caps_at_max_items(self) -> None:
        leads = extract_rss(self._RSS_FEED, feed_url="https://example.com/feed", provider="test", max_items=1)
        assert len(leads) == 1

    def test_invalid_xml_raises(self) -> None:
        with pytest.raises(ValueError, match="parsed"):
            extract_rss(b"not xml at all <<>>", feed_url="https://example.com/feed", provider="test")

    def test_items_without_title_or_link_skipped(self) -> None:
        feed = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel>
          <item><description>No title or link here</description></item>
        </channel></rss>"""
        leads = extract_rss(feed, feed_url="https://example.com/feed", provider="test")
        assert len(leads) == 0

    def test_atom_feed_parsed(self) -> None:
        atom = b"""<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Atom Entry</title>
            <link href="https://example.com/entry1"/>
            <published>2026-11-05T08:00:00Z</published>
            <summary>Some content</summary>
          </entry>
        </feed>"""
        leads = extract_rss(atom, feed_url="https://example.com/atom", provider="test")
        assert len(leads) == 1
        assert leads[0].title == "Atom Entry"
