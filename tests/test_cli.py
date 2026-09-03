from pathlib import Path

from PIL import Image

from whiteboard_skill.cli import (
    _build_parser,
    _resolve_draw_text,
    _resolve_raster_resolution,
)


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


def test_render_image_accepts_multiline_text_wipe_options():
    parser = _build_parser()

    args = parser.parse_args(
        [
            "render-image",
            "lineart.png",
            "-o",
            "out.mp4",
            "--draw-text",
            "第一行\\n第二行",
            "--draw-text-position",
            "top",
            "--draw-text-align",
            "left",
            "--draw-text-reveal",
            "line-wipe",
            "--draw-text-order",
            "before",
        ]
    )

    assert _resolve_draw_text(args) == "第一行\n第二行"
    assert args.draw_text_position == "top"
    assert args.draw_text_align == "left"
    assert args.draw_text_reveal == "line-wipe"
    assert args.draw_text_order == "before"


def test_render_photo_accepts_direct_detail_and_horizontal_color_wipes():
    parser = _build_parser()

    args = parser.parse_args(
        [
            "render-photo",
            "input.png",
            "-o",
            "out.mp4",
            "--line-reveal",
            "detail-wipe",
            "--base-line-opacity",
            "0.6",
            "--color-fill",
            "left-to-right-gradient",
        ]
    )

    assert args.line_reveal == "detail-wipe"
    assert args.base_line_opacity == 0.6
    assert args.color_fill == "left-to-right-gradient"


def test_draw_text_file_reads_utf8_multiline_text(tmp_path: Path):
    caption = tmp_path / "caption.txt"
    caption.write_text("第一行\n第二行", encoding="utf-8")
    parser = _build_parser()

    args = parser.parse_args(["render-image", "lineart.png", "-o", "out.mp4", "--draw-text-file", str(caption)])

    assert _resolve_draw_text(args) == "第一行\n第二行"
