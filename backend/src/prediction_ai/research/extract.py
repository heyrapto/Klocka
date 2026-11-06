"""Bounded, deterministic text extraction from approved HTML and RSS sources.

Only text and locators are returned. Scripts, styles, forms, and embedded active
content are discarded. The extractor never executes page content and never
fetches subresources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree

from prediction_ai.research.models import ExtractedDocument, SourceLead, TextSpan
from prediction_ai.research.time import parse_timestamp

_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "template", "iframe", "object", "embed", "form"}
)
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "article",
        "section",
        "main",
        "aside",
        "li",
        "ul",
        "ol",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "td",
        "th",
        "tr",
        "figcaption",
        "summary",
        "details",
        "br",
        "hr",
    }
)
_WHITESPACE = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def normalize_text(value: str) -> str:
    return _WHITESPACE.sub(" ", unescape(value)).strip()


def content_hash(text: str) -> str:
    return f"sha256:{sha256(normalize_text(text).encode('utf-8')).hexdigest()}"


def quote_hash(text: str) -> str:
    return content_hash(text)


def split_sentences(paragraph: str, *, max_sentences: int = 12) -> list[str]:
    sentences = [item.strip() for item in _SENTENCE.split(paragraph) if item.strip()]
    return sentences[:max_sentences]


@dataclass(slots=True)
class _Paragraph:
    text: str
    char_start: int
    char_end: int


class _MainContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._buffer: list[str] = []
        self._paragraphs: list[_Paragraph] = []
        self._cursor = 0
        self._title_parts: list[str] = []
        self._in_title = False
        self._in_h1 = False
        self._h1_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.time_datetime: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attr_map = {key.lower(): (value or "") for key, value in attrs}
        if lowered == "meta":
            name = (attr_map.get("property") or attr_map.get("name") or "").lower()
            content = attr_map.get("content", "").strip()
            if name and content:
                self.meta[name] = content
        elif lowered == "title":
            self._in_title = True
        elif lowered == "h1":
            self._in_h1 = True
        elif lowered == "time" and self.time_datetime is None:
            self.time_datetime = attr_map.get("datetime") or None
        if lowered in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if lowered == "title":
            self._in_title = False
        elif lowered == "h1":
            self._in_h1 = False
        if lowered in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._in_h1:
            self._h1_parts.append(data)
        self._buffer.append(data)

    def _flush(self) -> None:
        text = normalize_text("".join(self._buffer))
        self._buffer = []
        if len(text) < 2:
            return
        start = self._cursor
        self._paragraphs.append(_Paragraph(text=text, char_start=start, char_end=start + len(text)))
        self._cursor = start + len(text) + 1

    def close(self) -> None:
        super().close()
        self._flush()

    @property
    def paragraphs(self) -> list[_Paragraph]:
        return self._paragraphs

    @property
    def title(self) -> str:
        return normalize_text("".join(self._title_parts)) or normalize_text("".join(self._h1_parts))

    @property
    def h1(self) -> str:
        return normalize_text("".join(self._h1_parts))


def _decode(raw: bytes, content_type: str) -> str:
    charset = "utf-8"
    match = re.search(r"charset=([\w\-]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1)
    else:
        head = raw[:2048].decode("ascii", errors="ignore")
        meta = re.search(r"<meta[^>]+charset=[\"']?([\w\-]+)", head, re.IGNORECASE)
        if meta:
            charset = meta.group(1)
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def extract_html(
    raw: bytes,
    *,
    canonical_url: str,
    final_url: str,
    content_type: str = "text/html",
    max_chars: int = 200_000,
    extraction_version: str = "html-extractor-1.0",
) -> ExtractedDocument:
    html = _decode(raw, content_type)
    parser = _MainContentParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as err:  # HTMLParser is forgiving; a failure is a data-quality signal.
        raise ValueError("HTML could not be parsed.") from err

    paragraphs = [
        paragraph
        for paragraph in parser.paragraphs
        if len(paragraph.text) >= 20 and not paragraph.text.lower().startswith(("cookie", "subscribe"))
    ]
    # Deduplicate repeated boilerplate while keeping the first occurrence.
    seen: set[str] = set()
    unique: list[_Paragraph] = []
    for paragraph in paragraphs:
        fingerprint = sha256(paragraph.text.lower().encode("utf-8")).hexdigest()
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(paragraph)

    text_parts: list[str] = []
    spans: list[TextSpan] = []
    cursor = 0
    for index, paragraph in enumerate(unique):
        if cursor + len(paragraph.text) > max_chars:
            break
        text_parts.append(paragraph.text)
        spans.append(
            TextSpan(
                kind="html_paragraph",
                locator=f"paragraph:{index}",
                text=paragraph.text[:4000],
                char_start=cursor,
                char_end=cursor + len(paragraph.text),
                quote_hash=quote_hash(paragraph.text),
            )
        )
        cursor += len(paragraph.text) + 1

    text = "\n".join(text_parts).strip()
    if not text:
        raise ValueError("HTML contained no extractable main-content text.")

    meta = parser.meta
    title = (
        meta.get("og:title")
        or parser.title
        or parser.h1
        or final_url.rsplit("/", 1)[-1]
    )
    publisher = meta.get("og:site_name") or meta.get("application-name")
    author = meta.get("author") or meta.get("article:author")
    published_raw = (
        meta.get("article:published_time")
        or meta.get("datepublished")
        or meta.get("pubdate")
        or parser.time_datetime
    )
    updated_raw = meta.get("article:modified_time") or meta.get("datemodified")

    published = parse_timestamp(published_raw)
    updated = parse_timestamp(updated_raw)
    quality_flags: list[str] = []
    if published is None or published.value is None:
        quality_flags.append("PUBLISHED_AT_MISSING")
    elif not published.had_timezone:
        quality_flags.append("PUBLISHED_AT_TIMEZONE_UNCERTAIN")
    if updated is None or updated.value is None:
        quality_flags.append("SOURCE_UPDATED_AT_MISSING")

    return ExtractedDocument(
        canonical_url=canonical_url,
        final_url=final_url,
        title=normalize_text(title)[:1000] or final_url,
        publisher=normalize_text(publisher)[:255] if publisher else None,
        author=normalize_text(author)[:255] if author else None,
        published_raw=published.raw if published else None,
        source_updated_raw=updated.raw if updated else None,
        published_at=published.value if published else None,
        source_updated_at=updated.value if updated else None,
        text=text,
        spans=spans,
        content_type=content_type,
        extraction_version=extraction_version,
        quality_flags=quality_flags,
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _first_text(element: ElementTree.Element, names: tuple[str, ...]) -> str | None:
    for child in element:
        if _local_name(child.tag) in names:
            text = normalize_text("".join(child.itertext()))
            if text:
                return text
    return None


def _first_attr(element: ElementTree.Element, names: tuple[str, ...], attr: str) -> str | None:
    for child in element:
        if _local_name(child.tag) in names:
            value = child.attrib.get(attr)
            if value:
                return value.strip()
    return None


def extract_rss(
    raw: bytes,
    *,
    feed_url: str,
    provider: str,
    max_items: int = 25,
) -> list[SourceLead]:
    """Parse RSS 2.0 or Atom into bounded, unverified leads."""

    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as err:
        raise ValueError("Feed could not be parsed.") from err

    items = [
        element
        for element in root.iter()
        if _local_name(element.tag) in {"item", "entry"}
    ]
    leads: list[SourceLead] = []
    for item in items[: max(0, min(max_items, 50))]:
        title = _first_text(item, ("title",))
        link = _first_text(item, ("link",)) or _first_attr(item, ("link",), "href")
        if not title or not link:
            continue
        published_raw = _first_text(item, ("pubdate", "published", "updated", "date"))
        summary = _first_text(item, ("description", "summary", "content"))
        author = _first_text(item, ("author", "creator"))
        publisher = _first_text(item, ("source",))
        parsed = parse_timestamp(published_raw)
        leads.append(
            SourceLead(
                provider=provider,
                title=title[:1000],
                url=urljoin(feed_url, link)[:2048],
                publisher=(publisher or author or None),
                published_raw=published_raw,
                published_at=parsed.value if parsed else None,
                snippet=summary[:1000] if summary else None,
            )
        )
    return leads


def extract_feed_metadata(raw: bytes) -> dict[str, Any]:
    """Return non-authoritative feed metadata for diagnostics only."""

    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return {}
    return {
        "title": _first_text(root, ("title",)),
        "updated": _first_text(root, ("updated", "lastbuilddate")),
        "extracted_at": datetime.now(UTC).isoformat(),
    }
