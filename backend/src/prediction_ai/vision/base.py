from typing import Protocol

from prediction_ai.schemas.analysis import ScreenshotMarketCandidate


class VisionExtractor(Protocol):
    """Converts a private screenshot into a strictly validated market candidate."""

    async def extract(
        self,
        image: bytes,
        mime_type: str,
        *,
        hint: str | None = None,
    ) -> ScreenshotMarketCandidate: ...
