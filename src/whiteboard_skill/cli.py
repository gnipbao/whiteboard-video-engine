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
from .preprocess import quality_check, svg_to_strokes, to_strokes
from .pipeline import run_pipeline
from .providers import get_providers
from .providers.lineart import get_lineart_provider, vectorize_with_vtracer
from .scene_split import split_script
from .whiteboard import DEFAULT_LINE_ART_SNAP_THRESHOLD, available_hands, render_image


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
        if args.command == "plan-script":
            script = args.script.read_text(encoding="utf-8")
            providers = get_providers(mock=not args.real)
            scenes = split_script(script, providers.llm, args.scenes)
            payload = [_scene_payload(scene) for scene in scenes]
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(args.output)
            return 0
        if args.command == "analyze-image":
            payload = _analyze_image(args.image, (args.width, args.height), args.stroke_detail)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
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
            lineart_path = args.lineart_output or args.output.with_name(f"{args.output.stem}-lineart.png")
            provider = get_lineart_provider(args.lineart_provider)
            provider.extract(args.image, lineart_path)
            render_input = lineart_path
            use_lineart_size = True
            if args.svg_output:
                vectorize_with_vtracer(lineart_path, args.svg_output)
                render_input = args.svg_output
                use_lineart_size = False
            if use_lineart_size:
                resolution, used_source_size = _resolve_raster_resolution(lineart_path, args.width, args.height)
            else:
                resolution, used_source_size = _resolve_raster_resolution(args.image, args.width, args.height)
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
                hand_style=args.hand,
                hand_scale=args.hand_scale,
                draw_text=args.draw_text,
                draw_text_position=args.draw_text_position,
                line_art_snap=not args.no_lineart_snap,
                line_art_snap_threshold=args.lineart_snap_threshold,
                line_thickness=args.line_thickness,
                stroke_detail=args.stroke_detail,
            )
            print(args.output)
            return 0
        if args.command == "render-image":
            resolution = (args.width, args.height)
            if args.size_from_image:
                if args.image.suffix.lower() == ".svg":
                    raise ValueError("--size-from-image is only supported for raster line-art images")
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
                hand_style=args.hand,
                hand_scale=args.hand_scale,
                draw_text=args.draw_text,
                draw_text_position=args.draw_text_position,
                line_art_snap=not args.no_lineart_snap,
                line_art_snap_threshold=args.lineart_snap_threshold,
                line_thickness=args.line_thickness,
                stroke_detail=args.stroke_detail,
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
            )
            print(args.output)
            print(f"scenes={len(project.scenes)} work_dir={settings.work_dir / _slug(args.script.stem)}")
            return 0
    except Exception as exc:
        print(f"whiteboard: {exc}", file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="whiteboard", description="Generate hand-drawn whiteboard videos from scripts or line-art images.")
    parser.add_argument("--log-level", default=settings.log_level)
    sub = parser.add_subparsers(dest="command")

    doctor = sub.add_parser("doctor", help="Check local runtime dependencies.")
    doctor.set_defaults(command="doctor")

    hands = sub.add_parser("list-hands", help="List built-in hand cursor styles.")
    hands.set_defaults(command="list-hands")

    plan = sub.add_parser("plan-script", help="Split a script into storyboard JSON.")
    plan.add_argument("script", type=Path)
    plan.add_argument("-o", "--output", type=Path, required=True)
    plan.add_argument("--scenes", type=int, default=4)
    plan.add_argument("--real", action="store_true", help="Use configured real LLM provider instead of deterministic mock planning.")
    plan.set_defaults(command="plan-script")

    analyze = sub.add_parser("analyze-image", help="Write a lightweight layer/stroke analysis JSON.")
    analyze.add_argument("image", type=Path)
    analyze.add_argument("-o", "--output", type=Path, required=True)
    analyze.add_argument("--width", type=int, default=1920)
    analyze.add_argument("--height", type=int, default=1080)
    analyze.add_argument("--stroke-detail", choices=["balanced", "rich", "max"], default="rich", help="Raster stroke extraction detail. Rich keeps short semantic details; max keeps tiny logo/facial strokes.")
    analyze.set_defaults(command="analyze-image")

    normalize = sub.add_parser("normalize-lineart", help="Normalize an existing line-art bitmap to pure black-on-white without dilation or thickening.")
    normalize.add_argument("image", type=Path)
    normalize.add_argument("-o", "--output", type=Path, required=True)
    normalize.add_argument("--threshold", type=int, default=224, help="Dark-pixel threshold for pure B/W conversion. Lower values avoid thickening anti-aliased lines.")
    normalize.add_argument("--clear-edge", type=int, default=6, help="Clear this many pixels on each canvas edge to remove generated borders.")
    normalize.set_defaults(command="normalize-lineart")

    lineart_provider_choices = ["auto", "informative", "anime2sketch", "anime", "manga"]

    extract = sub.add_parser("extract-lineart", help="Extract local line art from a color image using installed neural providers.")
    extract.add_argument("image", type=Path)
    extract.add_argument("-o", "--output", type=Path, required=True)
    extract.add_argument("--provider", choices=lineart_provider_choices, default="auto")
    extract.add_argument("--svg-output", type=Path, help="Optional SVG output via vtracer when installed.")
    extract.set_defaults(command="extract-lineart")

    photo = sub.add_parser("render-photo", help="Extract local line art from a color image and render a whiteboard MP4 in one step.")
    photo.add_argument("image", type=Path)
    photo.add_argument("-o", "--output", type=Path, required=True)
    photo.add_argument("--lineart-output", type=Path, help="Optional extracted line-art PNG path.")
    photo.add_argument("--svg-output", type=Path, help="Optional SVG output via vtracer when installed; SVG will be rendered if created.")
    photo.add_argument("--lineart-provider", choices=lineart_provider_choices, default="auto")
    photo.add_argument("--duration", type=float, default=8.0)
    photo.add_argument("--fps", type=int, default=60)
    photo.add_argument("--width", type=int, help="Output width. If omitted, render-photo uses the extracted line-art image width.")
    photo.add_argument("--height", type=int, help="Output height. If omitted, render-photo uses the extracted line-art image height.")
    photo.add_argument("--tail-color", type=float, default=2.0)
    photo.add_argument("--source-fit", choices=["exact", "blur-fill", "contain", "cover"], default="exact")
    photo.add_argument("--color-fill", choices=["contour-wipe", "brush-scan", "top-down-blocks", "fade"], default="contour-wipe")
    photo.add_argument("--color-blocks", type=int, default=18)
    photo.add_argument("--no-lineart-snap", action="store_true")
    photo.add_argument("--lineart-snap-threshold", type=int, default=DEFAULT_LINE_ART_SNAP_THRESHOLD)
    photo.add_argument("--line-thickness", type=int, default=0, help="Rendered stroke width. Use 0 to adapt to the source line art, or a positive value to override it.")
    photo.add_argument("--stroke-detail", choices=["balanced", "rich", "max"], default="rich")
    photo.add_argument("--draw-text")
    photo.add_argument("--draw-text-position", choices=["bottom", "top", "center"], default="bottom")
    photo.add_argument("--hand", default="asian")
    photo.add_argument("--hand-scale", type=float, default=1.0)
    photo.set_defaults(command="render-photo")

    render = sub.add_parser("render-image", help="Render one PNG/SVG image into a hand-drawn MP4.")
    render.add_argument("image", type=Path)
    render.add_argument("-o", "--output", type=Path, required=True)
    render.add_argument("--duration", type=float, default=8.0)
    render.add_argument("--fps", type=int, default=60)
    render.add_argument("--width", type=int, default=1920)
    render.add_argument("--height", type=int, default=1080)
    render.add_argument("--size-from-image", action="store_true", help="Use the raster line-art image size as the render canvas, adjusted to even H.264 dimensions.")
    render.add_argument("--tail-color", type=float, default=2.0)
    render.add_argument("--mode", choices=["smooth", "grid"], default="smooth", help="Compatibility option. Smooth is the maintained renderer.")
    render.add_argument("--source-image", type=Path, help="Optional original/color image used for the final color fade while drawing from the line-art image.")
    render.add_argument("--source-fit", choices=["exact", "blur-fill", "contain", "cover"], default="blur-fill", help="How to fit --source-image for the final color fill.")
    render.add_argument("--color-fill", choices=["contour-wipe", "brush-scan", "top-down-blocks", "fade"], default="contour-wipe", help="Final color fill style.")
    render.add_argument("--color-blocks", type=int, default=18, help="Number of horizontal blocks used by top-down color fill.")
    render.add_argument("--no-lineart-snap", action="store_true", help="Disable snapping to the original complete line-art image before color fill.")
    render.add_argument("--lineart-snap-threshold", type=int, default=DEFAULT_LINE_ART_SNAP_THRESHOLD, help="Threshold used by line-art snap. Lower avoids thickening/noise from gray pixels.")
    render.add_argument("--line-thickness", type=int, default=0, help="Rendered stroke width. Use 0 to adapt to the source line art, or a positive value to override it.")
    render.add_argument("--stroke-detail", choices=["balanced", "rich", "max"], default="rich", help="Raster stroke extraction detail. Rich keeps short semantic details; max keeps tiny logo/facial strokes.")
    render.add_argument("--draw-text", help="Append a short hand-drawn text title after the image strokes, for example: --draw-text '温馨的一家'.")
    render.add_argument("--draw-text-position", choices=["bottom", "top", "center"], default="bottom", help="Placement for --draw-text.")
    render.add_argument("--hand", default="asian", help="Hand cursor: asian (default), black, children, white, procedural, none, or a custom PNG/WebP path.")
    render.add_argument("--hand-scale", type=float, default=1.0)
    render.set_defaults(command="render-image")

    compose = sub.add_parser("compose", help="Concatenate rendered MP4 scene clips.")
    compose.add_argument("videos", nargs="+", type=Path)
    compose.add_argument("-o", "--output", type=Path, required=True)
    compose.set_defaults(command="compose")

    run = sub.add_parser("run", help="Run full script-to-video pipeline.")
    run.add_argument("script", type=Path)
    run.add_argument("-o", "--output", type=Path, required=True)
    run.add_argument("--scenes", type=int, default=4)
    run.add_argument("--fps", type=int, default=60)
    run.add_argument("--width", type=int, default=1920)
    run.add_argument("--height", type=int, default=1080)
    run.add_argument("--tail-color", type=float, default=2.0)
    run.add_argument("--voice", default="zh-CN-XiaoxiaoNeural")
    run.add_argument("--resume", action="store_true")
    run.add_argument("--mock", action="store_true")
    run.add_argument("--hand", default="asian", help="Hand cursor: asian (default), black, children, white, procedural, none, or a custom PNG/WebP path.")
    run.add_argument("--hand-scale", type=float, default=1.0)
    run.set_defaults(command="run")
    return parser


def _doctor() -> int:
    checks = {
        "ffmpeg": _check(lambda: ffmpeg_path()),
        "numpy": _check(lambda: __import__("numpy")),
        "Pillow": _check(lambda: __import__("PIL")),
        "pydantic": _check(lambda: __import__("pydantic")),
    }
    for name, ok in checks.items():
        print(f"{name}: {'ok' if ok else 'missing'}")
    return 0 if all(checks.values()) else 1


def _check(fn) -> bool:
    try:
        fn()
        return True
    except Exception:
        return False


def _even_dimension(value: float | int) -> int:
    rounded = max(2, int(round(value)))
    return rounded - rounded % 2


def _resolve_raster_resolution(image_path: Path, width: int | None, height: int | None) -> tuple[tuple[int, int], bool]:
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


def _analyze_image(image: Path, resolution: tuple[int, int], stroke_detail: str = "rich") -> dict[str, object]:
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
    from PIL import Image, ImageOps
    import numpy as np

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
