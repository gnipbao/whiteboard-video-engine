"""Command line entrypoint for the whiteboard skill."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .compose import compose_project, ffmpeg_path
from .config import settings
from .logging_setup import setup_logging
from .models import TextRole
from .pipeline import run_pipeline
from .preprocess import quality_check, svg_to_strokes, to_strokes
from .providers import get_llm_provider
from .providers.lineart import get_lineart_provider, vectorize_with_vtracer
from .scene_split import split_script
from .styles import (
    available_styles,
    recommend_styles,
    resolve_style,
    style_display_payload,
)
from .whiteboard import DEFAULT_LINE_ART_SNAP_THRESHOLD, available_hands, render_image


def _parse_block_sequence(value: str) -> list[int]:
    try:
        sequence = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "block sequence must be comma-separated integer ids"
        ) from exc
    if not sequence:
        raise argparse.ArgumentTypeError("block sequence cannot be empty")
    return sequence


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    setup_logging(getattr(args, "log_level", settings.log_level))

    if getattr(args, "mock", False):
        os.environ["MOCK"] = "1"

    try:
        if args.command == "doctor":
            return _doctor()
        if args.command == "list-hands":
            for hand in available_hands():
                print(hand)
            return 0
        if args.command == "list-styles":
            selected_styles = available_styles()
            if args.compatibility:
                selected_styles = tuple(
                    style
                    for style in selected_styles
                    if style.compatibility == args.compatibility
                )
            payload = [style_display_payload(style) for style in selected_styles]
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                for item in payload:
                    print(
                        f"{item['order']:>2}  {item['id']} [{item['compatibility']}]  "
                        f"{item['name_zh']} / {item['name_en']} — {item['summary']}"
                    )
            return 0
        if args.command == "recommend-styles":
            script = args.script.read_text(encoding="utf-8")
            payload = [
                style_display_payload(style)
                for style in recommend_styles(script, limit=args.limit)
            ]
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                for index, item in enumerate(payload, start=1):
                    print(
                        f"{index}. {item['id']}  {item['name_zh']} / "
                        f"{item['name_en']} — {item['summary']}"
                    )
            return 0
        if args.command == "plan-script":
            script = args.script.read_text(encoding="utf-8")
            style_selector = args.style
            if (
                style_selector is None
                and args.custom_style is None
                and args.custom_style_file is None
            ):
                style_selector = settings.visual_style
            style = resolve_style(
                style_selector,
                script=script,
                custom_style=args.custom_style,
                custom_style_file=args.custom_style_file,
            )
            provider = get_llm_provider(
                mock=not args.real,
                style_guidance=style.planner_guidance,
            )
            scenes = split_script(
                script,
                provider,
                args.scenes,
                style=style,
                theme=args.theme,
            )
            payload = [_scene_payload(scene) for scene in scenes]
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(args.output)
            return 0
        if args.command == "analyze-image":
            payload = _analyze_image(
                args.image, (args.width, args.height), args.stroke_detail
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(args.output)
            return 0
        if args.command == "normalize-lineart":
            _normalize_lineart(
                args.image,
                args.output,
                threshold=args.threshold,
                clear_edge=args.clear_edge,
            )
            print(args.output)
            return 0
        if args.command == "extract-lineart":
            provider = get_lineart_provider(args.provider)
            provider.extract(args.image, args.output)
            if args.svg_output:
                vectorize_with_vtracer(args.output, args.svg_output)
            print(args.output)
            return 0
        if args.command == "render-photo":
            lineart_path = args.lineart_output or args.output.with_name(
                f"{args.output.stem}-lineart.png"
            )
            provider = get_lineart_provider(args.lineart_provider)
            provider.extract(args.image, lineart_path)
            render_input = lineart_path
            use_lineart_size = True
            if args.svg_output:
                vectorize_with_vtracer(lineart_path, args.svg_output)
                render_input = args.svg_output
                use_lineart_size = False
            if use_lineart_size:
                resolution, used_source_size = _resolve_raster_resolution(
                    lineart_path, args.width, args.height
                )
            else:
                resolution, used_source_size = _resolve_raster_resolution(
                    args.image, args.width, args.height
                )
            render_image(
                render_input,
                args.output,
                duration=args.duration,
                fps=args.fps,
                resolution=resolution,
                tail_color_sec=args.tail_color,
                source_image_path=args.image,
                source_fit="exact" if used_source_size else args.source_fit,
                color_fill_mode=args.color_fill,
                color_fill_blocks=args.color_blocks,
                line_reveal_mode=args.line_reveal,
                base_line_opacity=args.base_line_opacity,
                hand_style=args.hand,
                hand_scale=args.hand_scale,
                draw_text=_resolve_draw_text(args),
                draw_text_role=args.draw_text_role,
                draw_text_position=args.draw_text_position,
                draw_text_align=args.draw_text_align,
                draw_text_width=args.draw_text_width,
                draw_text_max_height=args.draw_text_max_height,
                draw_text_line_spacing=args.draw_text_line_spacing,
                draw_text_font_size=args.draw_text_font_size,
                draw_text_font=args.draw_text_font,
                draw_text_reveal=args.draw_text_reveal,
                draw_text_order=args.draw_text_order,
                line_art_snap=not args.no_lineart_snap,
                line_art_snap_threshold=args.lineart_snap_threshold,
                line_thickness=args.line_thickness,
                stroke_detail=args.stroke_detail,
                animation_preset=args.animation_preset,
                story_text=args.story_text,
                max_draw_blocks=args.max_draw_blocks,
                draw_blocks=args.draw_blocks,
                block_overlap=args.block_overlap,
                block_order=args.block_order,
                block_sequence=args.block_sequence,
                block_fill_style=args.block_fill_style,
                color_fill_scope=args.color_fill_scope,
            )
            print(args.output)
            return 0
        if args.command == "render-image":
            resolution = (args.width, args.height)
            if args.size_from_image:
                if args.image.suffix.lower() == ".svg":
                    raise ValueError(
                        "--size-from-image is only supported for raster line-art images"
                    )
                from PIL import Image

                with Image.open(args.image) as source_image:
                    width, height = source_image.size
                resolution = (max(2, width - width % 2), max(2, height - height % 2))
            render_image(
                args.image,
                args.output,
                duration=args.duration,
                fps=args.fps,
                resolution=resolution,
                tail_color_sec=args.tail_color,
                source_image_path=args.source_image,
                source_fit=args.source_fit,
                color_fill_mode=args.color_fill,
                color_fill_blocks=args.color_blocks,
                line_reveal_mode=args.line_reveal,
                base_line_opacity=args.base_line_opacity,
                hand_style=args.hand,
                hand_scale=args.hand_scale,
                draw_text=_resolve_draw_text(args),
                draw_text_role=args.draw_text_role,
                draw_text_position=args.draw_text_position,
                draw_text_align=args.draw_text_align,
                draw_text_width=args.draw_text_width,
                draw_text_max_height=args.draw_text_max_height,
                draw_text_line_spacing=args.draw_text_line_spacing,
                draw_text_font_size=args.draw_text_font_size,
                draw_text_font=args.draw_text_font,
                draw_text_reveal=args.draw_text_reveal,
                draw_text_order=args.draw_text_order,
                line_art_snap=not args.no_lineart_snap,
                line_art_snap_threshold=args.lineart_snap_threshold,
                line_thickness=args.line_thickness,
                stroke_detail=args.stroke_detail,
                animation_preset=args.animation_preset,
                story_text=args.story_text,
                max_draw_blocks=args.max_draw_blocks,
                draw_blocks=args.draw_blocks,
                block_overlap=args.block_overlap,
                block_order=args.block_order,
                block_sequence=args.block_sequence,
                block_fill_style=args.block_fill_style,
                color_fill_scope=args.color_fill_scope,
            )
            print(args.output)
            return 0
        if args.command == "compose":
            compose_project(args.videos, [], args.output)
            print(args.output)
            return 0
        if args.command == "run":
            project = run_pipeline(
                args.script,
                args.output,
                scene_count=args.scenes,
                fps=args.fps,
                resolution=(args.width, args.height),
                voice=args.voice,
                tail_color_seconds=args.tail_color,
                resume=args.resume,
                mock=args.mock,
                hand_style=args.hand,
                hand_scale=args.hand_scale,
                tts_provider=args.tts_provider,
                image_model=args.image_model,
                image_quality=args.image_quality,
                lineart_provider=args.lineart_provider,
                scene_asset_mode=args.scene_assets,
                storyboard_dir=args.storyboard_dir,
                scene_plan_path=args.scene_plan,
                animation_preset=args.animation_preset,
                max_draw_blocks=args.max_draw_blocks,
                draw_blocks=args.draw_blocks,
                block_overlap=args.block_overlap,
                block_order=args.block_order,
                block_sequence=args.block_sequence,
                visual_style=args.style,
                custom_style=args.custom_style,
                custom_style_file=args.custom_style_file,
                visual_theme=args.theme,
                block_fill_style=args.block_fill_style,
                color_fill_scope=args.color_fill_scope,
                stroke_detail=args.stroke_detail,
                line_thickness=args.line_thickness,
                line_art_snap=args.line_art_snap,
                line_art_snap_threshold=args.line_art_snap_threshold,
                captions=args.captions,
                burn_subtitles=args.burn_subtitles,
                subtitle_font=args.subtitle_font,
                subtitle_font_size=args.subtitle_font_size,
                subtitle_margin_v=args.subtitle_margin_v,
                subtitle_outline=args.subtitle_outline,
            )
            print(args.output)
            if project.subtitle_path is not None:
                print(f"subtitles={project.subtitle_path}")
            print(
                f"style={project.visual_style_id} scenes={len(project.scenes)} "
                f"work_dir={settings.work_dir / _slug(args.script.stem)}"
            )
            return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary prints a concise provider/render error
        print(f"whiteboard: {exc}", file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _resolve_draw_text(args: argparse.Namespace) -> str | None:
    if getattr(args, "draw_text_file", None) is not None:
        return args.draw_text_file.read_text(encoding="utf-8")
    text = getattr(args, "draw_text", None)
    return text.replace("\\n", "\n") if text else None


def _add_draw_text_arguments(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--draw-text",
        help="Text to reveal. Newlines are preserved; literal \\n is also accepted.",
    )
    source.add_argument(
        "--draw-text-file",
        type=Path,
        help="UTF-8 text file used for longer multiline captions.",
    )
    parser.add_argument(
        "--draw-text-role",
        choices=[TextRole.ANNOTATION.value, TextRole.CAPTION.value],
        default=TextRole.ANNOTATION.value,
        help="Treat text as a short late annotation (default) or an explicit full caption.",
    )
    parser.add_argument(
        "--draw-text-position", choices=["bottom", "top", "center"], default="bottom"
    )
    parser.add_argument(
        "--draw-text-align", choices=["left", "center", "right"], default="center"
    )
    parser.add_argument(
        "--draw-text-width",
        type=float,
        default=0.82,
        help="Maximum text width as a fraction of the canvas (0.1-1.0).",
    )
    parser.add_argument(
        "--draw-text-max-height",
        type=float,
        default=0.36,
        help="Maximum text block height as a fraction of the canvas (0.1-1.0).",
    )
    parser.add_argument(
        "--draw-text-line-spacing",
        type=float,
        default=0.25,
        help="Line spacing as a fraction of the selected font size.",
    )
    parser.add_argument(
        "--draw-text-font-size",
        type=int,
        help="Optional fixed font size in pixels; otherwise text is fit automatically.",
    )
    parser.add_argument(
        "--draw-text-font",
        type=Path,
        help="Optional TTF/TTC/OTF font path, useful for a handwritten Chinese font.",
    )
    parser.add_argument(
        "--draw-text-reveal",
        choices=["stroke", "line-wipe"],
        default="stroke",
        help="Trace glyph strokes or reveal each line from left to right.",
    )
    parser.add_argument(
        "--draw-text-order",
        choices=["before", "after"],
        default="after",
        help="Reveal text before or after the image strokes.",
    )


def _add_block_animation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--animation-preset",
        choices=["classic", "block-speedpaint"],
        default="classic",
        help="Classic whole-scene drawing or object-by-object outline/detail/crayon beats.",
    )
    parser.add_argument(
        "--story-text",
        help="Legacy explicit full caption revealed from left to right by block-speedpaint.",
    )
    parser.add_argument(
        "--max-draw-blocks",
        type=int,
        default=6,
        help="Maximum automatically inferred object blocks.",
    )
    parser.add_argument(
        "--draw-blocks",
        type=int,
        help="Preferred maximum natural block count; connected objects are never split to reach it.",
    )
    parser.add_argument(
        "--block-overlap",
        type=float,
        default=0.08,
        help="Overlap between adjacent block windows (0-0.65).",
    )
    parser.add_argument(
        "--block-order", choices=["reading", "source"], default="reading"
    )
    parser.add_argument(
        "--block-sequence",
        type=_parse_block_sequence,
        help="Explicit inferred block ids, such as 1,0.",
    )


def _add_visual_style_arguments(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--style", help="Built-in style id, order, localized name, alias, or auto."
    )
    source.add_argument(
        "--custom-style", help="Inline visual direction for a custom style recipe."
    )
    source.add_argument(
        "--custom-style-file",
        type=Path,
        help="UTF-8 custom style description or constrained JSON recipe.",
    )
    parser.add_argument(
        "--theme",
        help="Optional story-specific art direction layered onto the selected style.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="whiteboard",
        description="Generate hand-drawn whiteboard videos from scripts or line-art images.",
    )
    parser.add_argument("--log-level", default=settings.log_level)
    sub = parser.add_subparsers(dest="command")

    doctor = sub.add_parser("doctor", help="Check local runtime dependencies.")
    doctor.set_defaults(command="doctor")

    hands = sub.add_parser("list-hands", help="List built-in hand cursor styles.")
    hands.set_defaults(command="list-hands")

    styles = sub.add_parser("list-styles", help="List built-in visual style recipes.")
    styles.add_argument(
        "--json", action="store_true", help="Write machine-readable JSON."
    )
    styles.add_argument(
        "--compatibility",
        choices=["native", "adaptive", "experimental"],
        help="Only show styles with this whiteboard-rendering compatibility level.",
    )
    styles.set_defaults(command="list-styles")

    recommend = sub.add_parser(
        "recommend-styles",
        help="Recommend built-in visual styles for a UTF-8 story script.",
    )
    recommend.add_argument("script", type=Path)
    recommend.add_argument("--limit", type=int, default=5)
    recommend.add_argument(
        "--json", action="store_true", help="Write machine-readable JSON."
    )
    recommend.set_defaults(command="recommend-styles")

    plan = sub.add_parser("plan-script", help="Split a script into storyboard JSON.")
    plan.add_argument("script", type=Path)
    plan.add_argument("-o", "--output", type=Path, required=True)
    plan.add_argument("--scenes", type=int, default=4)
    plan.add_argument(
        "--real",
        action="store_true",
        help="Use configured real LLM provider instead of deterministic mock planning.",
    )
    _add_visual_style_arguments(plan)
    plan.set_defaults(command="plan-script")

    analyze = sub.add_parser(
        "analyze-image", help="Write a lightweight layer/stroke analysis JSON."
    )
    analyze.add_argument("image", type=Path)
    analyze.add_argument("-o", "--output", type=Path, required=True)
    analyze.add_argument("--width", type=int, default=1920)
    analyze.add_argument("--height", type=int, default=1080)
    analyze.add_argument(
        "--stroke-detail",
        choices=["balanced", "rich", "max"],
        default="rich",
        help="Raster stroke extraction detail. Rich keeps short semantic details; max keeps tiny logo/facial strokes.",
    )
    analyze.set_defaults(command="analyze-image")

    normalize = sub.add_parser(
        "normalize-lineart",
        help="Normalize an existing line-art bitmap to pure black-on-white without dilation or thickening.",
    )
    normalize.add_argument("image", type=Path)
    normalize.add_argument("-o", "--output", type=Path, required=True)
    normalize.add_argument(
        "--threshold",
        type=int,
        default=224,
        help="Dark-pixel threshold for pure B/W conversion. Lower values avoid thickening anti-aliased lines.",
    )
    normalize.add_argument(
        "--clear-edge",
        type=int,
        default=6,
        help="Clear this many pixels on each canvas edge to remove generated borders.",
    )
    normalize.set_defaults(command="normalize-lineart")

    lineart_provider_choices = ["auto", "informative", "anime2sketch", "anime", "manga"]

    extract = sub.add_parser(
        "extract-lineart",
        help="Extract local line art from a color image using installed neural providers.",
    )
    extract.add_argument("image", type=Path)
    extract.add_argument("-o", "--output", type=Path, required=True)
    extract.add_argument("--provider", choices=lineart_provider_choices, default="auto")
    extract.add_argument(
        "--svg-output",
        type=Path,
        help="Optional SVG output via vtracer when installed.",
    )
    extract.set_defaults(command="extract-lineart")

    photo = sub.add_parser(
        "render-photo",
        help="Extract local line art from a color image and render a whiteboard MP4 in one step.",
    )
    photo.add_argument("image", type=Path)
    photo.add_argument("-o", "--output", type=Path, required=True)
    photo.add_argument(
        "--lineart-output", type=Path, help="Optional extracted line-art PNG path."
    )
    photo.add_argument(
        "--svg-output",
        type=Path,
        help="Optional SVG output via vtracer when installed; SVG will be rendered if created.",
    )
    photo.add_argument(
        "--lineart-provider", choices=lineart_provider_choices, default="auto"
    )
    photo.add_argument("--duration", type=float, default=8.0)
    photo.add_argument("--fps", type=int, default=60)
    photo.add_argument(
        "--width",
        type=int,
        help="Output width. If omitted, render-photo uses the extracted line-art image width.",
    )
    photo.add_argument(
        "--height",
        type=int,
        help="Output height. If omitted, render-photo uses the extracted line-art image height.",
    )
    photo.add_argument("--tail-color", type=float, default=2.0)
    photo.add_argument(
        "--source-fit",
        choices=["exact", "blur-fill", "contain", "cover"],
        default="exact",
    )
    photo.add_argument(
        "--color-fill",
        choices=[
            "contour-wipe",
            "brush-scan",
            "top-down-blocks",
            "fade",
            "left-to-right-gradient",
        ],
        default="contour-wipe",
    )
    photo.add_argument("--color-blocks", type=int, default=18)
    photo.add_argument(
        "--line-reveal",
        choices=["stroke", "detail-wipe"],
        default="stroke",
        help="Trace strokes or show a simple sketch immediately and add detail from left to right.",
    )
    photo.add_argument(
        "--base-line-opacity",
        type=float,
        default=0.76,
        help="Initial contour and black-hair opacity used by detail-wipe (0.0-1.0).",
    )
    photo.add_argument("--no-lineart-snap", action="store_true")
    photo.add_argument(
        "--lineart-snap-threshold", type=int, default=DEFAULT_LINE_ART_SNAP_THRESHOLD
    )
    photo.add_argument(
        "--line-thickness",
        type=int,
        default=0,
        help="Rendered stroke width. Use 0 to adapt to the source line art, or a positive value to override it.",
    )
    photo.add_argument(
        "--stroke-detail", choices=["balanced", "rich", "max"], default="rich"
    )
    photo.add_argument(
        "--block-fill-style",
        choices=["crayon", "clean", "soft-wash", "dry-brush"],
        default="crayon",
    )
    photo.add_argument(
        "--color-fill-scope",
        choices=["block", "scene"],
        default="block",
        help="Fill each natural object separately, or reveal one registered whole-scene color plate.",
    )
    _add_draw_text_arguments(photo)
    _add_block_animation_arguments(photo)
    photo.add_argument("--hand", default="asian")
    photo.add_argument("--hand-scale", type=float, default=1.0)
    photo.set_defaults(command="render-photo")

    render = sub.add_parser(
        "render-image", help="Render one PNG/SVG image into a hand-drawn MP4."
    )
    render.add_argument("image", type=Path)
    render.add_argument("-o", "--output", type=Path, required=True)
    render.add_argument("--duration", type=float, default=8.0)
    render.add_argument("--fps", type=int, default=60)
    render.add_argument("--width", type=int, default=1920)
    render.add_argument("--height", type=int, default=1080)
    render.add_argument(
        "--size-from-image",
        action="store_true",
        help="Use the raster line-art image size as the render canvas, adjusted to even H.264 dimensions.",
    )
    render.add_argument("--tail-color", type=float, default=2.0)
    render.add_argument(
        "--mode",
        choices=["smooth", "grid"],
        default="smooth",
        help="Compatibility option. Smooth is the maintained renderer.",
    )
    render.add_argument(
        "--source-image",
        type=Path,
        help="Optional original/color image used for the final color fade while drawing from the line-art image.",
    )
    render.add_argument(
        "--source-fit",
        choices=["exact", "blur-fill", "contain", "cover"],
        default="blur-fill",
        help="How to fit --source-image for the final color fill.",
    )
    render.add_argument(
        "--color-fill",
        choices=[
            "contour-wipe",
            "brush-scan",
            "top-down-blocks",
            "fade",
            "left-to-right-gradient",
        ],
        default="contour-wipe",
        help="Final color fill style.",
    )
    render.add_argument(
        "--color-blocks",
        type=int,
        default=18,
        help="Number of horizontal blocks used by top-down color fill.",
    )
    render.add_argument(
        "--line-reveal",
        choices=["stroke", "detail-wipe"],
        default="stroke",
        help="Trace strokes or show a simple sketch immediately and add detail from left to right.",
    )
    render.add_argument(
        "--base-line-opacity",
        type=float,
        default=0.76,
        help="Initial contour and black-hair opacity used by detail-wipe (0.0-1.0).",
    )
    render.add_argument(
        "--no-lineart-snap",
        action="store_true",
        help="Disable snapping to the original complete line-art image before color fill.",
    )
    render.add_argument(
        "--lineart-snap-threshold",
        type=int,
        default=DEFAULT_LINE_ART_SNAP_THRESHOLD,
        help="Threshold used by line-art snap. Lower avoids thickening/noise from gray pixels.",
    )
    render.add_argument(
        "--line-thickness",
        type=int,
        default=0,
        help="Rendered stroke width. Use 0 to adapt to the source line art, or a positive value to override it.",
    )
    render.add_argument(
        "--stroke-detail",
        choices=["balanced", "rich", "max"],
        default="rich",
        help="Raster stroke extraction detail. Rich keeps short semantic details; max keeps tiny logo/facial strokes.",
    )
    render.add_argument(
        "--block-fill-style",
        choices=["crayon", "clean", "soft-wash", "dry-brush"],
        default="crayon",
    )
    render.add_argument(
        "--color-fill-scope",
        choices=["block", "scene"],
        default="block",
        help="Fill each natural object separately, or reveal one registered whole-scene color plate.",
    )
    _add_draw_text_arguments(render)
    _add_block_animation_arguments(render)
    render.add_argument(
        "--hand",
        default="asian",
        help="Hand cursor: asian (default), black, children, white, procedural, none, or a custom PNG/WebP path.",
    )
    render.add_argument("--hand-scale", type=float, default=1.0)
    render.set_defaults(command="render-image")

    compose = sub.add_parser("compose", help="Concatenate rendered MP4 scene clips.")
    compose.add_argument("videos", nargs="+", type=Path)
    compose.add_argument("-o", "--output", type=Path, required=True)
    compose.set_defaults(command="compose")

    run = sub.add_parser("run", help="Run full script-to-video pipeline.")
    run.add_argument("script", type=Path)
    run.add_argument("-o", "--output", type=Path, required=True)
    run.add_argument(
        "--scenes",
        type=int,
        default=4,
        help="Target count for automatic planning; ignored when --scene-plan is provided.",
    )
    run.add_argument("--fps", type=int, default=30)
    run.add_argument("--width", type=int, default=1920)
    run.add_argument("--height", type=int, default=1080)
    run.add_argument("--tail-color", type=float, default=2.0)
    run.add_argument(
        "--tts-provider",
        choices=["none", "edge", "doubao"],
        default=settings.tts_provider,
        help="Narration provider. Use none to render a completely silent video.",
    )
    run.add_argument(
        "--voice",
        help="Provider voice ID. Defaults to the selected provider's recommended voice.",
    )
    run.add_argument(
        "--image-model",
        default=settings.image_model,
        help="OpenAI storyboard model; defaults to gpt-image-2.",
    )
    run.add_argument(
        "--image-quality",
        choices=["low", "medium", "high", "auto"],
        default=settings.image_quality,
    )
    run.add_argument(
        "--lineart-provider", choices=lineart_provider_choices, default="auto"
    )
    run.add_argument(
        "--scene-assets",
        choices=["auto", "color-to-lineart", "direct-lineart"],
        default="auto",
        help="Generate color storyboards then extract local line art; direct-lineart is for Mock previews only.",
    )
    run.add_argument(
        "--storyboard-dir",
        type=Path,
        help="Use precomputed scene_01.png, scene_02.png... color storyboards (for example from Codex image generation).",
    )
    run.add_argument(
        "--scene-plan",
        type=Path,
        help=(
            "Use an explicit JSON scene list instead of remote scene planning. "
            "With --storyboard-dir, only the selected TTS provider is initialized."
        ),
    )
    run.add_argument(
        "--animation-preset",
        choices=["classic", "block-speedpaint"],
        default="block-speedpaint",
    )
    _add_visual_style_arguments(run)
    run.add_argument(
        "--max-draw-blocks",
        type=int,
        default=None,
        help="Override the style's maximum inferred scene blocks.",
    )
    run.add_argument(
        "--draw-blocks",
        type=int,
        default=None,
        help="Override the style's preferred natural block count. Use 0 for automatic grouping.",
    )
    run.add_argument(
        "--block-overlap",
        type=float,
        default=None,
        help="Override the style's block timing overlap (0-0.65).",
    )
    run.add_argument("--block-order", choices=["reading", "source"], default=None)
    run.add_argument(
        "--block-sequence",
        type=_parse_block_sequence,
        help="Explicit inferred block ids, such as 1,0.",
    )
    run.add_argument(
        "--block-fill-style",
        choices=["crayon", "clean", "soft-wash", "dry-brush"],
        default=None,
        help="Override the selected style's block color texture.",
    )
    run.add_argument(
        "--color-fill-scope",
        choices=["block", "scene"],
        default=None,
        help="Override block-local versus whole-scene registered color reveal.",
    )
    run.add_argument(
        "--stroke-detail", choices=["balanced", "rich", "max"], default=None
    )
    run.add_argument("--line-thickness", type=int, default=None)
    run.add_argument(
        "--line-art-snap",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override whether the style snaps to the complete source line art.",
    )
    run.add_argument("--line-art-snap-threshold", type=int, default=None)
    caption_group = run.add_mutually_exclusive_group()
    caption_group.add_argument(
        "--captions",
        action="store_true",
        help="Deprecated no-op; use --burn-subtitles to burn the generated sidecar SRT.",
    )
    caption_group.add_argument(
        "--no-captions",
        action="store_false",
        dest="captions",
        help="Deprecated no-op; subtitle burn-in is disabled by default.",
    )
    caption_group.add_argument(
        "--burn-subtitles",
        action="store_true",
        help="Burn the generated narration SRT into the final MP4 while keeping the sidecar file.",
    )
    run.add_argument(
        "--subtitle-font",
        default="sans-serif",
        help="Font family used for burned subtitles; libass selects a glyph-compatible fallback.",
    )
    run.add_argument(
        "--subtitle-font-size",
        type=float,
        default=16.0,
        help="Burned subtitle size in ASS scale units (16 is suitable for 16:9 video).",
    )
    run.add_argument(
        "--subtitle-margin-v",
        type=int,
        default=22,
        help="Bottom margin for burned subtitles in ASS scale units.",
    )
    run.add_argument(
        "--subtitle-outline",
        type=float,
        default=1.6,
        help="Black outline width for burned subtitles in ASS scale units.",
    )
    run.add_argument("--resume", action="store_true")
    run.add_argument("--mock", action="store_true")
    run.add_argument(
        "--hand",
        default="asian",
        help="Hand cursor: asian (default), black, children, white, procedural, none, or a custom PNG/WebP path.",
    )
    run.add_argument("--hand-scale", type=float, default=1.0)
    run.set_defaults(command="run", captions=False, burn_subtitles=False)
    return parser


def _doctor() -> int:
    required_checks = {
        "ffmpeg": _check(lambda: ffmpeg_path()),
        "numpy": _check(lambda: __import__("numpy")),
        "Pillow": _check(lambda: __import__("PIL")),
        "pydantic": _check(lambda: __import__("pydantic")),
    }
    capability_checks = {
        "openai-sdk": _check(lambda: __import__("openai")),
        "edge-tts": _check(lambda: __import__("edge_tts")),
        "torch": _check(lambda: __import__("torch")),
        "local-lineart": _check(lambda: get_lineart_provider("auto")),
    }
    runtime_settings = type(settings).from_env()
    configuration = {
        "OPENAI_API_KEY": bool(runtime_settings.openai_api_key),
        "doubao-tts-auth": bool(
            runtime_settings.doubao_tts_api_key
            or (
                runtime_settings.doubao_tts_app_id
                and runtime_settings.doubao_tts_access_key
            )
        ),
    }

    for name, ok in required_checks.items():
        print(f"{name}: {'ok' if ok else 'missing'}")
    for name, ok in capability_checks.items():
        print(f"{name}: {'ok' if ok else 'missing'}")
    for name, configured in configuration.items():
        print(f"{name}: {'configured' if configured else 'unconfigured'}")

    # Credentials are intentionally informational: Mock mode and precomputed
    # assets remain usable without paid API access. Missing runtime packages or
    # the neural line-art model make the corresponding production path fail.
    return 0 if all((*required_checks.values(), *capability_checks.values())) else 1


def _check(fn) -> bool:
    try:
        fn()
        return True
    except Exception:  # noqa: BLE001 - a doctor check reports failure for any dependency error
        return False


def _even_dimension(value: float) -> int:
    rounded = max(2, round(value))
    return rounded - rounded % 2


def _resolve_raster_resolution(
    image_path: Path, width: int | None, height: int | None
) -> tuple[tuple[int, int], bool]:
    """Resolve a raster output size for render-photo.

    Returns the H.264-safe even resolution and whether it came directly from
    the source image dimensions.
    """

    from PIL import Image

    with Image.open(image_path) as source_image:
        source_width, source_height = source_image.size

    if width is None and height is None:
        return (_even_dimension(source_width), _even_dimension(source_height)), True
    if width is None:
        assert height is not None
        resolved_height = _even_dimension(height)
        resolved_width = _even_dimension(resolved_height * source_width / source_height)
        return (resolved_width, resolved_height), False
    if height is None:
        resolved_width = _even_dimension(width)
        resolved_height = _even_dimension(resolved_width * source_height / source_width)
        return (resolved_width, resolved_height), False
    return (_even_dimension(width), _even_dimension(height)), False


def _scene_payload(scene) -> dict[str, object]:
    if hasattr(scene, "model_dump"):
        return scene.model_dump(mode="json")
    return json.loads(scene.json())


def _analyze_image(
    image: Path, resolution: tuple[int, int], stroke_detail: str = "rich"
) -> dict[str, object]:
    if image.suffix.lower() == ".svg":
        strokes, preview = svg_to_strokes(image, resolution)
        return {
            "source": str(image),
            "mode": "svg",
            "width": preview.width,
            "height": preview.height,
            "stroke_count": len(strokes),
            "foreground_ratio": None,
            "text_regions": [],
            "color_layers": [],
        }
    strokes = to_strokes(image, resolution, stroke_detail=stroke_detail)
    return {
        "source": str(image),
        "mode": "raster-skeleton",
        "stroke_detail": stroke_detail,
        "width": resolution[0],
        "height": resolution[1],
        "stroke_count": len(strokes),
        "foreground_ratio": quality_check(image, resolution),
        "text_regions": [],
        "color_layers": [],
    }


def _normalize_lineart(
    image: Path,
    output: Path,
    threshold: int = 224,
    clear_edge: int = 6,
) -> None:
    import numpy as np
    from PIL import Image, ImageOps

    raw = Image.open(image).convert("RGB")
    gray = ImageOps.autocontrast(raw.convert("L"))
    arr = np.asarray(gray, dtype=np.uint8)
    mask = arr < max(0, min(255, threshold))
    edge = max(0, int(clear_edge))
    if edge:
        edge = min(edge, max(0, raw.width // 8), max(0, raw.height // 8))
        if edge:
            mask[:edge, :] = False
            mask[-edge:, :] = False
            mask[:, :edge] = False
            mask[:, -edge:] = False
    final = np.where(mask, 0, 255).astype(np.uint8)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(final, mode="L").convert("RGB").save(output)


def _slug(value: str) -> str:
    import re

    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff._-]+", "-", value).strip("-._")
    return slug or "whiteboard-project"


if __name__ == "__main__":
    raise SystemExit(main())
