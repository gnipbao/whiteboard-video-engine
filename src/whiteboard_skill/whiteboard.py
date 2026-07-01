"""Stroke-level whiteboard renderer."""

from __future__ import annotations

import math
import bisect
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .preprocess import Stroke, load_on_canvas, postprocess_strokes, svg_to_strokes, to_strokes, trace_8connected, zhang_suen_skeleton


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


FONT_CANDIDATES = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
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


DEFAULT_LINE_ART_SNAP_THRESHOLD = 170


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
) -> Image.Image:
    """Composite missing black line-art pixels over a redrawn stroke canvas."""

    if line_art is None and ink_mask is None:
        return canvas.copy()
    base = canvas.convert("RGB")
    mask = ink_mask if ink_mask is not None else _line_art_ink_mask(line_art, base.size, threshold=threshold)
    if mask.size != base.size:
        mask = mask.resize(base.size, Image.Resampling.NEAREST)
    opacity = SKETCH_INK_OPACITY * max(0.0, min(1.0, alpha))
    mask = mask.point(lambda px: int(px * opacity))
    ink = Image.new("RGB", base.size, SKETCH_INK_COLOR)
    base.paste(ink, mask=mask)
    return base


def _reveal_line_art_canvas(
    canvas: Image.Image,
    line_art: Image.Image | None,
    reveal_mask: Image.Image | None,
    threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD,
    ink_mask: Image.Image | None = None,
) -> Image.Image:
    if reveal_mask is None or (line_art is None and ink_mask is None):
        return canvas.copy()
    base = canvas.convert("RGB")
    mask = ink_mask if ink_mask is not None else _line_art_ink_mask(line_art, base.size, threshold=threshold)
    if mask.size != base.size:
        mask = mask.resize(base.size, Image.Resampling.NEAREST)
    reveal = _combine_masks(mask, reveal_mask).point(lambda px: int(px * SKETCH_INK_OPACITY))
    ink = Image.new("RGB", base.size, SKETCH_INK_COLOR)
    base.paste(ink, mask=reveal)
    return base


def _load_text_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=max(10, size))


def _fit_text_font(text: str, resolution: tuple[int, int]) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    width, height = resolution
    max_width = width * 0.82
    max_height = height * 0.12
    start = int(max(20, min(height * 0.085, width * 0.16)))
    probe = Image.new("L", resolution, 255)
    draw = ImageDraw.Draw(probe)
    for size in range(start, 13, -2):
        font = _load_text_font(size)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        if right - left <= max_width and bottom - top <= max_height:
            return font
    return _load_text_font(14)


def _text_to_strokes(text: str, resolution: tuple[int, int], position: str = "bottom") -> list[Stroke]:
    """Convert a short caption into drawable centerline strokes."""

    cleaned = " ".join(text.split())
    if not cleaned:
        return []
    width, height = resolution
    font = _fit_text_font(cleaned, resolution)
    image = Image.new("L", resolution, 255)
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = draw.textbbox((0, 0), cleaned, font=font)
    text_w, text_h = right - left, bottom - top
    x = (width - text_w) / 2.0 - left
    margin = max(18, int(round(height * 0.055)))
    if position == "top":
        y = margin - top
    elif position == "center":
        y = (height - text_h) / 2.0 - top
    else:
        y = height - margin - text_h - top
    draw.text((x, y), cleaned, font=font, fill=0)

    mask = np.asarray(image, dtype=np.uint8) < 235
    if not np.any(mask):
        return []
    skel = zhang_suen_skeleton(mask)
    paths = trace_8connected(skel, min_points=4)
    strokes = [
        Stroke(points=[(float(x), float(y)) for x, y in path], source="text")
        for path in paths
    ]
    return postprocess_strokes(strokes, resolution, smooth=True, merge=True)


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
    for a, b in zip(points, points[1:]):
        total += math.hypot(b[0] - a[0], b[1] - a[1])
        cumulative.append(total)
    return cumulative


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
    return _top_down_brush_fill(canvas, source, progress, blocks=blocks)


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
    complete_line_art: Image.Image | None = None,
    line_art_snap: bool = True,
    line_art_snap_threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD,
) -> None:
    """Render strokes into a hand-drawn MP4 scene.

    Example:
        >>> render_scene(Path("missing.png"), [], 1.0, Path("out.mp4"))  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
    """

    if not strokes:
        raise ValueError("No strokes to render")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    source = source_image.convert("RGB").resize(resolution) if source_image else load_on_canvas(image_path, resolution)
    aa_scale = 2 if max(resolution) <= 1280 else 1
    estimated_line_width = _estimate_line_art_width(complete_line_art, threshold=line_art_snap_threshold)
    effective_line_thickness = _resolve_line_thickness(line_thickness, estimated_line_width)
    reveal_width = max(effective_line_thickness * 3 + 2, int(round(estimated_line_width * 2.2)), 8)
    snap_ink_mask = (
        _line_art_ink_mask(complete_line_art, resolution, threshold=line_art_snap_threshold)
        if complete_line_art is not None and line_art_snap
        else None
    )
    canvas = Image.new("RGB", (resolution[0] * aa_scale, resolution[1] * aa_scale), "white")
    draw = ImageDraw.Draw(canvas)
    reveal_mask = Image.new("L", resolution, 0) if complete_line_art is not None and line_art_snap else None
    reveal_draw = ImageDraw.Draw(reveal_mask) if reveal_mask is not None else None

    total_frames = max(1, int(round(duration * fps)))
    fade_frames = max(0, min(total_frames - 1, int(round(tail_color_sec * fps))))
    draw_frames = max(1, total_frames - fade_frames)
    completion_frames = min(max(6, int(round(fps * 0.45))), max(1, draw_frames // 5))
    timeline = _prepare_timeline(strokes, draw_frames)
    if not timeline:
        raise ValueError("No drawable stroke segments")
    total_units = max(item.pause_end_unit for item in timeline)
    stroke_progress = [0.0 for _ in timeline]
    cursor = (resolution[0] / 2, resolution[1] / 2)
    angle = 0.0
    angle_history: list[float] = []
    hand_cursor = None
    contour_fill_cache: ContourFillCache | None = None
    color_base_canvas: Image.Image | None = None
    if show_cursor and str(hand_style) not in {"procedural", "none"}:
        hand_cursor = _load_hand_cursor(hand_style, resolution, hand_scale)
    process, ffmpeg_command = _open_ffmpeg_rawvideo_writer(out_path, resolution, fps)
    try:
        for frame_index in range(total_frames):
            if frame_index < draw_frames:
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
                            width = _line_width(effective_line_thickness, progress_ratio, aa_scale)
                            _draw_stroke_segment(draw, pts, item.stroke.color, width, aa_scale)
                            if reveal_draw is not None:
                                _draw_reveal_mask_segment(reveal_draw, pts, reveal_width)
                            cursor = pts[-1]
                            angle = _smooth_angle(angle_history, segment_angle(pts[-2], pts[-1]))
                        stroke_progress[stroke_index] = desired
                frame = _present_canvas(canvas, resolution)
                frame = _reveal_line_art_canvas(frame, complete_line_art, reveal_mask, threshold=line_art_snap_threshold, ink_mask=snap_ink_mask)
                if line_art_snap:
                    completion_start = max(0, draw_frames - completion_frames)
                    if frame_index >= completion_start:
                        completion = (frame_index - completion_start + 1) / max(1, completion_frames)
                        frame = _complete_line_art_canvas(frame, complete_line_art, threshold=line_art_snap_threshold, alpha=completion, ink_mask=snap_ink_mask)
                if show_cursor:
                    if hand_cursor is not None:
                        frame = _paste_hand_cursor(frame, cursor[0], cursor[1], hand_cursor)
                    elif hand_style != "none":
                        _draw_cursor(frame, cursor[0], cursor[1], angle)
            else:
                progress = (frame_index - draw_frames + 1) / max(1, fade_frames)
                if color_base_canvas is None:
                    color_base_canvas = _present_canvas(canvas, resolution)
                    color_base_canvas = _reveal_line_art_canvas(
                        color_base_canvas,
                        complete_line_art,
                        reveal_mask,
                        threshold=line_art_snap_threshold,
                        ink_mask=snap_ink_mask,
                    )
                    if line_art_snap:
                        color_base_canvas = _complete_line_art_canvas(
                            color_base_canvas,
                            complete_line_art,
                            threshold=line_art_snap_threshold,
                            ink_mask=snap_ink_mask,
                        )
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
    hand_style: str | Path = "asian",
    hand_scale: float = 1.0,
    draw_text: str | None = None,
    draw_text_position: str = "bottom",
    line_art_snap: bool = True,
    line_art_snap_threshold: int = DEFAULT_LINE_ART_SNAP_THRESHOLD,
    line_thickness: int | None = 0,
    stroke_detail: str = "rich",
) -> None:
    """Extract strokes from one image and render a scene MP4."""

    source_image = None
    complete_line_art = None
    if image_path.suffix.lower() == ".svg":
        strokes, source_image = svg_to_strokes(image_path, resolution)
        complete_line_art = source_image
    else:
        strokes = to_strokes(image_path, resolution, stroke_detail=stroke_detail)
        complete_line_art = _line_art_canvas(image_path, resolution)
    if draw_text:
        strokes.extend(_text_to_strokes(draw_text, resolution, draw_text_position))
    if source_image_path:
        source_image = _load_source_image_canvas(source_image_path, resolution, source_fit)
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
        complete_line_art=complete_line_art,
        line_art_snap=line_art_snap,
        line_art_snap_threshold=line_art_snap_threshold,
    )
