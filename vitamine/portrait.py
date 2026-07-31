"""Safe normalization helpers for database-backed researcher portraits."""

from __future__ import annotations

import io
import math
import re
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_PORTRAIT_BYTES = 5 * 1024 * 1024
MAX_PORTRAIT_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_PORTRAIT_SOURCE_DIMENSION = 12_000
MAX_PORTRAIT_SOURCE_PIXELS = 50_000_000
MAX_PORTRAIT_OUTPUT_DIMENSION = 2_000


@dataclass(frozen=True)
class PortraitMetadata:
    mime_type: str
    filename: str
    width: int
    height: int


@dataclass(frozen=True)
class NormalizedPortrait:
    data: bytes
    metadata: PortraitMetadata


def _safe_png_filename(value: str) -> str:
    name = Path(str(value or "")).name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]+", "", name)
    stem = Path(name).stem.strip()[:230]
    return f"{stem}.png" if stem else "portrait.png"


def _normalized_mode(image: Image.Image) -> Image.Image:
    has_alpha = image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    return image.convert("RGBA" if has_alpha else "RGB")


def _encode_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True, compress_level=9)
    return output.getvalue()


def normalize_portrait_image(
    data: bytes,
    *,
    filename: str = "",
) -> NormalizedPortrait:
    if not data:
        raise ValueError("Choose a JPEG or PNG portrait.")
    if len(data) > MAX_PORTRAIT_UPLOAD_BYTES:
        raise ValueError("The original portrait must be 25 MB or smaller.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"JPEG", "PNG"}:
                    raise ValueError("Choose a valid JPEG or PNG image.")
                width, height = source.size
                if width <= 0 or height <= 0:
                    raise ValueError("The portrait has invalid dimensions.")
                if (
                    width > MAX_PORTRAIT_SOURCE_DIMENSION
                    or height > MAX_PORTRAIT_SOURCE_DIMENSION
                    or width * height > MAX_PORTRAIT_SOURCE_PIXELS
                ):
                    raise ValueError(
                        "The portrait is too large; use an image below 50 megapixels."
                    )
                source.load()
                image = _normalized_mode(ImageOps.exif_transpose(source))
    except ValueError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
    ) as exc:
        raise ValueError("Choose a valid JPEG or PNG image.") from exc

    image.thumbnail(
        (MAX_PORTRAIT_OUTPUT_DIMENSION, MAX_PORTRAIT_OUTPUT_DIMENSION),
        Image.Resampling.LANCZOS,
    )
    encoded = _encode_png(image)
    while len(encoded) > MAX_PORTRAIT_BYTES:
        scale = min(
            0.9,
            max(0.5, math.sqrt(MAX_PORTRAIT_BYTES / len(encoded)) * 0.95),
        )
        resized = (
            max(1, int(image.width * scale)),
            max(1, int(image.height * scale)),
        )
        if resized == image.size:
            raise ValueError("The portrait could not be reduced below 5 MB.")
        image = image.resize(resized, Image.Resampling.LANCZOS)
        encoded = _encode_png(image)

    metadata = PortraitMetadata(
        mime_type="image/png",
        filename=_safe_png_filename(filename),
        width=image.width,
        height=image.height,
    )
    return NormalizedPortrait(data=encoded, metadata=metadata)


def validate_portrait_image(data: bytes, *, filename: str = "") -> PortraitMetadata:
    """Backward-compatible metadata validation through the normalizer."""

    return normalize_portrait_image(data, filename=filename).metadata
