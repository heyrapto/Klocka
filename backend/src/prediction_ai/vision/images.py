import hashlib
import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from prediction_ai.core.config import Settings
from prediction_ai.core.exceptions import (
    PayloadTooLargeException,
    UnsupportedMediaTypeException,
    ValidationException,
)

_ALLOWED_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
_ALLOWED_MIMES = frozenset(_ALLOWED_FORMATS.values())
_NORMALIZED_MIME = "image/webp"


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    sha256: str


def _open_image(data: bytes, settings: Settings) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as err:
        raise UnsupportedMediaTypeException(
            "The uploaded file is not a decodable PNG, JPEG, or WebP image."
        ) from err

    if image.format not in _ALLOWED_FORMATS:
        raise UnsupportedMediaTypeException(
            "Unsupported image format. Upload a PNG, JPEG, or WebP screenshot."
        )

    frames = getattr(image, "n_frames", 1)
    if frames != 1:
        raise UnsupportedMediaTypeException("Animated images are not accepted.")

    width, height = image.size
    if width < 16 or height < 16:
        raise ValidationException("The screenshot is too small to analyze.")
    if width > settings.MAX_IMAGE_EDGE or height > settings.MAX_IMAGE_EDGE:
        raise ValidationException(
            f"Image dimensions exceed the {settings.MAX_IMAGE_EDGE}px limit."
        )
    if width * height > settings.MAX_IMAGE_PIXELS:
        raise ValidationException("Image contains too many pixels to process safely.")
    return image


def inspect_image(data: bytes, settings: Settings) -> tuple[str, int, int]:
    """Validate bytes without rewriting them."""
    image = _open_image(data, settings)
    mime_type = _ALLOWED_FORMATS[image.format or ""]
    return mime_type, image.width, image.height


def validate_and_normalize_image(data: bytes, settings: Settings) -> ValidatedImage:
    """Validate an untrusted upload, strip metadata, and re-encode it safely."""
    if not data:
        raise ValidationException("The uploaded file is empty.")
    if len(data) > settings.MAX_UPLOAD_BYTES:
        raise PayloadTooLargeException(
            f"Screenshot exceeds the {settings.MAX_UPLOAD_BYTES} byte limit."
        )

    image = _open_image(data, settings)
    # Re-encode the decoded pixels so EXIF, ICC payloads, and appended data are dropped.
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    image.thumbnail(
        (settings.MAX_IMAGE_EDGE, settings.MAX_IMAGE_EDGE),
        Image.Resampling.LANCZOS,
    )

    output = io.BytesIO()
    image.save(output, format="WEBP", quality=92, method=6)
    normalized = output.getvalue()

    return ValidatedImage(
        data=normalized,
        mime_type=_NORMALIZED_MIME,
        width=image.width,
        height=image.height,
        sha256=hashlib.sha256(data).hexdigest(),
    )
