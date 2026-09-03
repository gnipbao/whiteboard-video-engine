import json
from pathlib import Path

import pytest
from PIL import Image

from whiteboard_skill import cli
from whiteboard_skill.cli import (
    _build_parser,
    _resolve_draw_text,
    _resolve_raster_resolution,
    main,
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


def test_resolve_raster_resolution_preserves_aspect_for_single_dimension(
    tmp_path: Path,
):
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


def test_list_styles_json_is_stable_machine_readable(capsys):
    assert main(["list-styles", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload
    assert payload[0]["order"] == 1
    assert {"id", "name_zh", "name_en", "compatibility", "summary"} <= payload[0].keys()
    assert payload[0]["render"]["block_fill_style"] == "crayon"
    assert payload[0]["aesthetic"]


def test_list_styles_text_can_filter_by_compatibility(capsys):
    assert main(["list-styles", "--compatibility", "native"]) == 0

    output = capsys.readouterr().out
    assert "warm-crayon-storybook" in output
    assert "[native]" in output
    assert "[experimental]" not in output


def test_recommend_styles_reads_script_and_honors_limit(tmp_path: Path, capsys):
    script = tmp_path / "story.md"
    script.write_text("古代寺庙里的三个和尚讲了一个禅意寓言。", encoding="utf-8")

    assert main(["recommend-styles", str(script), "--limit", "2", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 2
    assert payload[0]["id"] == "ink-wash-minimal"


def test_plan_and_run_style_sources_are_mutually_exclusive():
    parser = _build_parser()
    for command in (
        ["plan-script", "story.md", "-o", "plan.json"],
        ["run", "story.md", "-o", "story.mp4"],
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    *command,
                    "--style",
                    "clean-whiteboard",
                    "--custom-style",
                    "soft pencil",
                ]
            )


def test_run_accepts_style_theme_and_renderer_overrides():
    args = _build_parser().parse_args(
        [
            "run",
            "story.md",
            "-o",
            "story.mp4",
            "--style",
            "anime-graphite",
            "--theme",
            "雨天回忆",
            "--block-fill-style",
            "dry-brush",
            "--stroke-detail",
            "max",
            "--line-thickness",
            "2",
            "--no-line-art-snap",
            "--line-art-snap-threshold",
            "220",
            "--max-draw-blocks",
            "5",
            "--draw-blocks",
            "3",
            "--block-overlap",
            "0.1",
            "--block-order",
            "source",
        ]
    )

    assert args.style == "anime-graphite"
    assert args.theme == "雨天回忆"
    assert args.block_fill_style == "dry-brush"
    assert args.stroke_detail == "max"
    assert args.line_thickness == 2
    assert args.line_art_snap is False
    assert args.line_art_snap_threshold == 220
    assert (
        args.max_draw_blocks,
        args.draw_blocks,
        args.block_overlap,
        args.block_order,
    ) == (5, 3, 0.1, "source")


def test_render_commands_accept_block_fill_style():
    parser = _build_parser()

    photo = parser.parse_args(
        [
            "render-photo",
            "input.png",
            "-o",
            "out.mp4",
            "--block-fill-style",
            "soft-wash",
        ]
    )
    render = parser.parse_args(
        ["render-image", "line.png", "-o", "out.mp4", "--block-fill-style", "clean"]
    )

    assert photo.block_fill_style == "soft-wash"
    assert render.block_fill_style == "clean"


def test_render_image_passes_block_fill_style_to_renderer(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    def fake_render_image(_image: Path, _output: Path, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "render_image", fake_render_image)

    assert (
        main(
            [
                "render-image",
                str(tmp_path / "lineart.png"),
                "-o",
                str(tmp_path / "out.mp4"),
                "--block-fill-style",
                "dry-brush",
            ]
        )
        == 0
    )
    assert captured["block_fill_style"] == "dry-brush"


def test_render_photo_passes_block_fill_style_to_renderer(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    class FakeLineArtProvider:
        def extract(self, _source: Path, output: Path):
            Image.new("RGB", (96, 64), "white").save(output)

    def fake_render_image(_image: Path, _output: Path, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        cli, "get_lineart_provider", lambda _name: FakeLineArtProvider()
    )
    monkeypatch.setattr(cli, "render_image", fake_render_image)

    assert (
        main(
            [
                "render-photo",
                str(tmp_path / "source.png"),
                "-o",
                str(tmp_path / "out.mp4"),
                "--block-fill-style",
                "clean",
            ]
        )
        == 0
    )
    assert captured["block_fill_style"] == "clean"


def test_run_passes_visual_style_and_renderer_overrides(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    class FakeProject:
        def __init__(self) -> None:
            self.subtitle_path = None
            self.scenes: list[object] = []
            self.visual_style_id = "custom-test-style"

    def fake_run_pipeline(_script: Path, _output: Path, **kwargs):
        captured.update(kwargs)
        return FakeProject()

    monkeypatch.setattr(cli, "run_pipeline", fake_run_pipeline)

    assert (
        main(
            [
                "run",
                str(tmp_path / "story.md"),
                "-o",
                str(tmp_path / "story.mp4"),
                "--custom-style",
                "charcoal on cream paper",
                "--theme",
                "雨天回忆",
                "--block-fill-style",
                "soft-wash",
                "--stroke-detail",
                "max",
                "--line-thickness",
                "2",
                "--no-line-art-snap",
                "--line-art-snap-threshold",
                "218",
            ]
        )
        == 0
    )
    assert captured["visual_style"] is None
    assert captured["custom_style"] == "charcoal on cream paper"
    assert captured["custom_style_file"] is None
    assert captured["visual_theme"] == "雨天回忆"
    assert captured["block_fill_style"] == "soft-wash"
    assert captured["stroke_detail"] == "max"
    assert captured["line_thickness"] == 2
    assert captured["line_art_snap"] is False
    assert captured["line_art_snap_threshold"] == 218


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

    args = parser.parse_args(
        [
            "render-image",
            "lineart.png",
            "-o",
            "out.mp4",
            "--draw-text-file",
            str(caption),
        ]
    )

    assert _resolve_draw_text(args) == "第一行\n第二行"
