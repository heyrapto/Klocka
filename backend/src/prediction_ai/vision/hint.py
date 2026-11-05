import uuid
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from prediction_ai.core.exceptions import ValidationException
from prediction_ai.schemas.analysis import ScreenshotMarketCandidate

_POLYMARKET_HOSTS = {"polymarket.com", "www.polymarket.com"}
_BAYSE_HOSTS = {"bayse.markets", "www.bayse.markets", "relay.bayse.markets"}


@dataclass(frozen=True, slots=True)
class HintReference:
    platform: str | None
    url: str | None
    slug: str | None
    market_id: str | None
    text: str | None

    @property
    def is_platform_url(self) -> bool:
        return self.platform is not None


def parse_hint(hint: str | None) -> HintReference:
    if hint is None:
        return HintReference(None, None, None, None, None)
    value = hint.strip()
    if not value:
        return HintReference(None, None, None, None, None)

    parsed = urlparse(value)
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"}:
            raise ValidationException("Only http(s) market links are accepted as a hint.")
        host = (parsed.hostname or "").lower()
        if host not in _POLYMARKET_HOSTS | _BAYSE_HOSTS:
            raise ValidationException(
                "Market hint URLs must point to polymarket.com or bayse.markets."
            )
        platform = "polymarket" if host in _POLYMARKET_HOSTS else "bayse"
        segments = [unquote(part) for part in parsed.path.split("/") if part]
        slug: str | None = None
        market_id: str | None = None
        for index, segment in enumerate(segments):
            if segment in {"market", "event"} and index + 1 < len(segments):
                slug = segments[index + 1]
                break
        if slug is None and segments:
            slug = segments[-1]
        if slug:
            try:
                uuid.UUID(slug)
                market_id = slug
            except ValueError:
                pass
        return HintReference(platform=platform, url=value, slug=slug, market_id=market_id, text=None)

    return HintReference(platform=None, url=None, slug=None, market_id=None, text=value[:500])


def build_hint_candidate(hint: str | None) -> ScreenshotMarketCandidate | None:
    reference = parse_hint(hint)
    if hint is None or not hint.strip():
        return None
    if reference.is_platform_url:
        return ScreenshotMarketCandidate(
            platform=reference.platform or "unknown",  # type: ignore[arg-type]
            market_url=reference.url,
            market_slug=reference.slug,
            market_id=reference.market_id,
            market_title=None,
            extraction_source="hint",
            overall_confidence=0.6,
        )
    return ScreenshotMarketCandidate(
        platform="unknown",
        market_title=reference.text,
        extraction_source="hint",
        overall_confidence=0.4,
    )
