import io
import json
from typing import Any

import anyio
import structlog
from google import genai
from google.genai import errors, types
from PIL import Image
from pydantic import ValidationError

from prediction_ai.core.config import Settings
from prediction_ai.core.exceptions import (
    ExtractionFailedException,
    UpstreamProviderException,
)
from prediction_ai.core.gemini_schema import build_gemini_response_schema
from prediction_ai.schemas.analysis import ScreenshotMarketCandidate
from prediction_ai.vision.base import VisionExtractor
from prediction_ai.vision.prompts import VISION_SYSTEM_PROMPT, build_user_prompt

logger = structlog.get_logger(__name__)

#   title                  – verbose descriptions inflate the schema payload.
def _build_gemini_response_schema() -> dict[str, Any]:
    return build_gemini_response_schema(ScreenshotMarketCandidate)


# ---------------------------------------------------------------------------
# Image preparation helpers
# ---------------------------------------------------------------------------

def _prepare_image_bytes(image_bytes: bytes, max_edge: int) -> tuple[bytes, str]:
    """Normalize screenshot dimensions and re-encode to JPEG for vision model."""
    image = Image.open(io.BytesIO(image_bytes))
    image.load()
    image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    final_image: Image.Image
    if image.mode != "RGB":
        if "A" in image.getbands():
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.getchannel("A"))
            final_image = background
        else:
            final_image = image.convert("RGB")
    else:
        final_image = image
    output = io.BytesIO()
    final_image.save(output, format="JPEG", quality=90, optimize=True)
    return output.getvalue(), "image/jpeg"


def _clean_json_text(text: str) -> str:
    """Strip markdown code-fence blocks if the model wraps its output."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


# ---------------------------------------------------------------------------
# Post-extraction guard
# ---------------------------------------------------------------------------

def _assert_extraction_quality(candidate: ScreenshotMarketCandidate) -> None:
    """Raise ExtractionFailedException when mandatory market fields are missing.

    Gemini must return at minimum:
    - A non-empty market title or asset identifier.
    - At least one outcome with a price or probability present.

    If neither is satisfied the response is rejected early so the pipeline
    never proceeds with an empty candidate — the caller must surface this
    as a retriable extraction error rather than silently producing a bad
    forecast.
    """
    if not (candidate.market_title or candidate.asset_or_entity):
        raise ExtractionFailedException(
            "Gemini extraction incomplete: no market title or asset identifier found in the screenshot. "
            "Ensure the screenshot shows a prediction market contract with a visible question or asset label."
        )

    prices_found = any(
        o.displayed_price is not None or o.displayed_probability is not None
        for o in candidate.outcomes
    )
    if not candidate.outcomes:
        raise ExtractionFailedException(
            "Gemini extraction incomplete: no outcome labels or prices were read from the screenshot. "
            "The screenshot must show at least one tradeable outcome (e.g. 'Buy Up ₦58' / 'Buy Down ₦45')."
        )
    if not prices_found:
        raise ExtractionFailedException(
            f"Gemini extraction incomplete: {len(candidate.outcomes)} outcome(s) found but none carry "
            "a price or probability. Verify that the screenshot shows executable token prices "
            "(e.g. 'Buy Up ₦58' on Bayse, or a YES/NO price on Polymarket)."
        )


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class GeminiVisionExtractor(VisionExtractor):
    """Google Gemini vision-language extractor with structured Pydantic schema decoding."""

    def __init__(self, settings: Settings, client: genai.Client | None = None) -> None:
        self._settings = settings
        api_key = settings.GEMINI_API_KEY.get_secret_value() if settings.GEMINI_API_KEY else None
        self._client = client or (genai.Client(api_key=api_key) if api_key else None)
        self._model = settings.GEMINI_VISION_MODEL or settings.GEMINI_MODEL

    async def extract(
        self,
        image: bytes,
        mime_type: str,
        *,
        hint: str | None = None,
    ) -> ScreenshotMarketCandidate:
        del mime_type

        if self._client is None:
            raise ExtractionFailedException(
                "GEMINI_API_KEY is not configured. An official Google Gemini API key is "
                "required to extract prediction market contracts from screenshots."
            )

        jpeg_bytes, media_type = await anyio.to_thread.run_sync(
            _prepare_image_bytes, image, self._settings.VISION_MAX_IMAGE_EDGE
        )

        image_part = types.Part.from_bytes(data=jpeg_bytes, mime_type=media_type)
        text_part = types.Part.from_text(text=build_user_prompt(hint))
        content = types.Content(parts=[image_part, text_part])

        config = types.GenerateContentConfig(
            system_instruction=VISION_SYSTEM_PROMPT,
            temperature=0.0,
            response_mime_type="application/json",
            # Use a pre-cleaned, constraint-stripped schema dict.
            # Sending the Pydantic class directly causes the SDK to serialise
            # the full JSON Schema verbatim, including additionalProperties,
            # $defs/$ref, min/max bounds, and date-time format — all of which
            # either cause a 400 INVALID_ARGUMENT or a "too many states" error
            # in Gemini's constrained-decoding FSM.
            response_schema=_build_gemini_response_schema(),
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=content,
                config=config,
            )
        except errors.APIError as err:
            logger.error("Gemini API call failed", status_code=err.code, message=str(err))
            if err.code in (401, 403):
                raise ExtractionFailedException(
                    "Gemini API authentication failed. Verify GEMINI_API_KEY."
                ) from err
            if err.code == 429:
                raise UpstreamProviderException(
                    "Gemini API rate limit exceeded.",
                    headers={"Retry-After": "30"},
                ) from err
            if err.code and err.code >= 500:
                raise UpstreamProviderException(
                    "Gemini upstream service is temporarily unavailable."
                ) from err
            raise ExtractionFailedException(f"Gemini extraction failed: {err.message}") from err
        except Exception as err:
            logger.exception("Unexpected error communicating with Gemini vision model")
            raise UpstreamProviderException("Failed to communicate with vision model.") from err

        raw_text = response.text
        if not raw_text or not raw_text.strip():
            raise ExtractionFailedException("Gemini returned an empty extraction response.")

        clean_text = _clean_json_text(raw_text)
        try:
            data: dict[str, Any] = json.loads(clean_text)
            candidate = ScreenshotMarketCandidate.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as err:
            logger.warning("Gemini output failed schema validation", error=str(err))
            raise ExtractionFailedException(
                "Gemini output could not be validated against the market candidate schema."
            ) from err

        # --- mandatory quality gate -------------------------------------------
        # Raises ExtractionFailedException if title/outcomes/prices are absent.
        _assert_extraction_quality(candidate)
        # ----------------------------------------------------------------------

        candidate.extraction_source = "vision"
        if hint:
            candidate.extraction_warnings.append(
                "A user-supplied market reference was provided and verified."
            )

        logger.info(
            "Gemini extraction succeeded",
            platform=candidate.platform,
            title=candidate.market_title,
            outcomes=len(candidate.outcomes),
            prices=[str(o.displayed_price) for o in candidate.outcomes],
        )
        return candidate
