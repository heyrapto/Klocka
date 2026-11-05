from unittest.mock import AsyncMock, MagicMock

import pytest

from prediction_ai.core.config import Settings
from prediction_ai.core.exceptions import ExtractionFailedException
from prediction_ai.core.gemini_schema import build_gemini_response_schema
from prediction_ai.schemas.analysis import ScreenshotMarketCandidate
from prediction_ai.vision.gemini import GeminiVisionExtractor


def test_gemini_response_schema_contains_only_provider_safe_keys():
    schema = build_gemini_response_schema(ScreenshotMarketCandidate)
    serialized = str(schema)
    assert "additionalProperties" not in serialized
    assert "additional_properties" not in serialized
    assert "$ref" not in serialized
    assert "$defs" not in serialized


@pytest.mark.asyncio
async def test_gemini_vision_extractor_no_key_strictly_fails():
    settings = Settings(
        VISION_PROVIDER="gemini",
        GEMINI_API_KEY=None,
    )
    extractor = GeminiVisionExtractor(settings)
    with pytest.raises(ExtractionFailedException) as exc_info:
        await extractor.extract(
            b"fake_image_bytes",
            "image/png",
            hint="https://polymarket.com/market/will-btc-hit-100k",
        )
    assert "GEMINI_API_KEY is not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_vision_extractor_no_key_raises_without_hint():
    settings = Settings(
        VISION_PROVIDER="gemini",
        GEMINI_API_KEY=None,
    )
    extractor = GeminiVisionExtractor(settings)
    with pytest.raises(ExtractionFailedException):
        await extractor.extract(b"fake_image_bytes", "image/png", hint=None)


@pytest.mark.asyncio
async def test_gemini_vision_extractor_with_mocked_client():
    settings = Settings(
        VISION_PROVIDER="gemini",
        GEMINI_API_KEY="test_key",  # type: ignore[arg-type]
    )
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = (
        '{"platform": "polymarket", "market_title": "Will Solana reach $300?", '
            '"category": "crypto", "target_price": 300, "comparison_operator": ">=", '
            '"displayed_underlying_price": 285.5, "countdown_text": "10:17", '
            '"current_round_label": "9:15 AM", "category": "crypto", '
            '"outcomes": [{"label": "Yes", "displayed_price": 0.55, "text_confidence": 0.95}], '
        '"overall_confidence": 0.92}'
    )
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    extractor = GeminiVisionExtractor(settings, client=mock_client)

    # Simple 100x100 white PNG image
    import io

    from PIL import Image

    img = Image.new("RGB", (100, 100), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    candidate = await extractor.extract(buf.getvalue(), "image/png")
    assert candidate.platform == "polymarket"
    assert candidate.market_title == "Will Solana reach $300?"
    assert candidate.target_price == 300
    assert candidate.displayed_underlying_price == 285.5
    assert candidate.countdown_text == "10:17"
    assert candidate.current_round_label == "9:15 AM"
    assert candidate.overall_confidence == 0.92
