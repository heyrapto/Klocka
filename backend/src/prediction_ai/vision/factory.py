import httpx
from google import genai

from prediction_ai.core.config import Settings
from prediction_ai.core.exceptions import ExtractionFailedException
from prediction_ai.schemas.analysis import ScreenshotMarketCandidate
from prediction_ai.vision.base import VisionExtractor
from prediction_ai.vision.gemini import GeminiVisionExtractor
from prediction_ai.vision.hint import build_hint_candidate
from prediction_ai.vision.openai import OpenAIVisionExtractor


class DisabledVisionExtractor(VisionExtractor):
    """Fallback extractor when vision processing is disabled or mocking."""

    async def extract(
        self,
        image: bytes,
        mime_type: str,
        *,
        hint: str | None = None,
    ) -> ScreenshotMarketCandidate:
        del image, mime_type
        if hint:
            candidate = build_hint_candidate(hint)
            if candidate is not None:
                candidate.extraction_warnings.append("Vision processing is disabled; hint used.")
                return candidate
        raise ExtractionFailedException(
            "Vision extraction is disabled and no valid market hint was provided."
        )


def create_vision_extractor(
    settings: Settings,
    http_client: httpx.AsyncClient | None = None,
    gemini_client: genai.Client | None = None,
) -> VisionExtractor:
    """Instantiate the configured vision extractor implementation."""
    if settings.VISION_PROVIDER == "gemini":
        return GeminiVisionExtractor(settings, client=gemini_client)
    elif settings.VISION_PROVIDER == "openai":
        client = http_client or httpx.AsyncClient()
        return OpenAIVisionExtractor(settings, client=client)
    return DisabledVisionExtractor()
