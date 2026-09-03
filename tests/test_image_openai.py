import base64
import io
import random
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from whiteboard_skill.providers.image_openai import (
    OpenAIImageProvider,
    _gpt_image_2_size,
)


class _FakeImages:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.kwargs = None

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(self.payload).decode("ascii"))]
        )


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (230, 120, 80)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_gpt_image_2_provider_requests_color_storyboard_options(tmp_path: Path):
    images = _FakeImages(_png_bytes())
    client = SimpleNamespace(images=images)
    provider = OpenAIImageProvider(model="gpt-image-2", quality="low", client=client)

    output = provider.generate("crayon story scene, no text", tmp_path / "scene.png", (1920, 1080))

    assert output.exists()
    assert images.kwargs == {
        "model": "gpt-image-2",
        "prompt": "crayon story scene, no text",
        "size": "2048x1152",
        "quality": "low",
        "background": "opaque",
        "output_format": "png",
    }


def test_gpt_image_2_size_preserves_common_video_ratios():
    assert _gpt_image_2_size(1280, 720) == "1280x720"
    assert _gpt_image_2_size(1920, 1080) == "2048x1152"
    assert _gpt_image_2_size(1080, 1920) == "1152x2048"
    assert _gpt_image_2_size(1000, 1000) == "1024x1024"


def test_gpt_image_2_size_handles_three_to_one_boundaries():
    assert _gpt_image_2_size(300, 100) == "1440x480"
    assert _gpt_image_2_size(100, 300) == "480x1440"


def test_gpt_image_2_size_fuzz_always_satisfies_api_limits():
    generator = random.Random(17)
    for _ in range(250):
        height = generator.randint(100, 10_000)
        ratio = generator.uniform(1 / 3, 3)
        width = max(1, round(height * ratio))
        if width / height < 1 / 3 or width / height > 3:
            continue
        mapped_width, mapped_height = map(int, _gpt_image_2_size(width, height).split("x"))
        assert mapped_width % 16 == 0
        assert mapped_height % 16 == 0
        assert max(mapped_width, mapped_height) <= 3840
        assert 655_360 <= mapped_width * mapped_height <= 8_294_400
        assert 1 / 3 <= mapped_width / mapped_height <= 3
