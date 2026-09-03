"""Stroke-level whiteboard renderer."""

from __future__ import annotations

import bisect
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .models import Annotation, TextRole, TimingCue
from .preprocess import (
    Stroke,
    StrokeBlock,
    group_strokes_into_blocks,
    load_on_canvas,
    postprocess_strokes,
    svg_to_strokes,
    to_strokes,
    trace_8connected,
    zhang_suen_skeleton,
)

BUILTIN_HANDS = ("asian", "black", "children", "white")
HAND_ANCHORS: dict[str, tuple[float, float]] = {
    "asian": (0.04, 0.28),
    "black": (0.05, 0.30),
    "children": (0.16, 0.15),
    "white": (0.04, 0.35),
}
SKETCH_INK_COLOR = (64, 60, 62)
SKETCH_INK_OPACITY = 0.76


@dataclass(frozen=True)
class HandCursor:
    """Prepared hand cursor image and its pen-tip anchor."""

    image: Image.Image
    anchor: tuple[float, float]


@dataclass
class TimelineStroke:
    """Stroke geometry mapped onto the render timeline."""

    stroke: Stroke
    cumulative: list[float]
    length: float
    start_unit: float
    end_unit: float
    pause_end_unit: float


@dataclass(frozen=True)
class ContourFillCache:
    """Precomputed field used by contour-aware color fill."""

    resistance: np.ndarray
    rows: np.ndarray
    wave: np.ndarray


@dataclass(frozen=True)
class CrayonFillCache:
    """Stable fields used by the block-local left-to-right crayon reveal."""

    columns: np.ndarray
    wave: np.ndarray
    grain: np.ndarray
    foreground: np.ndarray
    source: np.ndarray
    textured_source: np.ndarray


@dataclass
class BlockRenderState:
    """Mutable timelines and color mask for one sequential drawing block."""

    block: StrokeBlock
    coarse_timeline: list[TimelineStroke]
    detail_timeline: list[TimelineStroke]
    coarse_progress: list[float]
    detail_progress: list[float]
    region_mask: np.ndarray


@dataclass(frozen=True)
class TextLayout:
    """Rasterized multiline text and the bounds of each rendered line."""

    mask: Image.Image
    lines: tuple[str, ...]
    line_boxes: tuple[tuple[int, int, int, int], ...]
    glyph_boxes: tuple[tuple[int, int, int, int], ...]
    font_size: int


@dataclass(frozen=True)
class AnnotationRenderState:
    """Late overlay timing and glyph geometry for one short label."""

    layout: TextLayout
    start: float
    end: float


FONT_CANDIDATES = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)


def ease_in_out_sine(t: float) -> float:
    """Return sine ease-in-out progress.

    Example:
        >>> round(ease_in_out_sine(0.5), 2)
        0.5
    """

    return -(math.cos(math.pi * max(0.0, min(1.0, t))) - 1) / 2


def segment_angle(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Return segment angle in radians."""

    return math.atan2(b[1] - a[1], b[0] - a[0])


def _ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH")
    return ffmpeg


def _resize_cover(image: Image.Image, resolution: tuple[int, int]) -> Image.Image:
    width, height = resolution
    src = image.convert("RGB")
    scale = max(width / src.width, height / src.height)
    resized = src.resize((max(1, int(round(src.width * scale))), max(1, int(round(src.height * scale)))), Image.Resampling.LANCZOS)
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _resize_contain(image: Image.Image, resolution: tuple[int, int], background: Image.Image | None = None) -> Image.Image:
    width, height = resolution
    src = image.convert("RGB")
    src.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas = background.copy() if background else Image.new("RGB", resolution, "white")
    canvas.paste(src, ((width - src.width) // 2, (height - src.height) // 2))
    return canvas


def _load_source_image_canvas(image_path: Path, resolution: tuple[int, int], fit: str = "blur-fill") -> Image.Image:
    """Load the real color source for the final fill stage."""

    source = Image.open(image_path).convert("RGB")
    if fit == "exact":
        return source.resize(resolution, Image.Resampling.LANCZOS)
    if fit == "cover":
        return _resize_cover(source, resolution)
    if fit == "contain":
        return _resize_contain(source, resolution)
    background = _resize_cover(source, resolution).filter(ImageFilter.GaussianBlur(radius=max(8, min(resolution) // 26)))
    background = Image.blend(background, Image.new("RGB", resolution, "white"), 0.12)
    return _resize_contain(source, resolution, background=background)


def _line_art_canvas(image_path: Path, resolution: tuple[int, int]) -> Image.Image:
    """Load source line art as a same-size white canvas."""

    return load_on_canvas(image_path, resolution)


DEFAULT_LINE_ART_SNAP_THRESHOLD = 235


def _dilate_bool_mask(mask: np.ndarray, radius: int = 1) -> np.ndarray:
    if radius <= 0 or mask.size == 0:
        return mask.copy()
    h, w = mask.shape
    padded = np.pad(mask, radius, mode="constant", constant_values=False)
    out = np.zeros_like(mask, dtype=bool)
    span = radius * 2 + 1
    for dy in range(span):
        for dx in range(span):
            out |= padded[dy : dy + h, dx : dx + w]
    return out


def _line_art_ink_mask(line_art: Image.Image, size: tuple[int, int], threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD) -> Image.Image:
    source = line_art.convert("RGB")
    if source.size != size:
        source = source.resize(size, Image.Resampling.LANCZOS)
    gray = np.asarray(source.convert("L"), dtype=np.uint8)
    raw_mask = gray < threshold
    if not np.any(raw_mask):
        return Image.new("L", size, 0)

    skel = zhang_suen_skeleton(raw_mask)
    skeleton_pixels = int(np.count_nonzero(skel))
    if skeleton_pixels <= 0:
        return Image.fromarray(raw_mask.astype(np.uint8) * 255, mode="L")

    estimated_width = float(np.count_nonzero(raw_mask)) / skeleton_pixels
    if estimated_width <= 2.2:
        return Image.fromarray(raw_mask.astype(np.uint8) * 255, mode="L")

    halo = _dilate_bool_mask(skel, radius=1)
    soft_mask = np.zeros_like(gray, dtype=np.uint8)
    soft_mask[halo] = 110
    soft_mask[skel] = 255
    return Image.fromarray(soft_mask, mode="L")


def _line_art_binary_mask(line_art: Image.Image, threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD) -> np.ndarray:
    gray = np.asarray(line_art.convert("L"), dtype=np.uint8)
    return gray < threshold


def _line_art_tone_layer(line_art: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Build a softly tinted ink layer while preserving pencil lightness."""

    source = line_art.convert("L")
    if source.size != size:
        source = source.resize(size, Image.Resampling.LANCZOS)
    gray_2d = np.asarray(source, dtype=np.uint8)
    ink_values = gray_2d[gray_2d < 255]
    gray = gray_2d.astype(np.float32)[..., None]
    if len(np.unique(ink_values)) >= 16:
        # Anime2Sketch places most useful pencil values in the upper half of
        # the range. Normalize that cleaned range into a visible, but still
        # varied, charcoal band rather than letting thin contours disappear.
        normalized = np.clip(gray / 224.0, 0.0, 1.0) ** 1.35
        darkest = np.asarray((82, 78, 80), dtype=np.float32)[None, None, :]
        lightest = np.asarray((200, 198, 199), dtype=np.float32)[None, None, :]
        toned = darkest + (lightest - darkest) * normalized
        toned[gray_2d == 255] = 255
    else:
        # Keep the established appearance for binary Informative Drawings and
        # hand-authored black line art.
        normalized = gray / 255.0
        darkest = np.asarray(SKETCH_INK_COLOR, dtype=np.float32)[None, None, :]
        pencil = darkest + (255.0 - darkest) * normalized
        opacity_byte = int(255 * SKETCH_INK_OPACITY)
        toned = (
            pencil * opacity_byte + 255.0 * (255 - opacity_byte) + 127.0
        ) / 255.0
    return Image.fromarray(np.clip(toned, 0, 255).astype(np.uint8), mode="RGB")


def _estimate_line_art_width(line_art: Image.Image | None, threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD) -> int:
    """Estimate the visible line width of a raster line-art image."""

    if line_art is None:
        return 2
    mask = _line_art_binary_mask(line_art, threshold=threshold)
    if not np.any(mask):
        return 2
    skel = zhang_suen_skeleton(mask)
    skeleton_pixels = int(np.count_nonzero(skel))
    if skeleton_pixels <= 0:
        return 2
    estimated = float(np.count_nonzero(mask)) / skeleton_pixels
    return max(2, min(7, int(round(estimated))))


def _resolve_line_thickness(requested: int | None, estimated_line_width: int) -> int:
    """Resolve an explicit stroke width or adapt it to the source line art."""

    if requested is None or int(requested) <= 0:
        return max(1, min(7, int(round(estimated_line_width))))
    return max(1, int(requested))


def _combine_masks(a: Image.Image, b: Image.Image) -> Image.Image:
    arr = np.minimum(np.asarray(a.convert("L"), dtype=np.uint8), np.asarray(b.convert("L"), dtype=np.uint8))
    return Image.fromarray(arr, mode="L")


def _complete_line_art_canvas(
    canvas: Image.Image,
    line_art: Image.Image | None,
    threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD,
    alpha: float = 1.0,
    ink_mask: Image.Image | None = None,
    ink_layer: Image.Image | None = None,
) -> Image.Image:
    """Composite missing black line-art pixels over a redrawn stroke canvas."""

    if line_art is None and ink_mask is None:
        return canvas.copy()
    base = canvas.convert("RGB")
    mask = ink_mask if ink_mask is not None else _line_art_ink_mask(line_art, base.size, threshold=threshold)
    if mask.size != base.size:
        mask = mask.resize(base.size, Image.Resampling.NEAREST)
    registered_ink = ink_layer
    if registered_ink is None and line_art is not None:
        registered_ink = _line_art_tone_layer(line_art, base.size)
    opacity = max(0.0, min(1.0, alpha))
    if registered_ink is None:
        opacity *= SKETCH_INK_OPACITY
    mask = mask.point(lambda px: int(px * opacity))
    ink = registered_ink or Image.new("RGB", base.size, SKETCH_INK_COLOR)
    if ink.size != base.size:
        ink = ink.resize(base.size, Image.Resampling.LANCZOS)
    if registered_ink is not None:
        ink = ImageChops.darker(base, ink)
    base.paste(ink, mask=mask)
    return base


def _reveal_line_art_canvas(
    canvas: Image.Image,
    line_art: Image.Image | None,
    reveal_mask: Image.Image | None,
    threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD,
    ink_mask: Image.Image | None = None,
    ink_layer: Image.Image | None = None,
) -> Image.Image:
    if reveal_mask is None or (line_art is None and ink_mask is None):
        return canvas.copy()
    base = canvas.convert("RGB")
    mask = ink_mask if ink_mask is not None else _line_art_ink_mask(line_art, base.size, threshold=threshold)
    if mask.size != base.size:
        mask = mask.resize(base.size, Image.Resampling.NEAREST)
    registered_ink = ink_layer
    if registered_ink is None and line_art is not None:
        registered_ink = _line_art_tone_layer(line_art, base.size)
    reveal = _combine_masks(mask, reveal_mask)
    if registered_ink is None:
        reveal = reveal.point(lambda px: int(px * SKETCH_INK_OPACITY))
    ink = registered_ink or Image.new("RGB", base.size, SKETCH_INK_COLOR)
    if ink.size != base.size:
        ink = ink.resize(base.size, Image.Resampling.LANCZOS)
    if registered_ink is not None:
        ink = ImageChops.darker(base, ink)
    base.paste(ink, mask=reveal)
    return base


def _load_text_font(size: int, font_path: Path | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if font_path is not None:
        try:
            return ImageFont.truetype(str(font_path), size=size)
        except OSError as exc:
            raise ValueError(f"Unable to load text font: {font_path}") from exc
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=max(10, size))


_CJK_TOKEN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[^\s\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[ \t]+")


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    if not text:
        return 0
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font, anchor="lt")
    return max(0, right - left)


def _wrap_text_lines(
    text: str,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    """Wrap explicit paragraphs while allowing Chinese to break character by character."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not normalized.strip():
        return []
    lines: list[str] = []
    for paragraph in normalized.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for token in _CJK_TOKEN_RE.findall(paragraph):
            candidate = current + token
            if current and _text_width(draw, candidate.rstrip(), font) > max_width:
                lines.append(current.rstrip())
                current = token.lstrip()
            else:
                current = candidate
            while current and _text_width(draw, current.rstrip(), font) > max_width:
                split_at = max(1, len(current) - 1)
                while split_at > 1 and _text_width(draw, current[:split_at].rstrip(), font) > max_width:
                    split_at -= 1
                lines.append(current[:split_at].rstrip())
                current = current[split_at:].lstrip()
        lines.append(current.rstrip())
    return lines


def _layout_text(
    text: str,
    resolution: tuple[int, int],
    position: str = "bottom",
    *,
    align: str = "center",
    width_ratio: float = 0.82,
    max_height_ratio: float = 0.36,
    line_spacing: float = 0.25,
    font_size: int | None = None,
    font_path: Path | None = None,
    origin: tuple[float, float] | None = None,
) -> TextLayout:
    """Lay out multiline text into a canvas-sized ink mask."""

    if position not in {"top", "center", "bottom"}:
        raise ValueError(f"Unsupported text position: {position}")
    if align not in {"left", "center", "right"}:
        raise ValueError(f"Unsupported text alignment: {align}")
    if not 0.1 <= width_ratio <= 1.0:
        raise ValueError("Text width ratio must be between 0.1 and 1.0")
    if not 0.1 <= max_height_ratio <= 1.0:
        raise ValueError("Text max-height ratio must be between 0.1 and 1.0")
    if line_spacing < 0:
        raise ValueError("Text line spacing must be non-negative")
    if origin is not None and not all(0.0 <= coordinate <= 1.0 for coordinate in origin):
        raise ValueError("Text origin coordinates must be between 0.0 and 1.0")

    width, height = resolution
    max_width = max(1, int(round(width * width_ratio)))
    max_height = max(1, int(round(height * max_height_ratio)))
    origin_px: tuple[int, int] | None = None
    if origin is not None:
        right_margin = max(8, round(width * 0.02))
        origin_px = (
            min(round(origin[0] * width), max(0, width - max_width - right_margin)),
            round(origin[1] * height),
        )
    probe = Image.new("L", resolution, 0)
    probe_draw = ImageDraw.Draw(probe)
    if font_size is not None and font_size < 8:
        raise ValueError("Text font size must be at least 8 pixels")
    start = font_size or int(max(20, min(height * 0.075, width * 0.13)))
    sizes = [font_size] if font_size is not None else list(range(start, 11, -2))

    chosen: tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, list[str], int, int] | None = None
    for size in sizes:
        assert size is not None
        font = _load_text_font(size, font_path)
        lines = _wrap_text_lines(text, probe_draw, font, max_width)
        if not lines:
            return TextLayout(Image.new("L", resolution, 0), (), (), (), size)
        _left, _top, _right, sample_bottom = probe_draw.textbbox((0, 0), "国Ag", font=font, anchor="lt")
        line_height = max(1, sample_bottom)
        spacing_px = max(0, int(round(size * line_spacing)))
        total_height = line_height * len(lines) + spacing_px * max(0, len(lines) - 1)
        if total_height <= max_height:
            chosen = (font, lines, line_height, spacing_px)
            break
    if chosen is None:
        raise ValueError("Text does not fit the requested layout; increase --draw-text-max-height or reduce --draw-text-font-size")

    font, lines, line_height, spacing_px = chosen
    actual_size = int(getattr(font, "size", font_size or 12))
    total_height = line_height * len(lines) + spacing_px * max(0, len(lines) - 1)
    margin_y = max(12, int(round(height * 0.045)))
    if origin_px is not None:
        start_y = min(origin_px[1], max(0, height - total_height - margin_y))
    elif position == "top":
        start_y = margin_y
    elif position == "center":
        start_y = int(round((height - total_height) / 2))
    else:
        start_y = height - margin_y - total_height
    region_left = (
        origin_px[0]
        if origin_px is not None
        else int(round((width - max_width) / 2))
    )
    region_right = region_left + max_width

    mask = Image.new("L", resolution, 0)
    draw = ImageDraw.Draw(mask)
    boxes: list[tuple[int, int, int, int]] = []
    glyph_boxes: list[tuple[int, int, int, int]] = []
    for index, line in enumerate(lines):
        y = start_y + index * (line_height + spacing_px)
        line_width = _text_width(draw, line, font)
        if align == "left":
            x = region_left
        elif align == "right":
            x = region_right - line_width
        else:
            x = int(round((width - line_width) / 2))
        if line:
            draw.text((x, y), line, font=font, fill=255, anchor="lt")
        for glyph_index, glyph in enumerate(line):
            if glyph.isspace():
                continue
            glyph_left = x + _text_width(draw, line[:glyph_index], font)
            glyph_right = x + _text_width(draw, line[: glyph_index + 1], font)
            if glyph_right > glyph_left:
                glyph_boxes.append(
                    (glyph_left, y, glyph_right, y + line_height)
                )
        boxes.append((x, y, x + line_width, y + line_height))
    return TextLayout(
        mask,
        tuple(lines),
        tuple(boxes),
        tuple(glyph_boxes),
        actual_size,
    )


def _text_to_strokes(
    text: str,
    resolution: tuple[int, int],
    position: str = "bottom",
    *,
    align: str = "center",
    width_ratio: float = 0.82,
    max_height_ratio: float = 0.36,
    line_spacing: float = 0.25,
    font_size: int | None = None,
    font_path: Path | None = None,
) -> list[Stroke]:
    """Convert laid-out text into drawable centerline strokes."""

    layout = _layout_text(
        text,
        resolution,
        position,
        align=align,
        width_ratio=width_ratio,
        max_height_ratio=max_height_ratio,
        line_spacing=line_spacing,
        font_size=font_size,
        font_path=font_path,
    )
    mask = np.asarray(layout.mask, dtype=np.uint8) > 0
    if not np.any(mask):
        return []
    skel = zhang_suen_skeleton(mask)
    paths = trace_8connected(skel, min_points=4)
    strokes = [
        Stroke(points=[(float(x), float(y)) for x, y in path], source="text")
        for path in paths
    ]
    return postprocess_strokes(strokes, resolution, smooth=True, merge=True)


def _text_wipe_strokes(layout: TextLayout) -> list[Stroke]:
    """Create one left-to-right reveal path for each visible text line."""

    strokes: list[Stroke] = []
    for left, top, right, bottom in layout.line_boxes:
        if right <= left or bottom <= top:
            continue
        reveal_width = max(2, bottom - top + 4)
        pad = reveal_width / 2
        y = (top + bottom) / 2
        strokes.append(
            Stroke(
                points=[(left - pad, y), (right + pad, y)],
                source="text-wipe",
                reveal_width=reveal_width,
            )
        )
    return strokes


def _annotation_typewriter_mask(
    layout: TextLayout,
    progress: float,
) -> Image.Image:
    """Reveal complete glyphs in reading order with a short nib-like sweep."""

    reveal = Image.new("L", layout.mask.size, 0)
    glyphs = layout.glyph_boxes
    if not glyphs or progress <= 0:
        return reveal
    if progress >= 1:
        return Image.new("L", layout.mask.size, 255)

    scaled = max(0.0, min(1.0, progress)) * len(glyphs)
    completed = min(len(glyphs), int(scaled))
    draw = ImageDraw.Draw(reveal)
    for left, top, right, bottom in glyphs[:completed]:
        draw.rectangle((left - 1, top - 1, right + 1, bottom + 1), fill=255)
    if completed < len(glyphs):
        left, top, right, bottom = glyphs[completed]
        local = ease_in_out_sine(scaled - completed)
        lead = left + (right - left + 2) * local
        nib_slant = max(1.0, layout.font_size * 0.055)
        draw.polygon(
            [
                (left - 1, top - 1),
                (lead - nib_slant, top - 1),
                (lead + nib_slant, bottom + 1),
                (left - 1, bottom + 1),
            ],
            fill=255,
        )
    return reveal


def _annotation_cursor_pose(
    layout: TextLayout,
    progress: float,
) -> tuple[tuple[float, float], float] | None:
    """Return the writing-tip pose for the glyph currently being revealed."""

    glyphs = layout.glyph_boxes
    if not glyphs or progress <= 0 or progress >= 1:
        return None
    scaled = max(0.0, min(1.0, progress)) * len(glyphs)
    index = min(len(glyphs) - 1, int(scaled))
    local = ease_in_out_sine(scaled - index)
    left, top, right, bottom = glyphs[index]
    x = left + (right - left) * local
    y = top + (bottom - top) * (0.48 + 0.08 * math.sin(local * math.pi))
    return ((float(x), float(y)), 0.0)


def _layout_annotation(
    annotation: Annotation,
    resolution: tuple[int, int],
    font_path: Path | None = None,
) -> TextLayout:
    """Lay out one concise annotation near its normalized scene position."""

    font_size = max(18, min(46, round(resolution[1] * 0.043)))
    return _layout_text(
        annotation.text,
        resolution,
        "top",
        align="left",
        width_ratio=0.30,
        max_height_ratio=0.14,
        line_spacing=0.12,
        font_size=font_size,
        font_path=font_path,
        origin=(annotation.x, annotation.y),
    )


def _annotation_render_states(
    layouts: list[TextLayout],
    total_frames: int,
    fps: int,
    animation_end: float,
) -> list[AnnotationRenderState]:
    """Schedule sparse annotations late, after the scene outline is readable."""

    if not layouts:
        return []
    video_duration = total_frames / max(1, fps)
    durations = [
        max(0.3, min(0.6, len(layout.glyph_boxes) * 0.075)) / video_duration
        for layout in layouts
    ]
    gap = min(0.08 / video_duration, animation_end * 0.01)
    total_span = sum(durations) + gap * max(0, len(durations) - 1)
    latest_end = animation_end * 0.99
    preferred_start = animation_end * 0.94
    earliest_start = animation_end * 0.85
    cursor = max(
        earliest_start,
        min(preferred_start, latest_end - total_span),
    )
    states: list[AnnotationRenderState] = []
    for layout, duration in zip(layouts, durations, strict=True):
        end = min(animation_end, cursor + duration)
        states.append(AnnotationRenderState(layout=layout, start=cursor, end=end))
        cursor = end + gap
    return states


def _draw_cursor(frame: Image.Image, x: float, y: float, angle: float, scale: float = 0.85) -> None:
    draw = ImageDraw.Draw(frame, "RGBA")
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux

    def pt(back: float, side: float) -> tuple[float, float]:
        return (x - ux * back + nx * side, y - uy * back + ny * side)

    draw.line([pt(55 * scale, 0), (x, y)], fill=(20, 20, 20, 255), width=max(4, int(7 * scale)))
    draw.polygon([pt(7 * scale, -5 * scale), (x, y), pt(7 * scale, 5 * scale)], fill=(8, 8, 8, 255))
    draw.polygon([pt(50 * scale, 11 * scale), pt(96 * scale, 18 * scale), pt(104 * scale, 45 * scale), pt(46 * scale, 34 * scale)], fill=(239, 191, 147, 235))
    draw.polygon([pt(42 * scale, 20 * scale), pt(70 * scale, 24 * scale), pt(74 * scale, 52 * scale), pt(35 * scale, 44 * scale)], fill=(246, 204, 164, 245))


def available_hands() -> tuple[str, ...]:
    """Return valid hand cursor names."""

    return ("procedural", "none", *BUILTIN_HANDS)


def _hand_asset_path(hand: str) -> Path:
    filename = f"{hand}.png"
    package_path = Path(__file__).resolve().parent / "assets" / "hands" / filename
    if package_path.exists():
        return package_path
    skill_path = Path(__file__).resolve().parents[2] / "assets" / "hands" / filename
    if skill_path.exists():
        return skill_path
    raise ValueError(f"Unknown or missing hand asset: {hand}")


def _load_hand_cursor(hand: str | Path, resolution: tuple[int, int], hand_scale: float) -> HandCursor:
    name = str(hand)
    if name in BUILTIN_HANDS:
        image_path = _hand_asset_path(name)
        anchor_ratio = HAND_ANCHORS[name]
    else:
        image_path = Path(name).expanduser()
        if not image_path.exists():
            raise ValueError(f"Hand asset does not exist: {image_path}")
        anchor_ratio = (0.05, 0.30)

    image = Image.open(image_path).convert("RGBA")
    scale_factor = max(0.1, hand_scale) * max(0.35, min(resolution) / 640.0)
    if abs(scale_factor - 1.0) > 0.01:
        image = image.resize((max(1, int(image.width * scale_factor)), max(1, int(image.height * scale_factor))), Image.Resampling.LANCZOS)
    anchor = (image.width * anchor_ratio[0], image.height * anchor_ratio[1])
    return HandCursor(image=image, anchor=anchor)


def _paste_hand_cursor(frame: Image.Image, x: float, y: float, cursor: HandCursor) -> Image.Image:
    dest = (int(round(x - cursor.anchor[0])), int(round(y - cursor.anchor[1])))
    rgba = frame.convert("RGBA")
    rgba.alpha_composite(cursor.image, dest)
    return rgba.convert("RGB")


def _stroke_cumulative(points: list[tuple[float, float]]) -> list[float]:
    cumulative = [0.0]
    total = 0.0
    for a, b in zip(points, points[1:], strict=False):
        total += math.hypot(b[0] - a[0], b[1] - a[1])
        cumulative.append(total)
    return cumulative


def _point_to_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Return the shortest distance from ``point`` to a line segment."""

    vx, vy = end[0] - start[0], end[1] - start[1]
    length_sq = vx * vx + vy * vy
    if length_sq <= 1e-9:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    projection = (
        (point[0] - start[0]) * vx + (point[1] - start[1]) * vy
    ) / length_sq
    projection = max(0.0, min(1.0, projection))
    nearest = (start[0] + vx * projection, start[1] + vy * projection)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


def _simplify_stroke_path(
    points: list[tuple[float, float]],
    tolerance: float,
) -> list[tuple[float, float]]:
    """Simplify a path while keeping endpoints and meaningful corners."""

    if len(points) <= 2 or tolerance <= 0:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    pending = [(0, len(points) - 1)]
    while pending:
        start_index, end_index = pending.pop()
        start, end = points[start_index], points[end_index]
        farthest_index = -1
        farthest_distance = 0.0
        for index in range(start_index + 1, end_index):
            distance = _point_to_segment_distance(points[index], start, end)
            if distance > farthest_distance:
                farthest_distance = distance
                farthest_index = index
        if farthest_index >= 0 and farthest_distance > tolerance:
            keep[farthest_index] = True
            pending.append((start_index, farthest_index))
            pending.append((farthest_index, end_index))
    return [point for index, point in enumerate(points) if keep[index]]


def _naturalize_stroke_path(
    points: list[tuple[float, float]],
    resolution: tuple[int, int],
) -> list[tuple[float, float]]:
    """Remove raster stair-steps and tiny hairpins without moving real corners.

    The maximum simplification error stays close to one output pixel.  A broad
    reveal brush later intersects this guide path with the original line-art
    mask, so the guide may be smooth while the final contour remains exact.
    """

    if len(points) <= 2:
        return list(points)
    min_side = min(resolution)
    duplicate_gap = max(0.35, min(0.75, min_side * 0.00045))
    hairpin_gap = max(1.5, min(2.4, min_side * 0.0017))
    tolerance = max(0.45, min(1.15, min_side * 0.00085))

    deduped = [points[0]]
    for point in points[1:]:
        if math.hypot(point[0] - deduped[-1][0], point[1] - deduped[-1][1]) >= duplicate_gap:
            deduped.append(point)
    if deduped[-1] != points[-1]:
        deduped.append(points[-1])

    # Skeleton tracers occasionally produce A-B-A or A-B-C micro-spurs at
    # junctions.  Remove only local returns small enough to be pixel noise.
    unhooked: list[tuple[float, float]] = []
    for point in deduped:
        if len(unhooked) >= 2:
            return_gap = math.hypot(
                point[0] - unhooked[-2][0],
                point[1] - unhooked[-2][1],
            )
            outgoing = math.hypot(
                unhooked[-1][0] - unhooked[-2][0],
                unhooked[-1][1] - unhooked[-2][1],
            )
            if return_gap <= hairpin_gap and outgoing <= hairpin_gap * 1.8:
                unhooked.pop()
                if unhooked and math.hypot(
                    point[0] - unhooked[-1][0],
                    point[1] - unhooked[-1][1],
                ) < duplicate_gap:
                    continue
        unhooked.append(point)
    if len(unhooked) <= 2:
        return unhooked

    simplified = _simplify_stroke_path(unhooked, tolerance)
    if len(simplified) <= 2:
        return simplified

    # Light corner-aware averaging removes the last one-pixel stair steps. A
    # 70-degree or sharper corner is preserved so faces and architecture do not
    # become inflated, generic curves.
    natural = [simplified[0]]
    for previous, current, following in zip(
        simplified,
        simplified[1:-1],
        simplified[2:],
        strict=False,
    ):
        incoming = (current[0] - previous[0], current[1] - previous[1])
        outgoing = (following[0] - current[0], following[1] - current[1])
        denominator = math.hypot(*incoming) * math.hypot(*outgoing)
        cosine = (
            (incoming[0] * outgoing[0] + incoming[1] * outgoing[1])
            / denominator
            if denominator > 1e-9
            else -1.0
        )
        short_corner = max(math.hypot(*incoming), math.hypot(*outgoing)) <= max(
            2.2,
            tolerance * 3.2,
        )
        if short_corner:
            weight = 0.22
        else:
            weight = 0.0 if cosine < 0.34 else min(0.14, (cosine - 0.34) * 0.20)
        natural.append(
            (
                current[0] * (1.0 - 2.0 * weight)
                + (previous[0] + following[0]) * weight,
                current[1] * (1.0 - 2.0 * weight)
                + (previous[1] + following[1]) * weight,
            )
        )
    natural.append(simplified[-1])
    return _simplify_stroke_path(natural, tolerance * 0.75)


def _naturalize_story_strokes(
    strokes: list[Stroke],
    resolution: tuple[int, int],
) -> list[Stroke]:
    """Prepare raster guides for stable block drawing without losing detail."""

    minimum_fragment = max(2.5, min(4.0, min(resolution) * 0.003))
    natural: list[Stroke] = []
    for stroke in strokes:
        if len(stroke.points) < 2:
            continue
        is_raster = not stroke.source.startswith(("svg", "text"))
        points = (
            _naturalize_stroke_path(stroke.points, resolution)
            if is_raster
            else list(stroke.points)
        )
        if len(points) < 2:
            continue
        length = _stroke_cumulative(points)[-1]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        if is_raster and length < minimum_fragment and diagonal < minimum_fragment * 0.8:
            continue
        natural.append(
            Stroke(
                points=points,
                color=stroke.color,
                source=f"{stroke.source}+natural" if is_raster else stroke.source,
                reveal_width=stroke.reveal_width,
            )
        )
    return natural


def _spread_pause_indices(stroke_count: int, pause_count: int) -> set[int]:
    if stroke_count <= 1 or pause_count <= 0:
        return set()
    return {
        min(stroke_count - 2, max(0, round((idx + 1) * (stroke_count - 1) / (pause_count + 1))))
        for idx in range(pause_count)
    }


def _prepare_timeline(strokes: list[Stroke], draw_frames: int) -> list[TimelineStroke]:
    prepared: list[tuple[Stroke, list[float], float]] = []
    for stroke in strokes:
        if len(stroke.points) < 2:
            continue
        cumulative = _stroke_cumulative(stroke.points)
        if cumulative[-1] > 0:
            prepared.append((stroke, cumulative, cumulative[-1]))
    total_length = sum(length for _, _, length in prepared)
    if total_length <= 0:
        return []

    strokes_per_frame = len(prepared) / max(1, draw_frames)
    pixels_per_frame = total_length / max(1, draw_frames)
    if strokes_per_frame >= 0.85 or pixels_per_frame >= 85:
        pause_ratio = 0.0
    elif strokes_per_frame >= 0.45 or pixels_per_frame >= 55:
        pause_ratio = 0.008
    else:
        pause_ratio = 0.03
    pause_count = min(max(0, int(round(draw_frames * pause_ratio))), max(0, len(prepared) - 1))
    pause_indices = _spread_pause_indices(len(prepared), pause_count)
    pause_unit = total_length / max(1, draw_frames - pause_count)
    cursor = 0.0
    timeline: list[TimelineStroke] = []
    for index, (stroke, cumulative, length) in enumerate(prepared):
        start = cursor
        end = start + length
        cursor = end + (pause_unit if index in pause_indices else 0.0)
        timeline.append(TimelineStroke(stroke=stroke, cumulative=cumulative, length=length, start_unit=start, end_unit=end, pause_end_unit=cursor))
    return timeline


def _point_at_distance(points: list[tuple[float, float]], cumulative: list[float], distance: float) -> tuple[float, float]:
    if distance <= 0:
        return points[0]
    if distance >= cumulative[-1]:
        return points[-1]
    index = max(0, min(len(points) - 2, bisect.bisect_right(cumulative, distance) - 1))
    start, end = points[index], points[index + 1]
    seg_len = max(cumulative[index + 1] - cumulative[index], 1e-6)
    local = (distance - cumulative[index]) / seg_len
    return (start[0] + (end[0] - start[0]) * local, start[1] + (end[1] - start[1]) * local)


def _stroke_segment_between(
    points: list[tuple[float, float]],
    cumulative: list[float],
    start_distance: float,
    end_distance: float,
) -> list[tuple[float, float]]:
    start_distance = max(0.0, min(cumulative[-1], start_distance))
    end_distance = max(start_distance, min(cumulative[-1], end_distance))
    if end_distance <= start_distance:
        return []
    segment = [_point_at_distance(points, cumulative, start_distance)]
    for index, distance in enumerate(cumulative[1:-1], start=1):
        if start_distance < distance < end_distance:
            segment.append(points[index])
    segment.append(_point_at_distance(points, cumulative, end_distance))
    deduped: list[tuple[float, float]] = []
    for point in segment:
        if not deduped or math.hypot(point[0] - deduped[-1][0], point[1] - deduped[-1][1]) > 0.05:
            deduped.append(point)
    return deduped


def _line_width(base_width: int, progress: float, scale: int) -> int:
    pressure = 0.68 + 0.42 * math.sin(math.pi * max(0.0, min(1.0, progress)))
    return max(1, int(round(base_width * pressure * scale)))


def _draw_stroke_segment(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    color: tuple[int, int, int],
    width: int,
    scale: int,
) -> None:
    if len(points) < 2:
        return
    scaled = [(x * scale, y * scale) for x, y in points] if scale != 1 else points
    draw.line(scaled, fill=color, width=width, joint="curve")
    radius = max(1, width // 2)
    for x, y in (scaled[0], scaled[-1]):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def _draw_reveal_mask_segment(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    width: int,
) -> None:
    if len(points) < 2:
        return
    draw.line(points, fill=255, width=width, joint="curve")
    radius = max(1, width // 2)
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)


def _present_canvas(canvas: Image.Image, resolution: tuple[int, int]) -> Image.Image:
    if canvas.size == resolution:
        return canvas.copy()
    return canvas.resize(resolution, Image.Resampling.LANCZOS)


def _open_ffmpeg_rawvideo_writer(out_path: Path, resolution: tuple[int, int], fps: int) -> tuple[subprocess.Popen[bytes], list[str]]:
    width, height = resolution
    command = [
        _ffmpeg(),
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    return subprocess.Popen(command, stdin=subprocess.PIPE), command


def _write_ffmpeg_frame(process: subprocess.Popen[bytes], frame: Image.Image) -> None:
    if process.stdin is None:
        raise RuntimeError("ffmpeg stdin is not available")
    payload = frame.tobytes() if frame.mode == "RGB" else frame.convert("RGB").tobytes()
    process.stdin.write(payload)


def _smooth_angle(history: list[float], angle: float, window: int = 5) -> float:
    history.append(angle)
    del history[:-window]
    return math.atan2(sum(math.sin(item) for item in history), sum(math.cos(item) for item in history))


def split_story_strokes(strokes: list[Stroke], coarse_ink_ratio: float = 0.68) -> tuple[list[Stroke], list[Stroke]]:
    """Split long structural paths from short detail paths while preserving draw order."""

    drawable = [stroke for stroke in strokes if len(stroke.points) >= 2]
    if len(drawable) < 2:
        return drawable, []

    metrics: list[tuple[int, float, float]] = []
    total_length = 0.0
    for index, stroke in enumerate(drawable):
        length = _stroke_cumulative(stroke.points)[-1]
        xs = [point[0] for point in stroke.points]
        ys = [point[1] for point in stroke.points]
        diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
        importance = length + diagonal * 0.35
        metrics.append((index, length, importance))
        total_length += length

    target = total_length * max(0.2, min(0.9, coarse_ink_ratio))
    selected: set[int] = set()
    selected_length = 0.0
    for index, length, _importance in sorted(metrics, key=lambda item: item[2], reverse=True):
        selected.add(index)
        selected_length += length
        if selected_length >= target:
            break
    if len(selected) == len(drawable):
        smallest = min(metrics, key=lambda item: item[2])[0]
        selected.remove(smallest)

    coarse = [stroke for index, stroke in enumerate(drawable) if index in selected]
    details = [stroke for index, stroke in enumerate(drawable) if index not in selected]
    return coarse, details


def _top_down_block_fill(canvas: Image.Image, source: Image.Image, progress: float, blocks: int = 18) -> Image.Image:
    """Reveal source image in hard horizontal blocks from top to bottom."""

    width, height = canvas.size
    if progress <= 0:
        return canvas.copy()
    if progress >= 1:
        return source.copy()
    block_count = max(1, int(blocks))
    visible_blocks = max(1, min(block_count, math.ceil(progress * block_count)))
    reveal_y = min(height, math.ceil(height * visible_blocks / block_count))
    frame = canvas.copy()
    frame.paste(source.crop((0, 0, width, reveal_y)), (0, 0))
    return frame


def _top_down_brush_fill(
    canvas: Image.Image,
    source: Image.Image,
    progress: float,
    blocks: int = 18,
) -> tuple[Image.Image, tuple[float, float] | None, float]:
    """Reveal source in horizontal brush passes and return brush position."""

    width, height = canvas.size
    if progress <= 0:
        return canvas.copy(), None, 0.0
    if progress >= 1:
        return source.copy(), None, 0.0

    block_count = max(1, int(blocks))
    scaled = max(0.0, min(0.999999, progress)) * block_count
    active = min(block_count - 1, int(scaled))
    local = scaled - active
    y0 = int(round(height * active / block_count))
    y1 = int(round(height * (active + 1) / block_count))
    frame = canvas.copy()
    if y0 > 0:
        frame.paste(source.crop((0, 0, width, y0)), (0, 0))

    brush_pad = max(6, height // 180)
    if active % 2 == 0:
        x = int(round(width * ease_in_out_sine(local)))
        if x > 0:
            frame.paste(source.crop((0, max(0, y0 - brush_pad), x, min(height, y1 + brush_pad))), (0, max(0, y0 - brush_pad)))
        cursor = (float(x), float((y0 + y1) / 2))
        angle = 0.0
    else:
        x = int(round(width * (1.0 - ease_in_out_sine(local))))
        if x < width:
            frame.paste(source.crop((x, max(0, y0 - brush_pad), width, min(height, y1 + brush_pad))), (x, max(0, y0 - brush_pad)))
        cursor = (float(x), float((y0 + y1) / 2))
        angle = math.pi
    return frame, cursor, angle


def _prepare_contour_fill_cache(canvas: Image.Image) -> ContourFillCache:
    """Build a field that delays color reveal around already drawn contours."""

    width, height = canvas.size
    gray = canvas.convert("L")
    ink = gray.point(lambda px: 255 if px < 218 else 0)
    spread = max(3, min(17, min(width, height) // 64))
    if spread % 2 == 0:
        spread += 1
    resistance_img = ink.filter(ImageFilter.MaxFilter(spread)).filter(
        ImageFilter.GaussianBlur(radius=max(1.5, min(width, height) / 220.0))
    )
    resistance = np.asarray(resistance_img, dtype=np.float32) / 255.0

    # Pull resistance downward so color appears to catch on contours before
    # slowly passing them, instead of crossing as a flat horizontal scan line.
    drag = resistance.copy()
    decay = 0.86
    for row in range(1, height):
        drag[row] = np.maximum(drag[row], drag[row - 1] * decay)
    drag = np.clip(drag, 0.0, 1.0)

    x = np.arange(width, dtype=np.float32)
    primary = np.sin(x / max(24.0, width / 20.0))
    secondary = np.sin(x / max(8.0, width / 72.0) + 1.7) * 0.35
    return ContourFillCache(
        resistance=drag,
        rows=np.arange(height, dtype=np.float32)[:, None],
        wave=(primary + secondary).astype(np.float32),
    )


def _contour_wipe_fill(
    canvas: Image.Image,
    source: Image.Image,
    progress: float,
    blocks: int = 18,
    cache: ContourFillCache | None = None,
) -> tuple[Image.Image, tuple[float, float] | None, float]:
    """Reveal color top-down with a contour-blocked moving boundary."""

    width, height = canvas.size
    if progress <= 0:
        return canvas.copy(), None, 0.0
    if progress >= 1:
        return source.copy(), None, 0.0
    if source.size != canvas.size:
        source = source.resize(canvas.size, Image.Resampling.LANCZOS)
    cache = cache or _prepare_contour_fill_cache(canvas)

    p = ease_in_out_sine(progress)
    delay_px = max(12.0, min(52.0, height * 0.04))
    wave_px = max(5.0, min(18.0, min(width, height) * 0.018))
    lead = p * (height + delay_px * 2.0) - delay_px
    animated_wave = cache.wave[None, :] * wave_px + math.sin(progress * math.tau * 0.8) * wave_px * 0.35
    reveal = cache.rows <= (lead + animated_wave - cache.resistance * delay_px)

    canvas_arr = np.asarray(canvas.convert("RGB"), dtype=np.uint8)
    source_arr = np.asarray(source.convert("RGB"), dtype=np.uint8)
    out = canvas_arr.copy()
    out[reveal] = source_arr[reveal]
    frame = Image.fromarray(out, mode="RGB")

    pass_count = max(1, int(blocks))
    phase = (progress * pass_count) % 1.0
    lane = int(progress * pass_count)
    eased = ease_in_out_sine(phase)
    cursor_x = int(round((eased if lane % 2 == 0 else 1.0 - eased) * (width - 1)))
    column = reveal[:, cursor_x]
    ys = np.flatnonzero(column)
    cursor_y = float(ys[-1]) if len(ys) else max(0.0, min(float(height - 1), lead))
    cursor = (float(cursor_x), max(0.0, min(float(height - 1), cursor_y)))
    angle = 0.0 if lane % 2 == 0 else math.pi
    return frame, cursor, angle


def _horizontal_gradient_mask(
    size: tuple[int, int],
    progress: float,
    *,
    feather_ratio: float = 0.12,
    wave_ratio: float = 0.012,
) -> Image.Image:
    """Build a soft, slightly irregular left-to-right reveal mask."""

    width, height = size
    if progress <= 0:
        return Image.new("L", size, 0)
    if progress >= 1:
        return Image.new("L", size, 255)
    feather = max(2.0, width * max(0.01, feather_ratio))
    wave = width * max(0.0, wave_ratio)
    front = progress * (width + feather * 2.0) - feather
    xs = np.arange(width, dtype=np.float32)[None, :]
    ys = np.arange(height, dtype=np.float32)[:, None]
    boundary = front + np.sin(ys / max(18.0, height / 18.0)) * wave
    boundary += np.sin(ys / max(7.0, height / 61.0) + 1.3) * wave * 0.32
    alpha = np.clip((boundary - xs) / feather + 0.5, 0.0, 1.0)
    return Image.fromarray(np.round(alpha * 255.0).astype(np.uint8), mode="L")


def _simplify_ink_mask(mask: Image.Image, radius: int = 1) -> Image.Image:
    """Remove fine texture while retaining the heavier structural sketch."""

    source = mask.convert("L")
    if radius <= 0:
        return source.copy()
    kernel = radius * 2 + 1
    simplified = source.filter(ImageFilter.MinFilter(kernel)).filter(ImageFilter.MaxFilter(kernel))
    source_pixels = int(np.count_nonzero(np.asarray(source, dtype=np.uint8) > 16))
    simplified_pixels = int(np.count_nonzero(np.asarray(simplified, dtype=np.uint8) > 16))
    if source_pixels and simplified_pixels < source_pixels * 0.025:
        return source.copy()
    return simplified


def _base_sketch_ink_mask(
    line_art: Image.Image,
    detail_mask: Image.Image,
    source_image: Image.Image | None = None,
    *,
    threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD,
) -> Image.Image:
    """Keep heavy structural contours and dense black regions such as hair."""

    size = detail_mask.size
    raw = line_art.convert("L").resize(size, Image.Resampling.LANCZOS)
    raw_mask = raw.point(lambda value: 255 if value < threshold else 0)
    structural = _simplify_ink_mask(raw_mask, radius=2)
    structural_arr = np.asarray(structural, dtype=np.uint8)

    if source_image is None:
        return structural
    source = source_image.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    source_arr = np.asarray(source, dtype=np.uint8)
    gray = np.asarray(source.convert("L"), dtype=np.uint8)
    chroma = source_arr.max(axis=2).astype(np.int16) - source_arr.min(axis=2).astype(np.int16)
    very_dark = (gray < 92) & (chroma < 32)
    dark_image = Image.fromarray(very_dark.astype(np.uint8) * 255, mode="L")
    density_radius = max(3, int(round(min(size) * 0.008)))
    density = np.asarray(dark_image.filter(ImageFilter.BoxBlur(density_radius)), dtype=np.uint8)
    dense_region = density > 30
    detail_arr = np.asarray(detail_mask.convert("L"), dtype=np.uint8)
    dense_black = np.where(dense_region, detail_arr, 0).astype(np.uint8)
    merged = np.maximum(structural_arr, dense_black)

    detail_pixels = int(np.count_nonzero(detail_arr > 16))
    merged_pixels = int(np.count_nonzero(merged > 16))
    if detail_pixels and merged_pixels < detail_pixels * 0.035:
        structural = _simplify_ink_mask(raw_mask, radius=1)
        merged = np.maximum(np.asarray(structural, dtype=np.uint8), dense_black)
    return Image.fromarray(merged, mode="L")


def _merge_ink_masks(first: Image.Image | None, second: Image.Image | None, size: tuple[int, int]) -> Image.Image | None:
    masks = [mask.convert("L").resize(size, Image.Resampling.LANCZOS) for mask in (first, second) if mask is not None]
    if not masks:
        return None
    merged = np.asarray(masks[0], dtype=np.uint8).copy()
    for mask in masks[1:]:
        merged = np.maximum(merged, np.asarray(mask, dtype=np.uint8))
    return Image.fromarray(merged, mode="L")


def _detail_wipe_frame(
    detail_mask: Image.Image,
    progress: float,
    *,
    base_mask: Image.Image | None = None,
    base_opacity: float = 0.76,
) -> Image.Image:
    """Show a simplified sketch immediately, then add detail left to right."""

    size = detail_mask.size
    base = Image.new("RGB", size, "white")
    if base_mask is not None:
        normalized_opacity = max(0.0, min(1.0, base_opacity)) / SKETCH_INK_OPACITY
        base = _complete_line_art_canvas(base, None, alpha=normalized_opacity, ink_mask=base_mask)
    reveal = _horizontal_gradient_mask(size, progress, feather_ratio=0.085, wave_ratio=0.01)
    return _reveal_line_art_canvas(base, None, reveal, ink_mask=detail_mask)


def _left_to_right_gradient_fill(
    canvas: Image.Image,
    source: Image.Image,
    progress: float,
) -> tuple[Image.Image, tuple[float, float] | None, float]:
    """Blend color in from left to right through a soft moving boundary."""

    if progress <= 0:
        return canvas.copy(), None, 0.0
    if progress >= 1:
        return source.convert("RGB").resize(canvas.size, Image.Resampling.LANCZOS), None, 0.0
    if source.size != canvas.size:
        source = source.resize(canvas.size, Image.Resampling.LANCZOS)
    mask = _horizontal_gradient_mask(canvas.size, ease_in_out_sine(progress), feather_ratio=0.14, wave_ratio=0.016)
    frame = Image.composite(source.convert("RGB"), canvas.convert("RGB"), mask)
    mask_arr = np.asarray(mask, dtype=np.uint8)
    transition = np.argwhere((mask_arr > 32) & (mask_arr < 224))
    if transition.size == 0:
        return frame, None, 0.0
    sample = transition[len(transition) // 2]
    return frame, (float(sample[1]), float(sample[0])), math.pi / 2


def _prepare_crayon_fill_cache(source: Image.Image) -> CrayonFillCache:
    """Create deterministic texture and an illustration-only foreground mask."""

    width, height = source.size
    rng = np.random.default_rng(20260707)
    grain = rng.random((height, width), dtype=np.float32)
    coarse_height = max(3, height // 48)
    coarse = Image.fromarray((rng.random((coarse_height, 1)) * 255).astype(np.uint8), mode="L")
    coarse = coarse.resize((1, height), Image.Resampling.BICUBIC)
    wave_noise = np.asarray(coarse, dtype=np.float32).reshape(height) / 255.0 - 0.5
    rows = np.arange(height, dtype=np.float32)
    wave = (
        np.sin(rows / max(13.0, height / 55.0)) * max(3.0, width * 0.006)
        + np.sin(rows / max(31.0, height / 21.0) + 1.4) * max(2.0, width * 0.003)
        + wave_noise * max(5.0, width * 0.014)
    ).astype(np.float32)

    source_arr = np.asarray(source.convert("RGB"), dtype=np.int16)
    border = np.concatenate(
        (
            source_arr[0, :, :],
            source_arr[-1, :, :],
            source_arr[:, 0, :],
            source_arr[:, -1, :],
        ),
        axis=0,
    )
    background = np.median(border, axis=0)
    background_distance = np.sqrt(np.mean((source_arr - background[None, None, :]) ** 2, axis=2))
    darkness = background.mean() - source_arr.mean(axis=2)
    chroma = source_arr.max(axis=2) - source_arr.min(axis=2)
    foreground = np.clip(
        np.maximum.reduce(
            (
                (background_distance - 2.5) / 15.0,
                (darkness - 3.0) / 18.0,
                (chroma - 3.0) / 16.0,
            )
        ),
        0.0,
        1.0,
    ).astype(np.float32)
    textured_source = source_arr.astype(np.float32)
    colored = foreground > 0.05
    texture = 0.965 + grain * 0.07
    textured_source[colored] = np.clip(textured_source[colored] * texture[colored, None], 0.0, 255.0)
    return CrayonFillCache(
        columns=np.arange(width, dtype=np.float32)[None, :],
        wave=wave[:, None],
        grain=grain,
        foreground=foreground,
        source=source_arr.astype(np.uint8),
        textured_source=textured_source.astype(np.uint8),
    )


def _crayon_reveal_alpha(
    progress: float,
    cache: CrayonFillCache,
    bounds: tuple[int, int, int, int],
    region_mask: np.ndarray | None = None,
    *,
    include_background: bool = False,
) -> tuple[np.ndarray, float]:
    """Build one block's textured left-to-right reveal alpha."""

    x0, _y0, x1, _y1 = bounds
    local_width = max(1, x1 - x0)
    feather = max(6.0, local_width * 0.055)
    p = ease_in_out_sine(progress)
    lead = x0 + p * (local_width + feather * 2.0) - feather
    distance = lead + cache.wave - cache.columns
    alpha = np.clip((distance + feather) / (feather * 2.0), 0.0, 1.0)
    if progress >= 1:
        alpha.fill(1.0)
    edge = (alpha > 0.0) & (alpha < 1.0)
    alpha[edge] = np.clip(alpha[edge] + (cache.grain[edge] - 0.5) * 0.42, 0.0, 1.0)
    if not include_background:
        alpha *= cache.foreground
    if region_mask is not None:
        if region_mask.shape != alpha.shape:
            raise ValueError("Crayon region mask does not match canvas size")
        alpha *= np.clip(region_mask, 0.0, 1.0)
    return alpha, lead


def _composite_cached_source(canvas: Image.Image, cache: CrayonFillCache, alpha: np.ndarray) -> Image.Image:
    canvas_arr = np.asarray(canvas.convert("RGB"), dtype=np.float32)
    source_arr = cache.textured_source.astype(np.float32)
    out = canvas_arr * (1.0 - alpha[:, :, None]) + source_arr * alpha[:, :, None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), mode="RGB")


def _block_fill_alpha(
    progress: float,
    cache: CrayonFillCache,
    bounds: tuple[int, int, int, int],
    region_mask: np.ndarray,
    style: str,
    *,
    include_background: bool = False,
) -> tuple[np.ndarray, float]:
    """Build a temporally stable object-local reveal for one media family."""

    if region_mask.shape != cache.foreground.shape:
        raise ValueError("Block region mask does not match canvas size")
    if style == "crayon":
        return _crayon_reveal_alpha(
            progress,
            cache,
            bounds,
            region_mask=region_mask,
            include_background=include_background,
        )
    profiles = {
        "clean": (0.035, 0.0, 0.02),
        "soft-wash": (0.12, 0.22, 0.08),
        "dry-brush": (0.075, 0.72, 0.30),
    }
    if style not in profiles:
        raise ValueError(f"Unsupported block fill style: {style}")
    feather_ratio, wave_strength, grain_strength = profiles[style]
    x0, _y0, x1, _y1 = bounds
    local_width = max(1, x1 - x0)
    feather = max(5.0, local_width * feather_ratio)
    p = ease_in_out_sine(progress)
    lead = x0 + p * (local_width + feather * 2.0) - feather
    distance = lead + cache.wave * wave_strength - cache.columns
    alpha = np.clip((distance + feather) / (feather * 2.0), 0.0, 1.0)
    if progress >= 1:
        alpha.fill(1.0)
    edge = (alpha > 0.0) & (alpha < 1.0)
    if grain_strength:
        alpha[edge] = np.clip(
            alpha[edge] + (cache.grain[edge] - 0.5) * grain_strength,
            0.0,
            1.0,
        )
    if not include_background:
        alpha *= cache.foreground
    alpha *= np.clip(region_mask, 0.0, 1.0)
    return alpha, lead


def _composite_block_source(
    canvas: Image.Image,
    cache: CrayonFillCache,
    alpha: np.ndarray,
    style: str,
) -> Image.Image:
    """Composite original or subtly textured color according to the style recipe."""

    canvas_arr = np.asarray(canvas.convert("RGB"), dtype=np.float32)
    source = cache.textured_source if style in {"crayon", "dry-brush"} else cache.source
    out = canvas_arr * (1.0 - alpha[:, :, None]) + source.astype(np.float32) * alpha[:, :, None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), mode="RGB")


def _crayon_left_right_fill(
    canvas: Image.Image,
    source: Image.Image,
    progress: float,
    cache: CrayonFillCache | None = None,
    region_mask: np.ndarray | None = None,
    bounds: tuple[int, int, int, int] | None = None,
) -> tuple[Image.Image, tuple[float, float] | None, float]:
    """Reveal illustration color left-to-right with a stable rough crayon edge."""

    if source.size != canvas.size:
        source = source.resize(canvas.size, Image.Resampling.LANCZOS)
    width, height = canvas.size
    if progress <= 0:
        return canvas.copy(), None, 0.0
    cache = cache or _prepare_crayon_fill_cache(source)
    x0, y0, x1, y1 = bounds or (0, 0, width, height)
    alpha, lead = _crayon_reveal_alpha(progress, cache, (x0, y0, x1, y1), region_mask=region_mask)
    frame = _composite_cached_source(canvas, cache, alpha)
    cursor_y = max(0.0, min(float(height - 1), (y0 + y1) / 2.0))
    cursor = None if progress >= 1 else (max(0.0, min(float(width - 1), lead)), cursor_y)
    return frame, cursor, 0.0


def _color_fill_frame(
    canvas: Image.Image,
    source: Image.Image,
    progress: float,
    mode: str = "contour-wipe",
    blocks: int = 18,
    contour_cache: ContourFillCache | None = None,
) -> tuple[Image.Image, tuple[float, float] | None, float]:
    if mode == "fade":
        return Image.blend(canvas, source, progress), None, 0.0
    if mode == "top-down-blocks":
        return _top_down_block_fill(canvas, source, progress, blocks=blocks), None, 0.0
    if mode == "brush-scan":
        return _top_down_brush_fill(canvas, source, progress, blocks=blocks)
    if mode == "contour-wipe":
        return _contour_wipe_fill(canvas, source, progress, blocks=blocks, cache=contour_cache)
    if mode == "left-to-right-gradient":
        return _left_to_right_gradient_fill(canvas, source, progress)
    return _top_down_brush_fill(canvas, source, progress, blocks=blocks)


def _phase_progress(t: float, start: float, end: float) -> float:
    if t <= start:
        return 0.0
    if t >= end:
        return 1.0
    return (t - start) / max(1e-6, end - start)


def _narration_paced_time(
    t: float,
    timing_cues: list[TimingCue] | None,
    video_duration: float,
    animation_end: float,
) -> float:
    """Map wall-clock time onto visual progress through narration phrase cues.

    Gaps between cues hold the drawing still. A cue advances from the previous
    cumulative target to its own ``draw_to`` target; omitted targets are evenly
    distributed. Returning ``t`` unchanged when no cues are supplied preserves
    the legacy renderer exactly.
    """

    if not timing_cues:
        return t
    if video_duration <= 0 or animation_end <= 0:
        return t
    elapsed = max(0.0, min(1.0, t)) * video_duration
    count = len(timing_cues)
    targets = [
        float(cue.draw_to) if cue.draw_to is not None else (index + 1) / count
        for index, cue in enumerate(timing_cues)
    ]
    previous_target = 0.0
    for cue, target in zip(timing_cues, targets, strict=True):
        if elapsed < cue.start_sec:
            return previous_target * animation_end
        if elapsed <= cue.end_sec:
            local = (elapsed - cue.start_sec) / max(1e-6, cue.end_sec - cue.start_sec)
            visual = previous_target + (target - previous_target) * local
            return visual * animation_end
        previous_target = target
    return animation_end


def _advance_timeline(
    draw: ImageDraw.ImageDraw,
    timeline: list[TimelineStroke],
    stroke_progress: list[float],
    progress: float,
    line_thickness: int,
    aa_scale: int,
    *,
    reveal_draw: ImageDraw.ImageDraw | None = None,
    reveal_width: int = 0,
    draw_guide: bool = True,
) -> tuple[tuple[float, float], float] | None:
    """Incrementally draw a timeline and return the latest pen pose.

    When a registered raster line-art mask is available, ``reveal_draw`` tracks
    the smooth guide path while ``draw_guide=False`` avoids exposing the pixel
    skeleton itself. The renderer then reveals the original contour through
    that mask, retaining natural widths and small line variation.
    """

    if not timeline or progress <= 0:
        return None
    total_units = max(item.pause_end_unit for item in timeline)
    target = total_units * ease_in_out_sine(progress)
    latest_pose: tuple[tuple[float, float], float] | None = None
    for stroke_index, item in enumerate(timeline):
        if target <= item.start_unit:
            break
        if target >= item.end_unit:
            desired = item.length
        else:
            local = (target - item.start_unit) / max(1e-6, item.end_unit - item.start_unit)
            desired = item.length * ease_in_out_sine(local)
        previous = stroke_progress[stroke_index]
        if desired <= previous:
            continue
        points = _stroke_segment_between(item.stroke.points, item.cumulative, previous, desired)
        if len(points) >= 2:
            if draw_guide:
                width = _line_width(
                    line_thickness,
                    desired / max(item.length, 1e-6),
                    aa_scale,
                )
                _draw_stroke_segment(draw, points, item.stroke.color, width, aa_scale)
            if reveal_draw is not None and reveal_width > 0:
                _draw_reveal_mask_segment(reveal_draw, points, reveal_width)
            latest_pose = (points[-1], segment_angle(points[-2], points[-1]))
        stroke_progress[stroke_index] = desired
    return latest_pose


def _block_windows(
    block_count: int,
    overlap: float = 0.08,
    start: float = 0.03,
    end: float = 0.94,
    weights: list[float] | None = None,
) -> list[tuple[float, float]]:
    """Return ink-weighted sequential windows with a small natural overlap."""

    if block_count <= 0:
        return []
    overlap = max(0.0, min(0.65, overlap))
    start = max(0.0, min(1.0, start))
    end = max(start + 1e-6, min(1.0, end))
    raw_weights = weights if weights is not None and len(weights) == block_count else [1.0] * block_count
    ink_weights = [math.sqrt(max(1e-6, float(weight))) for weight in raw_weights]
    ink_total = sum(ink_weights)
    # Half uniform / half ink weighted keeps tiny props readable without forcing
    # dense character blocks to draw at an implausibly high pen speed.
    shares = [0.5 / block_count + 0.5 * weight / ink_total for weight in ink_weights]
    denominator = 1.0 - overlap * sum(shares[:-1])
    scale = (end - start) / max(1e-6, denominator)
    durations = [share * scale for share in shares]
    windows: list[tuple[float, float]] = []
    cursor = start
    for duration in durations:
        windows.append((cursor, cursor + duration))
        cursor += duration * (1.0 - overlap)
    return windows


def _block_animation_end(total_frames: int, fps: int, tail_hold_sec: float) -> float:
    """Return the normalized end of drawing while reserving a final hold."""

    if total_frames <= 1:
        return 1.0
    hold_frames = max(0, min(total_frames - 1, int(round(max(0.0, tail_hold_sec) * fps))))
    return (total_frames - hold_frames) / total_frames


def _block_timeline_start(has_caption: bool, animation_end: float) -> float:
    """Reserve a tiny lead-in only for an explicitly enabled full caption."""

    return animation_end * 0.03 if has_caption else 0.0


def _build_block_region_masks(
    blocks: list[StrokeBlock],
    resolution: tuple[int, int],
) -> list[np.ndarray]:
    """Partition block bounds with a local Voronoi mask for isolated color fill."""

    if not blocks:
        return []
    width, height = resolution
    columns = np.arange(width, dtype=np.float32)[None, :]
    rows = np.arange(height, dtype=np.float32)[:, None]
    owner = np.full((height, width), -1, dtype=np.int16)
    best = np.full((height, width), np.inf, dtype=np.float32)
    for index, block in enumerate(blocks):
        x0, y0, x1, y1 = block.reveal_bounds
        center_x = (x0 + x1) / 2.0
        center_y = (y0 + y1) / 2.0
        half_width = max(12.0, (x1 - x0) / 2.0)
        half_height = max(12.0, (y1 - y0) / 2.0)
        score = ((columns - center_x) / half_width) ** 2 + ((rows - center_y) / half_height) ** 2
        inside = (columns >= x0) & (columns < x1) & (rows >= y0) & (rows < y1)
        update = inside & (score < best)
        best[update] = score[update]
        owner[update] = index
    return [(owner == index).astype(np.float32) for index in range(len(blocks))]


def _block_states(
    blocks: list[StrokeBlock],
    resolution: tuple[int, int],
    total_frames: int,
    windows: list[tuple[float, float]],
) -> list[BlockRenderState]:
    region_masks = _build_block_region_masks(blocks, resolution)
    states: list[BlockRenderState] = []
    for block, region_mask, window in zip(
        blocks,
        region_masks,
        windows,
        strict=True,
    ):
        natural_strokes = _naturalize_story_strokes(block.strokes, resolution)
        coarse, details = split_story_strokes(natural_strokes)
        coarse = postprocess_strokes(
            coarse,
            resolution,
            smooth=False,
            merge=False,
        )
        details = postprocess_strokes(
            details,
            resolution,
            smooth=False,
            merge=False,
        )
        window_frames = max(1, int(round(total_frames * (window[1] - window[0]))))
        coarse_timeline = _prepare_timeline(coarse, max(1, int(round(window_frames * 0.60))))
        detail_timeline = _prepare_timeline(details, max(1, int(round(window_frames * 0.38))))
        states.append(
            BlockRenderState(
                block=block,
                coarse_timeline=coarse_timeline,
                detail_timeline=detail_timeline,
                coarse_progress=[0.0 for _ in coarse_timeline],
                detail_progress=[0.0 for _ in detail_timeline],
                region_mask=region_mask,
            )
        )
    return states


def render_block_story_scene(
    image_path: Path,
    strokes: list[Stroke],
    duration: float,
    out_path: Path,
    story_text: str = "",
    fps: int = 30,
    resolution: tuple[int, int] = (1280, 720),
    tail_hold_sec: float = 0.0,
    line_thickness: int | None = 0,
    source_image: Image.Image | None = None,
    complete_line_art: Image.Image | None = None,
    text_ink_mask: Image.Image | None = None,
    annotations: list[Annotation] | None = None,
    timing_cues: list[TimingCue] | None = None,
    text_role: str = TextRole.CAPTION.value,
    line_art_snap: bool = True,
    line_art_snap_threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD,
    hand_style: str | Path = "asian",
    hand_scale: float = 1.0,
    max_draw_blocks: int = 6,
    draw_blocks: int | None = None,
    block_overlap: float = 0.08,
    block_order: str = "reading",
    block_sequence: list[int] | None = None,
    block_fill_style: str = "crayon",
    color_fill_scope: str = "block",
) -> None:
    """Render object blocks as outline, detail, then local crayon-color beats."""

    if not strokes:
        raise ValueError("No strokes to render")
    if duration <= 0 or fps <= 0:
        raise ValueError("Block story duration and fps must be positive")
    if block_fill_style not in {"crayon", "clean", "soft-wash", "dry-brush"}:
        raise ValueError(f"Unsupported block fill style: {block_fill_style}")
    if color_fill_scope not in {"block", "scene"}:
        raise ValueError(f"Unsupported color fill scope: {color_fill_scope}")
    try:
        TextRole(text_role)
    except ValueError as exc:
        raise ValueError(f"Unsupported text role: {text_role}") from exc
    out_path.parent.mkdir(parents=True, exist_ok=True)
    source = (
        source_image.convert("RGB").resize(resolution, Image.Resampling.LANCZOS)
        if source_image is not None
        else load_on_canvas(image_path, resolution)
    )
    blocks = group_strokes_into_blocks(
        strokes,
        resolution,
        max_blocks=max_draw_blocks,
        target_blocks=draw_blocks,
        order=block_order,
    )
    if not blocks:
        raise ValueError("No drawable stroke blocks")
    if block_sequence:
        by_id = {block.id: block for block in blocks}
        if len(set(block_sequence)) != len(block_sequence):
            raise ValueError("Block sequence contains duplicate ids")
        unknown = [block_id for block_id in block_sequence if block_id not in by_id]
        if unknown:
            raise ValueError(f"Unknown block ids in sequence: {unknown}")
        selected_ids = set(block_sequence)
        blocks = [by_id[block_id] for block_id in block_sequence] + [
            block for block in blocks if block.id not in selected_ids
        ]

    resolved_annotations = list(annotations or [])
    if len(resolved_annotations) > 2:
        raise ValueError("A scene can contain at most 2 annotations")
    if text_ink_mask is None and story_text.strip():
        text_ink_mask = _layout_text(
            story_text,
            resolution,
            "top",
            align="left",
            width_ratio=0.58,
            max_height_ratio=0.28,
            line_spacing=0.22,
        ).mask
    elif text_ink_mask is not None and text_ink_mask.size != resolution:
        text_ink_mask = text_ink_mask.resize(resolution, Image.Resampling.LANCZOS)
    annotation_layouts = [
        _layout_annotation(annotation, resolution)
        for annotation in resolved_annotations
    ]

    total_frames = max(1, int(round(duration * fps)))
    animation_end = _block_animation_end(total_frames, fps, tail_hold_sec)
    annotation_states = _annotation_render_states(
        annotation_layouts,
        total_frames,
        fps,
        animation_end,
    )
    has_caption = text_ink_mask is not None and text_ink_mask.getbbox() is not None
    line_animation_end = (
        animation_end * 0.72 if color_fill_scope == "scene" else animation_end
    )
    windows = _block_windows(
        len(blocks),
        overlap=block_overlap,
        start=_block_timeline_start(has_caption, animation_end),
        end=line_animation_end,
        weights=[block.ink_length for block in blocks],
    )
    states = _block_states(blocks, resolution, total_frames, windows)
    crayon_cache = _prepare_crayon_fill_cache(source)
    scene_fill_region = (
        np.ones((resolution[1], resolution[0]), dtype=np.float32)
        if color_fill_scope == "scene"
        else None
    )

    aa_scale = 2 if max(resolution) <= 1280 else 1
    estimated_line_width = _estimate_line_art_width(
        complete_line_art,
        threshold=line_art_snap_threshold,
    )
    effective_line_thickness = _resolve_line_thickness(line_thickness, estimated_line_width)
    canvas = Image.new(
        "RGB",
        (resolution[0] * aa_scale, resolution[1] * aa_scale),
        "white",
    )
    draw = ImageDraw.Draw(canvas)
    hand_cursor = None
    if str(hand_style) not in {"procedural", "none"}:
        hand_cursor = _load_hand_cursor(hand_style, resolution, hand_scale)
    angle_history: list[float] = []

    full_ink_mask: Image.Image | None = None
    full_ink_layer: Image.Image | None = None
    full_ink_array: np.ndarray | None = None
    if complete_line_art is not None and line_art_snap:
        full_ink_mask = _line_art_ink_mask(
            complete_line_art,
            resolution,
            threshold=line_art_snap_threshold,
        )
        full_ink_array = np.asarray(full_ink_mask, dtype=np.uint8)
        if not np.any(full_ink_array):
            full_ink_mask = None
            full_ink_array = None
        else:
            full_ink_layer = _line_art_tone_layer(complete_line_art, resolution)
    natural_reveal_mask = (
        Image.new("L", resolution, 0)
        if full_ink_array is not None
        else None
    )
    natural_reveal_draw = (
        ImageDraw.Draw(natural_reveal_mask)
        if natural_reveal_mask is not None
        else None
    )
    natural_reveal_width = max(
        8,
        effective_line_thickness * 3 + 2,
        round(estimated_line_width * 2.6),
    )
    block_ink_arrays: list[np.ndarray | None] = []
    for state in states:
        if full_ink_array is None:
            block_ink_arrays.append(None)
            continue
        block_ink_arrays.append(
            np.where(state.region_mask > 0.05, full_ink_array, 0).astype(np.uint8)
        )
    completed_ink = np.zeros((resolution[1], resolution[0]), dtype=np.uint8)
    snapped_blocks: set[int] = set()

    process, ffmpeg_command = _open_ffmpeg_rawvideo_writer(out_path, resolution, fps)
    try:
        for frame_index in range(total_frames):
            t = (frame_index + 1) / total_frames
            story_t = _narration_paced_time(
                t,
                timing_cues,
                duration,
                animation_end,
            )
            pen_pose: tuple[tuple[float, float], float] | None = None
            local_progresses: list[tuple[float, float, float]] = []
            for state, window in zip(states, windows, strict=True):
                local = _phase_progress(story_t, window[0], window[1])
                if color_fill_scope == "scene":
                    coarse_phase = _phase_progress(local, 0.0, 0.64)
                    detail_phase = _phase_progress(local, 0.38, 1.0)
                    fill_phase = 0.0
                else:
                    coarse_phase = _phase_progress(local, 0.0, 0.58)
                    detail_phase = _phase_progress(local, 0.36, 0.74)
                    fill_phase = _phase_progress(local, 0.68, 0.94)
                coarse_pose = _advance_timeline(
                    draw,
                    state.coarse_timeline,
                    state.coarse_progress,
                    coarse_phase,
                    effective_line_thickness,
                    aa_scale,
                    reveal_draw=natural_reveal_draw,
                    reveal_width=natural_reveal_width,
                    draw_guide=full_ink_array is None,
                )
                detail_pose = _advance_timeline(
                    draw,
                    state.detail_timeline,
                    state.detail_progress,
                    detail_phase,
                    effective_line_thickness,
                    aa_scale,
                    reveal_draw=natural_reveal_draw,
                    reveal_width=natural_reveal_width,
                    draw_guide=full_ink_array is None,
                )
                if coarse_pose is not None or detail_pose is not None:
                    pen_pose = detail_pose or coarse_pose
                local_progresses.append((coarse_phase, detail_phase, fill_phase))

            for index, (state, phases) in enumerate(
                zip(states, local_progresses, strict=True)
            ):
                coarse_phase, detail_phase, _fill_phase = phases
                snap_ready = (
                    detail_phase >= 1.0
                    if state.detail_timeline
                    else coarse_phase >= 1.0
                )
                block_ink = block_ink_arrays[index]
                if snap_ready and index not in snapped_blocks and block_ink is not None:
                    np.maximum(completed_ink, block_ink, out=completed_ink)
                    snapped_blocks.add(index)

            line_frame = _present_canvas(canvas, resolution)
            if natural_reveal_mask is not None and full_ink_mask is not None:
                line_frame = _reveal_line_art_canvas(
                    line_frame,
                    None,
                    natural_reveal_mask,
                    ink_mask=full_ink_mask,
                    ink_layer=full_ink_layer,
                )
            completed_mask = None
            if snapped_blocks:
                completed_mask = Image.fromarray(completed_ink, mode="L")
                line_frame = _complete_line_art_canvas(
                    line_frame,
                    complete_line_art,
                    ink_mask=completed_mask,
                    ink_layer=full_ink_layer,
                )

            frame = line_frame
            fill_pose: tuple[tuple[float, float], float] | None = None
            combined_alpha: np.ndarray | None = None
            if color_fill_scope == "scene":
                assert scene_fill_region is not None
                fill_phase = _phase_progress(
                    story_t,
                    animation_end * 0.68,
                    animation_end,
                )
                if fill_phase > 0:
                    combined_alpha, lead = _block_fill_alpha(
                        fill_phase,
                        crayon_cache,
                        (0, 0, resolution[0], resolution[1]),
                        scene_fill_region,
                        block_fill_style,
                        include_background=True,
                    )
                    if fill_phase < 1.0:
                        fill_pose = (
                            (
                                max(
                                    0.0,
                                    min(float(resolution[0] - 1), lead),
                                ),
                                float(resolution[1] - 1) / 2.0,
                            ),
                            0.0,
                        )
            else:
                for state, phases in zip(states, local_progresses, strict=True):
                    fill_phase = phases[2]
                    if fill_phase <= 0:
                        continue
                    block_alpha, lead = _block_fill_alpha(
                        fill_phase,
                        crayon_cache,
                        state.block.reveal_bounds,
                        state.region_mask,
                        block_fill_style,
                    )
                    if combined_alpha is None:
                        combined_alpha = block_alpha.copy()
                    else:
                        np.maximum(combined_alpha, block_alpha, out=combined_alpha)
                    if fill_phase >= 1.0:
                        continue
                    _x0, y0, _x1, y1 = state.block.reveal_bounds
                    fill_pose = (
                        (
                            max(0.0, min(float(resolution[0] - 1), lead)),
                            max(0.0, min(float(resolution[1] - 1), (y0 + y1) / 2.0)),
                        ),
                        0.0,
                    )
            if combined_alpha is not None:
                frame = _composite_block_source(
                    line_frame,
                    crayon_cache,
                    combined_alpha,
                    block_fill_style,
                )
            if completed_mask is not None:
                frame = _complete_line_art_canvas(
                    frame,
                    complete_line_art,
                    ink_mask=completed_mask,
                    ink_layer=full_ink_layer,
                )

            if text_ink_mask is not None:
                text_progress = _phase_progress(t, 0.0, animation_end * 0.18)
                text_reveal = _horizontal_gradient_mask(
                    resolution,
                    text_progress,
                    feather_ratio=0.055,
                    wave_ratio=0.006,
                )
                frame = _reveal_line_art_canvas(
                    frame,
                    None,
                    text_reveal,
                    ink_mask=text_ink_mask,
                )

            annotation_pose: tuple[tuple[float, float], float] | None = None
            for annotation_state in annotation_states:
                annotation_progress = _phase_progress(
                    story_t,
                    annotation_state.start,
                    annotation_state.end,
                )
                if annotation_progress <= 0:
                    continue
                annotation_reveal = _annotation_typewriter_mask(
                    annotation_state.layout,
                    annotation_progress,
                )
                frame = _reveal_line_art_canvas(
                    frame,
                    None,
                    annotation_reveal,
                    ink_mask=annotation_state.layout.mask,
                )
                if annotation_progress < 1.0:
                    annotation_pose = _annotation_cursor_pose(
                        annotation_state.layout,
                        annotation_progress,
                    )

            active_pose = annotation_pose or pen_pose or fill_pose
            if active_pose is not None and str(hand_style) != "none":
                (cursor_x, cursor_y), raw_angle = active_pose
                angle = _smooth_angle(angle_history, raw_angle)
                if hand_cursor is not None:
                    frame = _paste_hand_cursor(
                        frame,
                        cursor_x,
                        cursor_y,
                        hand_cursor,
                    )
                else:
                    _draw_cursor(
                        frame,
                        cursor_x,
                        cursor_y,
                        angle,
                        scale=max(0.1, hand_scale),
                    )
            _write_ffmpeg_frame(process, frame)
    except Exception:
        if process.stdin is not None:
            process.stdin.close()
        process.wait()
        raise
    if process.stdin is not None:
        process.stdin.close()
    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, ffmpeg_command)


def render_scene(
    image_path: Path,
    strokes: list[Stroke],
    duration: float,
    out_path: Path,
    fps: int = 60,
    resolution: tuple[int, int] = (1920, 1080),
    tail_color_sec: float = 2.0,
    line_thickness: int | None = 0,
    show_cursor: bool = True,
    source_image: Image.Image | None = None,
    hand_style: str | Path = "asian",
    hand_scale: float = 1.0,
    color_fill_mode: str = "contour-wipe",
    color_fill_blocks: int = 18,
    line_reveal_mode: str = "stroke",
    base_line_opacity: float = 0.76,
    complete_line_art: Image.Image | None = None,
    text_ink_mask: Image.Image | None = None,
    line_art_snap: bool = True,
    line_art_snap_threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD,
) -> None:
    """Render strokes into a hand-drawn MP4 scene.

    Example:
        >>> render_scene(Path("missing.png"), [], 1.0, Path("out.mp4"))  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
    """

    if line_reveal_mode not in {"stroke", "detail-wipe"}:
        raise ValueError(f"Unsupported line reveal mode: {line_reveal_mode}")
    if not 0.0 <= base_line_opacity <= 1.0:
        raise ValueError("Base line opacity must be between 0.0 and 1.0")
    if not strokes and line_reveal_mode == "stroke":
        raise ValueError("No strokes to render")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    source = source_image.convert("RGB").resize(resolution) if source_image else load_on_canvas(image_path, resolution)
    aa_scale = 2 if max(resolution) <= 1280 else 1
    estimated_line_width = _estimate_line_art_width(complete_line_art, threshold=line_art_snap_threshold)
    effective_line_thickness = _resolve_line_thickness(line_thickness, estimated_line_width)
    reveal_width = max(effective_line_thickness * 3 + 2, int(round(estimated_line_width * 2.2)), 8)
    complete_ink_mask = (
        _line_art_ink_mask(complete_line_art, resolution, threshold=line_art_snap_threshold)
        if complete_line_art is not None
        else None
    )
    snap_ink_mask = (
        complete_ink_mask
        if complete_ink_mask is not None and line_art_snap
        else None
    )
    complete_ink_layer = (
        _line_art_tone_layer(complete_line_art, resolution)
        if complete_line_art is not None and snap_ink_mask is not None
        else None
    )
    canvas = Image.new("RGB", (resolution[0] * aa_scale, resolution[1] * aa_scale), "white")
    draw = ImageDraw.Draw(canvas)
    reveal_mask = Image.new("L", resolution, 0) if complete_line_art is not None and line_art_snap else None
    reveal_draw = ImageDraw.Draw(reveal_mask) if reveal_mask is not None else None
    if text_ink_mask is not None and text_ink_mask.size != resolution:
        text_ink_mask = text_ink_mask.resize(resolution, Image.Resampling.LANCZOS)
    detail_ink_mask = _merge_ink_masks(complete_ink_mask, text_ink_mask, resolution)
    base_ink_mask = (
        _base_sketch_ink_mask(complete_line_art, detail_ink_mask, source, threshold=line_art_snap_threshold)
        if complete_line_art is not None and detail_ink_mask is not None
        else None
    )
    if line_reveal_mode == "detail-wipe" and detail_ink_mask is None:
        raise ValueError("detail-wipe requires a rasterizable complete line-art image")
    text_reveal_mask = Image.new("L", resolution, 0) if text_ink_mask is not None else None
    text_reveal_draw = ImageDraw.Draw(text_reveal_mask) if text_reveal_mask is not None else None

    total_frames = max(1, int(round(duration * fps)))
    fade_frames = max(0, min(total_frames - 1, int(round(tail_color_sec * fps))))
    draw_frames = max(1, total_frames - fade_frames)
    completion_frames = min(max(6, int(round(fps * 0.45))), max(1, draw_frames // 5))
    timeline = _prepare_timeline(strokes, draw_frames) if line_reveal_mode == "stroke" else []
    if line_reveal_mode == "stroke" and not timeline:
        raise ValueError("No drawable stroke segments")
    total_units = max((item.pause_end_unit for item in timeline), default=0.0)
    stroke_progress = [0.0 for _ in timeline]
    cursor = (resolution[0] / 2, resolution[1] / 2)
    angle = 0.0
    angle_history: list[float] = []
    hand_cursor = None
    contour_fill_cache: ContourFillCache | None = None
    color_base_canvas: Image.Image | None = None
    if show_cursor and line_reveal_mode == "stroke" and str(hand_style) not in {"procedural", "none"}:
        hand_cursor = _load_hand_cursor(hand_style, resolution, hand_scale)
    process, ffmpeg_command = _open_ffmpeg_rawvideo_writer(out_path, resolution, fps)
    try:
        for frame_index in range(total_frames):
            if frame_index < draw_frames:
                if line_reveal_mode == "detail-wipe":
                    raw_progress = (frame_index + 1) / draw_frames
                    hold_ratio = 0.12
                    detail_progress = max(0.0, min(1.0, (raw_progress - hold_ratio) / (1.0 - hold_ratio)))
                    frame = _detail_wipe_frame(
                        detail_ink_mask,
                        detail_progress,
                        base_mask=base_ink_mask,
                        base_opacity=base_line_opacity,
                    )
                else:
                    target = total_units * ((frame_index + 1) / draw_frames)
                    for stroke_index, item in enumerate(timeline):
                        if target <= item.start_unit:
                            break
                        if target >= item.end_unit:
                            desired = item.length
                        else:
                            local = (target - item.start_unit) / max(1e-6, item.end_unit - item.start_unit)
                            desired = item.length * ease_in_out_sine(local)
                        previous = stroke_progress[stroke_index]
                        if desired > previous:
                            pts = _stroke_segment_between(item.stroke.points, item.cumulative, previous, desired)
                            if len(pts) >= 2:
                                progress_ratio = desired / max(item.length, 1e-6)
                                is_text = item.stroke.source.startswith("text")
                                if is_text and text_reveal_draw is not None:
                                    text_width = item.stroke.reveal_width or reveal_width
                                    _draw_reveal_mask_segment(text_reveal_draw, pts, text_width)
                                else:
                                    width = _line_width(effective_line_thickness, progress_ratio, aa_scale)
                                    _draw_stroke_segment(draw, pts, item.stroke.color, width, aa_scale)
                                if reveal_draw is not None and not is_text:
                                    _draw_reveal_mask_segment(reveal_draw, pts, reveal_width)
                                cursor = pts[-1]
                                angle = _smooth_angle(angle_history, segment_angle(pts[-2], pts[-1]))
                            stroke_progress[stroke_index] = desired
                    frame = _present_canvas(canvas, resolution)
                    frame = _reveal_line_art_canvas(
                        frame,
                        complete_line_art,
                        reveal_mask,
                        threshold=line_art_snap_threshold,
                        ink_mask=snap_ink_mask,
                        ink_layer=complete_ink_layer,
                    )
                    frame = _reveal_line_art_canvas(frame, None, text_reveal_mask, ink_mask=text_ink_mask)
                    if line_art_snap:
                        completion_start = max(0, draw_frames - completion_frames)
                        if frame_index >= completion_start:
                            completion = (frame_index - completion_start + 1) / max(1, completion_frames)
                            frame = _complete_line_art_canvas(
                                frame,
                                complete_line_art,
                                threshold=line_art_snap_threshold,
                                alpha=completion,
                                ink_mask=snap_ink_mask,
                                ink_layer=complete_ink_layer,
                            )
                    if text_ink_mask is not None and frame_index == draw_frames - 1:
                        frame = _complete_line_art_canvas(frame, None, ink_mask=text_ink_mask)
                    if show_cursor:
                        if hand_cursor is not None:
                            frame = _paste_hand_cursor(frame, cursor[0], cursor[1], hand_cursor)
                        elif hand_style != "none":
                            _draw_cursor(frame, cursor[0], cursor[1], angle)
            else:
                progress = (frame_index - draw_frames + 1) / max(1, fade_frames)
                if color_base_canvas is None:
                    if line_reveal_mode == "detail-wipe":
                        color_base_canvas = _detail_wipe_frame(
                            detail_ink_mask,
                            1.0,
                            base_mask=base_ink_mask,
                            base_opacity=base_line_opacity,
                        )
                    else:
                        color_base_canvas = _present_canvas(canvas, resolution)
                        color_base_canvas = _reveal_line_art_canvas(
                            color_base_canvas,
                            complete_line_art,
                            reveal_mask,
                            threshold=line_art_snap_threshold,
                            ink_mask=snap_ink_mask,
                            ink_layer=complete_ink_layer,
                        )
                        if line_art_snap:
                            color_base_canvas = _complete_line_art_canvas(
                                color_base_canvas,
                                complete_line_art,
                                threshold=line_art_snap_threshold,
                                ink_mask=snap_ink_mask,
                                ink_layer=complete_ink_layer,
                            )
                    color_base_canvas = _complete_line_art_canvas(color_base_canvas, None, ink_mask=text_ink_mask)
                color_canvas = color_base_canvas
                if color_fill_mode == "contour-wipe" and contour_fill_cache is None:
                    contour_fill_cache = _prepare_contour_fill_cache(color_canvas)
                frame, fill_cursor, fill_angle = _color_fill_frame(
                    color_canvas,
                    source,
                    progress,
                    mode=color_fill_mode,
                    blocks=color_fill_blocks,
                    contour_cache=contour_fill_cache,
                )
                frame = _complete_line_art_canvas(frame, None, ink_mask=text_ink_mask)
                if show_cursor and fill_cursor is not None:
                    if hand_cursor is not None:
                        frame = _paste_hand_cursor(frame, fill_cursor[0], fill_cursor[1], hand_cursor)
                    elif hand_style != "none":
                        _draw_cursor(frame, fill_cursor[0], fill_cursor[1], fill_angle)
            _write_ffmpeg_frame(process, frame)
    except Exception:
        if process.stdin is not None:
            process.stdin.close()
        process.wait()
        raise
    if process.stdin is not None:
        process.stdin.close()
    return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, ffmpeg_command)


def render_image(
    image_path: Path,
    out_path: Path,
    duration: float = 8.0,
    fps: int = 60,
    resolution: tuple[int, int] = (1920, 1080),
    tail_color_sec: float = 2.0,
    source_image_path: Path | None = None,
    source_fit: str = "blur-fill",
    color_fill_mode: str = "contour-wipe",
    color_fill_blocks: int = 18,
    line_reveal_mode: str = "stroke",
    base_line_opacity: float = 0.76,
    hand_style: str | Path = "asian",
    hand_scale: float = 1.0,
    draw_text: str | None = None,
    draw_text_position: str = "bottom",
    draw_text_align: str = "center",
    draw_text_width: float = 0.82,
    draw_text_max_height: float = 0.36,
    draw_text_line_spacing: float = 0.25,
    draw_text_font_size: int | None = None,
    draw_text_font: Path | None = None,
    draw_text_reveal: str = "stroke",
    draw_text_order: str = "after",
    draw_text_role: str = TextRole.ANNOTATION.value,
    annotations: list[Annotation] | None = None,
    timing_cues: list[TimingCue] | None = None,
    line_art_snap: bool = True,
    line_art_snap_threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD,
    line_thickness: int | None = 0,
    stroke_detail: str = "rich",
    animation_preset: str = "classic",
    story_text: str | None = None,
    max_draw_blocks: int = 6,
    draw_blocks: int | None = None,
    block_overlap: float = 0.08,
    block_order: str = "reading",
    block_sequence: list[int] | None = None,
    block_fill_style: str = "crayon",
    color_fill_scope: str = "block",
) -> None:
    """Extract strokes from one image and render a scene MP4."""

    try:
        resolved_text_role = TextRole(draw_text_role)
    except ValueError as exc:
        raise ValueError(f"Unsupported text role: {draw_text_role}") from exc
    if annotations and animation_preset != "block-speedpaint":
        raise ValueError("Positioned annotations require block-speedpaint")
    if timing_cues and animation_preset != "block-speedpaint":
        raise ValueError("Narration timing cues require block-speedpaint")

    source_image = None
    complete_line_art = None
    if image_path.suffix.lower() == ".svg":
        strokes, source_image = svg_to_strokes(image_path, resolution)
        complete_line_art = source_image
        if line_reveal_mode == "detail-wipe" and animation_preset == "classic":
            strokes = []
    else:
        complete_line_art = _line_art_canvas(image_path, resolution)
        strokes = (
            []
            if line_reveal_mode == "detail-wipe" and animation_preset == "classic"
            else to_strokes(image_path, resolution, stroke_detail=stroke_detail)
        )
    text_layout: TextLayout | None = None
    block_annotations = list(annotations or [])
    if draw_text:
        if (
            animation_preset == "block-speedpaint"
            and resolved_text_role == TextRole.ANNOTATION
        ):
            block_annotations.append(Annotation(text=draw_text))
        else:
            text_layout = _layout_text(
                draw_text,
                resolution,
                draw_text_position,
                align=draw_text_align,
                width_ratio=draw_text_width,
                max_height_ratio=draw_text_max_height,
                line_spacing=draw_text_line_spacing,
                font_size=draw_text_font_size,
                font_path=draw_text_font,
            )
        if line_reveal_mode == "stroke" and animation_preset == "classic":
            if draw_text_reveal == "line-wipe":
                text_strokes = _text_wipe_strokes(text_layout)
            elif draw_text_reveal == "stroke":
                text_strokes = _text_to_strokes(
                    draw_text,
                    resolution,
                    draw_text_position,
                    align=draw_text_align,
                    width_ratio=draw_text_width,
                    max_height_ratio=draw_text_max_height,
                    line_spacing=draw_text_line_spacing,
                    font_size=draw_text_font_size,
                    font_path=draw_text_font,
                )
            else:
                raise ValueError(f"Unsupported text reveal mode: {draw_text_reveal}")
            if draw_text_order == "before":
                strokes = text_strokes + strokes
            elif draw_text_order == "after":
                strokes.extend(text_strokes)
            else:
                raise ValueError(f"Unsupported text order: {draw_text_order}")
    if len(block_annotations) > 2:
        raise ValueError("A scene can contain at most 2 annotations")
    if source_image_path:
        source_image = _load_source_image_canvas(source_image_path, resolution, source_fit)
    if animation_preset == "block-speedpaint":
        render_block_story_scene(
            image_path,
            strokes,
            duration,
            out_path,
            story_text=story_text or "",
            fps=fps,
            resolution=resolution,
            tail_hold_sec=tail_color_sec,
            line_thickness=line_thickness,
            source_image=source_image,
            complete_line_art=complete_line_art,
            text_ink_mask=(
                text_layout.mask
                if text_layout is not None and not story_text
                else None
            ),
            annotations=block_annotations,
            timing_cues=timing_cues,
            text_role=resolved_text_role.value,
            line_art_snap=line_art_snap,
            line_art_snap_threshold=line_art_snap_threshold,
            hand_style=hand_style,
            hand_scale=hand_scale,
            max_draw_blocks=max_draw_blocks,
            draw_blocks=draw_blocks,
            block_overlap=block_overlap,
            block_order=block_order,
            block_sequence=block_sequence,
            block_fill_style=block_fill_style,
            color_fill_scope=color_fill_scope,
        )
        return
    if animation_preset != "classic":
        raise ValueError(f"Unknown animation preset: {animation_preset}")
    render_scene(
        image_path,
        strokes,
        duration,
        out_path,
        fps=fps,
        resolution=resolution,
        tail_color_sec=tail_color_sec,
        source_image=source_image,
        show_cursor=hand_style != "none",
        hand_style=hand_style,
        hand_scale=hand_scale,
        line_thickness=line_thickness,
        color_fill_mode=color_fill_mode,
        color_fill_blocks=color_fill_blocks,
        line_reveal_mode=line_reveal_mode,
        base_line_opacity=base_line_opacity,
        complete_line_art=complete_line_art,
        text_ink_mask=text_layout.mask if text_layout is not None else None,
        line_art_snap=line_art_snap,
        line_art_snap_threshold=line_art_snap_threshold,
    )
