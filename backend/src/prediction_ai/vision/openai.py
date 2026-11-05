import base64
import io
import json
from typing import Any

import anyio
import httpx
import structlog
from PIL import Image
from pydantic import ValidationError

from prediction_ai.core.config import Settings
from prediction_ai.core.exceptions import (
    ExtractionFailedException,
    ProviderTimeoutException,
    UpstreamProviderException,
)
from prediction_ai.schemas.analysis import ScreenshotMarketCandidate
from prediction_ai.vision.prompts import VISION_SYSTEM_PROMPT, build_user_prompt

logger = structlog.get_logger(__name__)


def _strict_schema(node: Any) -> Any:
    """Make a Pydantic JSON schema acceptable to strict structured-output APIs."""
    if isinstance(node, dict):
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["required"] = list(properties.keys())
            node["additionalProperties"] = False
        for value in list(node.values()):
            _strict_schema(value)
        node.pop("default", None)
    elif isinstance(node, list):
        for item in node:
            _strict_schema(item)
    return node


def _prepare_image(image_bytes: bytes, max_edge: int) -> tuple[str, str]:
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
    return base64.b64encode(output.getvalue()).decode("ascii"), "image/jpeg"


class OpenAIVisionExtractor:
    """OpenAI-compatible vision-language extractor with strict output parsing."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        if settings.VISION_API_KEY is None:
            raise ValueError("VISION_API_KEY is required for the OpenAI vision provider.")
        self._settings = settings
        self._client = client
        self._endpoint = f"{settings.VISION_API_BASE_URL.rstrip('/')}/chat/completions"
        self._schema = _strict_schema(ScreenshotMarketCandidate.model_json_schema())
        self._api_key = settings.VISION_API_KEY.get_secret_value()

    async def extract(
        self,
        image: bytes,
        mime_type: str,
        *,
        hint: str | None = None,
    ) -> ScreenshotMarketCandidate:
        del mime_type
        encoded, media_type = await anyio.to_thread.run_sync(
            _prepare_image, image, self._settings.VISION_MAX_IMAGE_EDGE
        )
        payload: dict[str, Any] = {
            "model": self._settings.VISION_MODEL,
            "temperature": 0.0,
            "max_tokens": self._settings.VISION_MAX_OUTPUT_TOKENS,
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": build_user_prompt(hint)},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                        },
                    ],
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "screenshot_market_candidate",
                    "strict": True,
                    "schema": self._schema,
                },
            },
        }

        response = await self._post(payload)
        if response.status_code == 400 and _is_response_format_error(response):
            logger.info("Vision provider rejected json_schema mode; retrying in JSON mode")
            payload.pop("response_format")
            payload["messages"][0]["content"] = (
                VISION_SYSTEM_PROMPT + "\nReturn one JSON object only, with no markdown fences."
            )
            response = await self._post(payload)

        if response.status_code == 401 or response.status_code == 403:
            raise ExtractionFailedException(
                "Vision provider credentials were rejected. Check VISION_API_KEY."
            )
        if response.status_code == 429:
            raise UpstreamProviderException(
                "Vision provider rate limit reached.",
                headers={"Retry-After": response.headers.get("Retry-After", "30")},
            )
        if response.status_code >= 500:
            raise UpstreamProviderException("Vision provider is temporarily unavailable.")
        if response.status_code >= 400:
            raise ExtractionFailedException("Vision provider rejected the extraction request.")

        content = _extract_content(response.json())
        if not content:
            raise ExtractionFailedException("Vision provider returned an empty extraction.")
        try:
            raw = json.loads(_strip_code_fence(content))
        except json.JSONDecodeError as err:
            raise ExtractionFailedException(
                "Vision provider returned malformed structured output."
            ) from err

        try:
            candidate = ScreenshotMarketCandidate.model_validate(raw)
        except ValidationError as err:
            logger.warning("Vision output failed schema validation", error_count=err.error_count())
            raise ExtractionFailedException(
                "Vision output did not match the required extraction schema."
            ) from err

        candidate.extraction_source = "vision"
        if hint:
            candidate.extraction_warnings.append(
                "A user-supplied market reference was provided and is checked separately."
            )
        return candidate

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            response = await self._client.post(
                self._endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._settings.VISION_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException as err:
            raise ProviderTimeoutException("Vision provider timed out.") from err
        except httpx.HTTPError as err:
            raise UpstreamProviderException("Vision provider request failed.") from err
        return response


def _is_response_format_error(response: httpx.Response) -> bool:
    try:
        body = response.text.lower()
    except Exception:
        return False
    return "response_format" in body or "json_schema" in body


def _extract_content(body: dict[str, Any]) -> str | None:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(parts) if parts else None
    return None


def _strip_code_fence(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped
