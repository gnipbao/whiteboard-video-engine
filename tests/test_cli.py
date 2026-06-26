from pathlib import Path

from PIL import Image

from whiteboard_skill.cli import _resolve_raster_resolution


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
