from pathlib import Path

from PIL import Image

from whiteboard_skill.cli import _build_parser, _resolve_raster_resolution


def test_resolve_raster_resolution_defaults_to_source_size(tmp_path: Path):
    image = tmp_path / "source.png"
    Image.new("RGB", (101, 77), "white").save(image)

    resolution, used_source_size = _resolve_raster_resolution(image, None, None)

    assert resolution == (100, 76)
    assert used_source_size is True


def test_resolve_raster_resolution_honors_explicit_size(tmp_path: Path):
    image = tmp_path / "source.png"
    Image.new("RGB", (101, 77), "white").save(image)

    resolution, used_source_size = _resolve_raster_resolution(image, 448, 600)

    assert resolution == (448, 600)
    assert used_source_size is False


def test_resolve_raster_resolution_preserves_aspect_for_single_dimension(tmp_path: Path):
    image = tmp_path / "source.png"
    Image.new("RGB", (100, 200), "white").save(image)

    resolution, used_source_size = _resolve_raster_resolution(image, 50, None)

    assert resolution == (50, 100)
    assert used_source_size is False


def test_render_commands_default_to_asian_hand_and_adaptive_line_width():
    parser = _build_parser()

    photo = parser.parse_args(["render-photo", "input.png", "-o", "out.mp4"])
    render = parser.parse_args(["render-image", "lineart.png", "-o", "out.mp4"])
    run = parser.parse_args(["run", "script.md", "-o", "out.mp4"])

    assert photo.hand == render.hand == run.hand == "asian"
    assert photo.line_thickness == render.line_thickness == 0
