from pathlib import Path

from PIL import Image, ImageDraw

from whiteboard_skill.fingerprints import file_sha256, stable_fingerprint
from whiteboard_skill.image_gen import (
    _normalize_registered_canvas,
    extract_scene_lineart,
    generate_scene_images,
    use_precomputed_source_images,
)
from whiteboard_skill.models import Scene


class _ColorProvider:
    def generate(self, _prompt: str, out_path: Path, size: tuple[int, int]) -> Path:
        image = Image.new("RGB", size, "white")
        ImageDraw.Draw(image).rectangle((12, 12, size[0] - 12, size[1] - 12), fill=(220, 90, 60))
        image.save(out_path)
        return out_path


class _LineArtProvider:
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, color_png: Path, out_png: Path) -> Path:
        self.calls += 1
        with Image.open(color_png) as source:
            image = Image.new("RGB", source.size, "white")
        ImageDraw.Draw(image).rectangle((12, 12, image.width - 12, image.height - 12), outline="black", width=2)
        image.save(out_png)
        return out_png


def test_color_storyboard_then_registered_local_lineart(tmp_path: Path):
    scenes = [Scene(id=1, narration="旁白", image_prompt="彩图")]
    scenes = generate_scene_images(
        scenes,
        _ColorProvider(),
        tmp_path / "color",
        (96, 64),
        asset_role="source",
    )
    scenes = extract_scene_lineart(
        scenes,
        _LineArtProvider(),
        tmp_path / "lineart",
        (96, 64),
    )

    assert scenes[0].source_image_path == tmp_path / "color" / "scene_01.png"
    assert scenes[0].lineart_path == tmp_path / "lineart" / "scene_01.png"
    assert scenes[0].image_path == scenes[0].lineart_path
    with Image.open(scenes[0].source_image_path) as color, Image.open(scenes[0].lineart_path) as line:
        assert color.size == line.size


def test_precomputed_storyboards_support_codex_generated_assets(tmp_path: Path):
    Image.new("RGB", (64, 64), "white").save(tmp_path / "scene_01.png")
    scenes = use_precomputed_source_images(
        [Scene(id=1, narration="旁白", image_prompt="彩图")],
        tmp_path,
    )
    assert scenes[0].source_image_path == (tmp_path / "scene_01.png").resolve()


def test_precomputed_storyboard_is_normalized_before_registered_lineart(tmp_path: Path):
    source_dir = tmp_path / "storyboards"
    source_dir.mkdir()
    Image.new("RGB", (64, 64), (240, 230, 210)).save(source_dir / "scene_01.png")

    scenes = use_precomputed_source_images(
        [Scene(id=1, narration="旁白", image_prompt="彩图")],
        source_dir,
        output_dir=tmp_path / "work" / "color",
        size=(160, 90),
    )
    scenes = extract_scene_lineart(
        scenes,
        _LineArtProvider(),
        tmp_path / "work" / "lineart",
        (160, 90),
    )

    with Image.open(scenes[0].source_image_path) as color, Image.open(scenes[0].lineart_path) as line:
        assert color.size == (160, 90)
        assert line.size == (160, 90)


def test_near_matching_small_storyboard_covers_canvas_without_border(tmp_path: Path):
    source = tmp_path / "source.png"
    output = tmp_path / "normalized.png"
    image = Image.new("RGB", (167, 94), (220, 80, 60))
    for point in ((0, 0), (166, 0), (0, 93), (166, 93)):
        image.putpixel(point, (92, 103, 81))
    image.save(source)

    _normalize_registered_canvas(source, output, (192, 108))

    with Image.open(output) as normalized:
        assert normalized.size == (192, 108)
        for point in ((0, 54), (191, 54), (96, 0), (96, 107)):
            red, green, blue = normalized.getpixel(point)
            assert red > 190
            assert red > green * 2
            assert red > blue * 2


def test_clearly_different_aspect_ratio_remains_contained(tmp_path: Path):
    source = tmp_path / "portrait.png"
    output = tmp_path / "normalized.png"
    Image.new("RGB", (40, 60), (220, 80, 60)).save(source)

    _normalize_registered_canvas(
        source,
        output,
        (160, 90),
        background=(11, 22, 33),
    )

    with Image.open(output) as normalized:
        assert normalized.size == (160, 90)
        assert normalized.getpixel((0, 45)) == (11, 22, 33)
        assert normalized.getpixel((159, 45)) == (11, 22, 33)
        assert normalized.getpixel((80, 0)) == (220, 80, 60)
        assert normalized.getpixel((80, 89)) == (220, 80, 60)


def test_resume_invalidates_legacy_precomputed_normalization(tmp_path: Path):
    source_dir = tmp_path / "storyboards"
    output_dir = tmp_path / "normalized"
    source_dir.mkdir()
    output_dir.mkdir()
    source = source_dir / "scene_01.png"
    Image.new("RGB", (167, 94), (220, 80, 60)).save(source)
    Image.new("RGB", (192, 108), "blue").save(output_dir / "scene_01.png")
    legacy_fingerprint = stable_fingerprint(
        {
            "precomputed_sha256": file_sha256(source),
            "size": (192, 108),
        }
    )
    scene = Scene(
        id=1,
        narration="旁白",
        image_prompt="彩图",
        source_generation_fingerprint=legacy_fingerprint,
    )

    use_precomputed_source_images(
        [scene],
        source_dir,
        output_dir=output_dir,
        size=(192, 108),
        resume=True,
    )

    assert scene.source_generation_fingerprint != legacy_fingerprint
    with Image.open(output_dir / "scene_01.png") as normalized:
        assert normalized.getpixel((96, 54)) == (220, 80, 60)


def test_lineart_resume_invalidates_when_registered_source_changes(tmp_path: Path):
    color_path = tmp_path / "color.png"
    Image.new("RGB", (96, 64), "red").save(color_path)
    scene = Scene(id=1, narration="旁白", image_prompt="彩图", source_image_path=color_path)
    provider = _LineArtProvider()

    extract_scene_lineart([scene], provider, tmp_path / "lineart", (96, 64))
    extract_scene_lineart([scene], provider, tmp_path / "lineart", (96, 64), resume=True)
    assert provider.calls == 1

    Image.new("RGB", (96, 64), "blue").save(color_path)
    extract_scene_lineart([scene], provider, tmp_path / "lineart", (96, 64), resume=True)
    assert provider.calls == 2
