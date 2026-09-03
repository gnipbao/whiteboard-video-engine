"""OpenAI image provider."""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path

from PIL import Image

from ..config import settings


class OpenAIImageProvider:
    """Generate color storyboard PNGs through the OpenAI Images API."""

    def __init__(self, model: str | None = None, quality: str | None = None, client=None) -> None:
        self.model = model or settings.image_model
        self.quality = quality or settings.image_quality
        if self.quality not in {"low", "medium", "high", "auto"}:
            raise ValueError("IMAGE_QUALITY must be low, medium, high, or auto")
        if client is not None:
            self._client = client
            return
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when MOCK is not enabled")
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover - optional dependency branch
            raise RuntimeError("Install optional dependency `openai` or run with MOCK=1") from exc
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=180.0,
        )

    def generate(self, prompt: str, out_path: Path, size: tuple[int, int]) -> Path:
        """Generate an image and write it to `out_path`."""

        out_path.parent.mkdir(parents=True, exist_ok=True)
        image_size = _openai_size(size, self.model)
        result = self._client.images.generate(
            model=self.model,
            prompt=prompt,
            size=image_size,
            quality=self.quality,
            background="opaque",
            output_format="png",
        )
        if not getattr(result, "data", None):
            raise RuntimeError("Image provider returned no image data")
        item = result.data[0]
        b64 = getattr(item, "b64_json", None)
        if not b64:
            raise RuntimeError("Image provider returned no base64 PNG data")
        try:
            payload = base64.b64decode(b64, validate=True)
            with Image.open(io.BytesIO(payload)) as generated:
                generated.load()
                generated.convert("RGB").save(out_path, format="PNG")
        except Exception as exc:
            raise RuntimeError("Image provider returned invalid image data") from exc
        return out_path


def _openai_size(size: tuple[int, int], model: str = "gpt-image-2") -> str:
    width, height = size
    if model.startswith("gpt-image-2"):
        return _gpt_image_2_size(width, height)
    if width == height:
        return "1024x1024"
    return "1536x1024" if width > height else "1024x1536"


def _gpt_image_2_size(width: int, height: int) -> str:
    """Return a legal GPT Image 2 size while retaining the video aspect ratio."""

    min_pixels = 655_360
    max_pixels = 8_294_400
    max_edge = 3840
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    ratio = width / height
    if ratio > 3.0 or ratio < 1 / 3.0:
        raise ValueError("GPT Image 2 requires an aspect ratio no wider than 3:1")
    if abs(ratio - 1.0) < 0.01:
        return "1024x1024"
    if abs(ratio - 16 / 9) < 0.02:
        return "2048x1152" if width >= 1600 else "1280x720"
    if abs(ratio - 9 / 16) < 0.02:
        return "1152x2048" if height >= 1600 else "720x1280"

    target_width, target_height = float(width), float(height)
    if max(target_width, target_height) > max_edge:
        scale = max_edge / max(target_width, target_height)
        target_width *= scale
        target_height *= scale
    pixels = target_width * target_height
    if pixels < min_pixels:
        scale = math.sqrt(min_pixels / pixels)
        target_width *= scale
        target_height *= scale
    pixels = target_width * target_height
    if pixels > max_pixels:
        scale = math.sqrt(max_pixels / pixels)
        target_width *= scale
        target_height *= scale

    target_pixels = target_width * target_height
    best: tuple[float, int, int] | None = None
    for legal_height in range(16, max_edge + 1, 16):
        for legal_width in range(16, max_edge + 1, 16):
            legal_pixels = legal_width * legal_height
            legal_ratio = legal_width / legal_height
            if legal_pixels < min_pixels or legal_pixels > max_pixels:
                continue
            if legal_ratio < 1 / 3 or legal_ratio > 3:
                continue
            aspect_error = abs(math.log(legal_ratio / ratio))
            area_error = abs(math.log(legal_pixels / target_pixels))
            dimension_error = (
                abs(math.log(legal_width / target_width))
                + abs(math.log(legal_height / target_height))
            )
            score = aspect_error * 12 + area_error + dimension_error * 0.1
            candidate = (score, legal_width, legal_height)
            if best is None or candidate < best:
                best = candidate
    if best is None:
        raise ValueError("Could not map the requested dimensions to a legal GPT Image 2 size")
    _, legal_width, legal_height = best
    return f"{legal_width}x{legal_height}"
