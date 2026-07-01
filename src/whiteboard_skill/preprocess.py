"""Image preprocessing and stroke extraction."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


Point = tuple[float, float]
NEIGHBORS_8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
DEFAULT_BORDER_MARGIN_RATIO = 0.012
MIN_BORDER_MARGIN_PX = 4
STROKE_DETAIL_PRESETS = {
    "balanced": {
        "threshold": 235,
        "min_points": 8,
        "min_length": 8.0,
        "smooth_spacing": None,
        "merge_gap": None,
        "merge": True,
        "max_merge_strokes": 1000,
    },
    "rich": {
        "threshold": 235,
        "min_points": 4,
        "min_length": "rich",
        "smooth_spacing": "rich",
        "merge_gap": "rich",
        "merge": True,
        "max_merge_strokes": 3200,
    },
    "max": {
        "threshold": 248,
        "min_points": 2,
        "min_length": 3.0,
        "smooth_spacing": "max",
        "merge_gap": "max",
        "merge": False,
        "max_merge_strokes": 0,
    },
}


@dataclass
class Stroke:
    """A drawable stroke path.

    Example:
        >>> Stroke(points=[(0, 0), (1, 1)]).source
        'raster'
    """

    points: list[Point]
    color: tuple[int, int, int] = (64, 60, 62)
    source: str = "raster"


def load_on_canvas(image_path: Path, canvas_size: tuple[int, int]) -> Image.Image:
    """Resize an image into a white canvas while preserving aspect ratio.

    Example:
        >>> load_on_canvas(Path("missing.png"), (640, 360))  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
    """

    canvas, _ = load_on_canvas_with_bounds(image_path, canvas_size)
    return canvas


def load_on_canvas_with_bounds(image_path: Path, canvas_size: tuple[int, int]) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Resize an image into a white canvas and return its placed bounds."""

    width, height = canvas_size
    src = Image.open(image_path).convert("RGBA")
    src.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    left, top = (width - src.width) // 2, (height - src.height) // 2
    canvas.alpha_composite(src, (left, top))
    return canvas.convert("RGB"), (left, top, left + src.width, top + src.height)


def binarize(img: np.ndarray, threshold: int = 235) -> np.ndarray:
    """Convert a grayscale image array into a boolean foreground mask.

    Example:
        >>> binarize(np.array([[255, 0]], dtype=np.uint8)).tolist()
        [[False, True]]
    """

    return img < threshold


def _border_margin_px(canvas_size: tuple[int, int], margin_px: int | None = None) -> int:
    if margin_px is not None:
        return max(0, int(margin_px))
    width, height = canvas_size
    return max(MIN_BORDER_MARGIN_PX, int(round(min(width, height) * DEFAULT_BORDER_MARGIN_RATIO)))


def _normalized_bounds(bounds: tuple[int, int, int, int], shape: tuple[int, int]) -> tuple[int, int, int, int]:
    h, w = shape
    x0, y0, x1, y1 = bounds
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def _clear_frame_band(mask: np.ndarray, bounds: tuple[int, int, int, int], margin_px: int) -> None:
    x0, y0, x1, y1 = _normalized_bounds(bounds, mask.shape)
    if x1 <= x0 or y1 <= y0:
        return
    margin = min(margin_px, max(1, (x1 - x0) // 4), max(1, (y1 - y0) // 4))
    if margin <= 0:
        return

    mask[y0 : min(y1, y0 + margin), x0:x1] = False
    mask[max(y0, y1 - margin) : y1, x0:x1] = False
    mask[y0:y1, x0 : min(x1, x0 + margin)] = False
    mask[y0:y1, max(x0, x1 - margin) : x1] = False

    search = max(margin * 3, 16)
    pad = max(1, margin // 2)
    density_threshold = 0.75

    def clear_rows(row_indices: np.ndarray) -> None:
        for row in row_indices:
            mask[max(y0, row - pad) : min(y1, row + pad + 1), x0:x1] = False

    def clear_cols(col_indices: np.ndarray) -> None:
        for col in col_indices:
            mask[y0:y1, max(x0, col - pad) : min(x1, col + pad + 1)] = False

    top_end = min(y1, y0 + search)
    if top_end > y0 and x1 > x0:
        rows = np.where(mask[y0:top_end, x0:x1].mean(axis=1) >= density_threshold)[0] + y0
        clear_rows(rows)
    bottom_start = max(y0, y1 - search)
    if y1 > bottom_start and x1 > x0:
        rows = np.where(mask[bottom_start:y1, x0:x1].mean(axis=1) >= density_threshold)[0] + bottom_start
        clear_rows(rows)
    left_end = min(x1, x0 + search)
    if left_end > x0 and y1 > y0:
        cols = np.where(mask[y0:y1, x0:left_end].mean(axis=0) >= density_threshold)[0] + x0
        clear_cols(cols)
    right_start = max(x0, x1 - search)
    if x1 > right_start and y1 > y0:
        cols = np.where(mask[y0:y1, right_start:x1].mean(axis=0) >= density_threshold)[0] + right_start
        clear_cols(cols)


def suppress_canvas_border(
    mask: np.ndarray,
    bounds: tuple[int, int, int, int] | None = None,
    margin_px: int | None = None,
) -> np.ndarray:
    """Remove frame-like lines hugging the canvas or placed-image edges."""

    cleaned = mask.copy()
    if cleaned.size == 0:
        return cleaned
    h, w = cleaned.shape
    margin = _border_margin_px((w, h), margin_px)
    _clear_frame_band(cleaned, (0, 0, w, h), margin)
    if bounds is not None:
        _clear_frame_band(cleaned, bounds, margin)
    return cleaned


def quality_check(png_path: Path, canvas_size: tuple[int, int] = (1024, 1024), threshold: int = 235) -> float:
    """Return foreground pixel ratio for a candidate line-art image.

    Example:
        >>> 0 <= quality_check(Path("missing.png")) <= 1  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
    """

    source, bounds = load_on_canvas_with_bounds(png_path, canvas_size)
    mask = suppress_canvas_border(binarize(np.asarray(source.convert("L")), threshold), bounds=bounds)
    return float(np.mean(mask))


def zhang_suen_skeleton(mask: np.ndarray, max_iterations: int = 160) -> np.ndarray:
    """Thin a binary mask into a 1px skeleton using Zhang-Suen thinning.

    Example:
        >>> zhang_suen_skeleton(np.ones((3, 3), dtype=bool)).shape
        (3, 3)
    """

    img = np.pad(mask.astype(np.uint8), 1, mode="constant")
    for _ in range(max_iterations):
        changed = False
        for step in (0, 1):
            p2, p3, p4 = img[:-2, 1:-1], img[:-2, 2:], img[1:-1, 2:]
            p5, p6, p7 = img[2:, 2:], img[2:, 1:-1], img[2:, :-2]
            p8, p9 = img[1:-1, :-2], img[:-2, :-2]
            center = img[1:-1, 1:-1]
            neighbors = [p2, p3, p4, p5, p6, p7, p8, p9]
            transitions = sum((neighbors[i] == 0) & (neighbors[(i + 1) % 8] == 1) for i in range(8))
            count = sum(neighbors)
            if step == 0:
                marker = (
                    (center == 1)
                    & (count >= 2)
                    & (count <= 6)
                    & (transitions == 1)
                    & ((p2 * p4 * p6) == 0)
                    & ((p4 * p6 * p8) == 0)
                )
            else:
                marker = (
                    (center == 1)
                    & (count >= 2)
                    & (count <= 6)
                    & (transitions == 1)
                    & ((p2 * p4 * p8) == 0)
                    & ((p2 * p6 * p8) == 0)
                )
            if np.any(marker):
                center[marker] = 0
                changed = True
        if not changed:
            break
    return img[1:-1, 1:-1].astype(bool)


def _skel_neighbors(skel: np.ndarray, point: tuple[int, int]) -> list[tuple[int, int]]:
    x, y = point
    h, w = skel.shape
    result = []
    for dx, dy in NEIGHBORS_8:
        nx, ny = x + dx, y + dy
        if not (0 <= nx < w and 0 <= ny < h and skel[ny, nx]):
            continue
        if dx != 0 and dy != 0 and (skel[y, nx] or skel[ny, x]):
            # An orthogonal bridge already connects these pixels. Ignoring the
            # redundant diagonal avoids tiny triangular strokes around T and
            # cross junctions while preserving true diagonal centerlines.
            continue
        result.append((nx, ny))
    return result


def _edge_key(a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
    return (a, b) if a <= b else (b, a)


def _choose_next(
    prev: tuple[int, int],
    cur: tuple[int, int],
    candidates: list[tuple[int, int]],
    visited_edges: set[tuple[tuple[int, int], tuple[int, int]]],
) -> tuple[int, int] | None:
    fresh = [p for p in candidates if _edge_key(cur, p) not in visited_edges and p != prev]
    if not fresh:
        return None
    vx, vy = cur[0] - prev[0], cur[1] - prev[1]
    return max(
        fresh,
        key=lambda p: ((vx * (p[0] - cur[0]) + vy * (p[1] - cur[1])) / (math.hypot(vx, vy) * math.hypot(p[0] - cur[0], p[1] - cur[1]) or 1.0)),
    )


def trace_8connected(skel: np.ndarray, min_points: int = 8) -> list[np.ndarray]:
    """Trace 8-connected skeleton pixels into continuous stroke arrays.

    At junctions, keep following the straightest unused edge instead of
    splitting every branch into a separate short stroke. Remaining branches
    are picked up by later starts. This better matches how a person would draw
    thick or jagged doodle contours with one continuous pen movement.

    Example:
        >>> trace_8connected(np.eye(8, dtype=bool), min_points=2)[0].shape[1]
        2
    """

    ys, xs = np.nonzero(skel)
    points = [(int(x), int(y)) for x, y in zip(xs, ys)]
    if not points:
        return []
    degrees = {p: len(_skel_neighbors(skel, p)) for p in points}
    starts = [p for p in points if degrees[p] == 1] + [p for p in points if degrees[p] > 2] + points
    visited_edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    strokes: list[np.ndarray] = []
    for start in starts:
        for nb in _skel_neighbors(skel, start):
            edge = _edge_key(start, nb)
            if edge in visited_edges:
                continue
            path = [start]
            prev, cur = start, nb
            visited_edges.add(edge)
            while True:
                path.append(cur)
                next_pt = _choose_next(prev, cur, _skel_neighbors(skel, cur), visited_edges)
                if next_pt is None:
                    break
                visited_edges.add(_edge_key(cur, next_pt))
                prev, cur = cur, next_pt
            if len(path) >= min_points:
                strokes.append(np.asarray(path, dtype=np.int32))
    return strokes


def _stroke_length(points: list[Point]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def _point_distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _unit_vector(a: Point, b: Point) -> Point:
    length = _point_distance(a, b)
    if length <= 1e-6:
        return (0.0, 0.0)
    return ((b[0] - a[0]) / length, (b[1] - a[1]) / length)


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _is_frame_stroke(points: list[Point], bounds: tuple[int, int, int, int], margin_px: int) -> bool:
    if len(points) < 2:
        return False
    x0, y0, x1, y1 = bounds
    width, height = max(1, x1 - x0), max(1, y1 - y0)
    xs = np.asarray([p[0] for p in points], dtype=float)
    ys = np.asarray([p[1] for p in points], dtype=float)
    min_x, max_x = float(xs.min()), float(xs.max())
    min_y, max_y = float(ys.min()), float(ys.max())
    span_x, span_y = max_x - min_x, max_y - min_y
    edge_margin = margin_px * 2
    touches = [
        min_x <= x0 + edge_margin,
        max_x >= x1 - edge_margin,
        min_y <= y0 + edge_margin,
        max_y >= y1 - edge_margin,
    ]
    if sum(touches) >= 3 and span_x >= width * 0.68 and span_y >= height * 0.68:
        return True
    if span_x >= width * 0.72 and (max_y <= y0 + edge_margin or min_y >= y1 - edge_margin):
        return True
    if span_y >= height * 0.72 and (max_x <= x0 + edge_margin or min_x >= x1 - edge_margin):
        return True
    near_edge = (xs <= x0 + margin_px) | (xs >= x1 - margin_px) | (ys <= y0 + margin_px) | (ys >= y1 - margin_px)
    return bool(float(np.mean(near_edge)) >= 0.82 and (span_x >= width * 0.55 or span_y >= height * 0.55))


def filter_canvas_border_strokes(
    strokes: list[Stroke],
    canvas_size: tuple[int, int],
    bounds: tuple[int, int, int, int] | None = None,
    margin_px: int | None = None,
) -> list[Stroke]:
    """Drop strokes that are likely image/canvas frame artifacts."""

    margin = _border_margin_px(canvas_size, margin_px)
    width, height = canvas_size
    frame_bounds = [(0, 0, width, height)]
    if bounds is not None and bounds != frame_bounds[0]:
        frame_bounds.append(bounds)
    return [stroke for stroke in strokes if not any(_is_frame_stroke(stroke.points, candidate, margin) for candidate in frame_bounds)]


def _merge_candidate(
    a: list[Point],
    b: list[Point],
    max_gap: float,
    min_dot: float,
    touch_gap: float,
    touch_min_dot: float,
    short_len: float,
    short_min_dot: float,
) -> list[Point] | None:
    if len(a) < 2 or len(b) < 2:
        return None
    variants = [
        (a, b, _unit_vector(a[-2], a[-1]), _unit_vector(b[0], b[1])),
        (a, list(reversed(b)), _unit_vector(a[-2], a[-1]), _unit_vector(b[-1], b[-2])),
        (b, a, _unit_vector(b[-2], b[-1]), _unit_vector(a[0], a[1])),
        (list(reversed(b)), a, _unit_vector(b[0], b[1]), _unit_vector(a[0], a[1])),
    ]
    best: tuple[float, list[Point]] | None = None
    for left, right, left_dir, right_dir in variants:
        gap = _point_distance(left[-1], right[0])
        dot = _dot(left_dir, right_dir)
        effective_min_dot = min_dot
        if gap <= touch_gap:
            effective_min_dot = touch_min_dot
        elif min(_stroke_length(left), _stroke_length(right)) <= short_len and gap <= max_gap * 0.55:
            effective_min_dot = short_min_dot
        if gap <= max_gap and dot >= effective_min_dot:
            merged = left + right[1:] if gap <= 1.25 else left + right
            if best is None or gap < best[0]:
                best = (gap, merged)
    return best[1] if best else None


def merge_nearby_strokes(
    strokes: list[Stroke],
    canvas_size: tuple[int, int],
    max_gap_px: float | None = None,
    max_angle_deg: float = 42.0,
    touch_angle_deg: float = 96.0,
    short_angle_deg: float = 72.0,
) -> list[Stroke]:
    """Merge short broken strokes when nearby endpoints have compatible tangents."""

    if len(strokes) < 2:
        return strokes
    max_gap = max_gap_px if max_gap_px is not None else max(9.0, min(canvas_size) * 0.016)
    min_dot = math.cos(math.radians(max_angle_deg))
    touch_gap = max(1.75, min(canvas_size) * 0.0022)
    touch_min_dot = math.cos(math.radians(touch_angle_deg))
    short_len = max(14.0, min(canvas_size) * 0.018)
    short_min_dot = math.cos(math.radians(short_angle_deg))
    remaining = [Stroke(points=list(stroke.points), color=stroke.color, source=stroke.source) for stroke in strokes if len(stroke.points) > 1]
    remaining.sort(key=lambda stroke: _stroke_length(stroke.points), reverse=True)
    changed = True
    passes = 0
    while changed and passes < 5:
        changed = False
        passes += 1
        idx = 0
        while idx < len(remaining):
            base = remaining[idx]
            best_index: int | None = None
            best_points: list[Point] | None = None
            best_gain = float("inf")
            for candidate_index, candidate in enumerate(remaining):
                if candidate_index == idx:
                    continue
                merged = _merge_candidate(base.points, candidate.points, max_gap, min_dot, touch_gap, touch_min_dot, short_len, short_min_dot)
                if merged is None:
                    continue
                gain = _stroke_length(merged) - _stroke_length(base.points) - _stroke_length(candidate.points)
                if gain < best_gain:
                    best_gain = gain
                    best_index = candidate_index
                    best_points = merged
            if best_index is not None and best_points is not None:
                base.points = best_points
                base.source = f"{base.source}+merged"
                del remaining[best_index]
                changed = True
                if best_index < idx:
                    idx -= 1
            idx += 1
    return remaining


def _resample_points(points: list[Point], spacing: float = 3.0) -> list[Point]:
    if len(points) < 2:
        return points
    total = _stroke_length(points)
    if total <= spacing:
        return [points[0], points[-1]]
    targets = np.arange(0.0, total, spacing).tolist()
    if not targets or targets[-1] < total:
        targets.append(total)
    result: list[Point] = []
    segment_index = 0
    traversed = 0.0
    for target in targets:
        while segment_index < len(points) - 2:
            seg_len = _point_distance(points[segment_index], points[segment_index + 1])
            if traversed + seg_len >= target:
                break
            traversed += seg_len
            segment_index += 1
        a, b = points[segment_index], points[segment_index + 1]
        seg_len = max(_point_distance(a, b), 1e-6)
        local = max(0.0, min(1.0, (target - traversed) / seg_len))
        result.append((a[0] + (b[0] - a[0]) * local, a[1] + (b[1] - a[1]) * local))
    return result


def _chaikin_smooth(points: list[Point], iterations: int = 1) -> list[Point]:
    if len(points) < 4:
        return points
    result = points
    for _ in range(iterations):
        smoothed: list[Point] = [result[0]]
        for a, b in zip(result, result[1:]):
            smoothed.append((a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25))
            smoothed.append((a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75))
        smoothed.append(result[-1])
        result = smoothed
    return result


def smooth_strokes(strokes: list[Stroke], spacing: float = 3.0) -> list[Stroke]:
    """Resample and lightly smooth stroke coordinates to reduce pixel stair-steps."""

    smoothed: list[Stroke] = []
    for stroke in strokes:
        if len(stroke.points) < 2:
            continue
        points = _resample_points(stroke.points, spacing=spacing)
        points = _chaikin_smooth(points, iterations=1)
        points = _resample_points(points, spacing=spacing)
        if len(points) > 1 and _stroke_length(points) > 2.0:
            smoothed.append(Stroke(points=points, color=stroke.color, source=f"{stroke.source}+smooth"))
    return smoothed


def postprocess_strokes(
    strokes: list[Stroke],
    canvas_size: tuple[int, int],
    smooth: bool = True,
    merge: bool = True,
    smooth_spacing: float | None = None,
    merge_gap_px: float | None = None,
    min_length_px: float = 0.0,
    max_merge_strokes: int | None = 700,
) -> list[Stroke]:
    """Apply continuity improvements before final stroke ordering."""

    processed = strokes
    if merge and (max_merge_strokes is None or len(processed) <= max_merge_strokes):
        processed = merge_nearby_strokes(processed, canvas_size, max_gap_px=merge_gap_px)
    if smooth:
        spacing = smooth_spacing if smooth_spacing is not None else max(2.25, min(canvas_size) * 0.0035)
        processed = smooth_strokes(processed, spacing=spacing)
    if min_length_px > 0:
        processed = [stroke for stroke in processed if _stroke_length(stroke.points) >= min_length_px]
    return order_strokes(processed, canvas_size)


def _stroke_bounds(points: list[Point]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _stroke_center(points: list[Point]) -> Point:
    x0, y0, x1, y1 = _stroke_bounds(points)
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _canvas_from_strokes(strokes: list[Stroke]) -> tuple[int, int]:
    if not strokes:
        return (1, 1)
    max_x = max(_stroke_bounds(stroke.points)[2] for stroke in strokes)
    max_y = max(_stroke_bounds(stroke.points)[3] for stroke in strokes)
    return (max(1, int(math.ceil(max_x + 1))), max(1, int(math.ceil(max_y + 1))))


def order_strokes(strokes: list[Stroke], canvas_size: tuple[int, int] | None = None) -> list[Stroke]:
    """Order strokes top-to-bottom, left-to-right, with local continuity."""

    remaining = [s for s in strokes if len(s.points) > 1 and _stroke_length(s.points) > 1.0]
    ordered: list[Stroke] = []
    current: Point | None = None
    last_center: Point | None = None
    width, height = canvas_size or _canvas_from_strokes(remaining)
    row_height = max(48.0, min(160.0, height * 0.12))

    def row_of(stroke: Stroke) -> int:
        _x0, y0, _x1, _y1 = _stroke_bounds(stroke.points)
        return int(y0 // row_height)

    while remaining:
        if current is None:
            index = min(
                range(len(remaining)),
                key=lambda i: (
                    row_of(remaining[i]),
                    _stroke_bounds(remaining[i].points)[0],
                    _stroke_bounds(remaining[i].points)[1],
                    -_stroke_length(remaining[i].points),
                ),
            )
        else:
            def score(idx: int) -> float:
                stroke = remaining[idx]
                start, end = stroke.points[0], stroke.points[-1]
                x0, y0, x1, y1 = _stroke_bounds(stroke.points)
                cx, cy = _stroke_center(stroke.points)
                dist = min(math.hypot(start[0] - current[0], start[1] - current[1]), math.hypot(end[0] - current[0], end[1] - current[1]))
                length = _stroke_length(stroke.points)
                visual = x0 * 0.55 + y0 * 0.18
                continuity = dist * 0.32
                long_line_bonus = min(length, 900.0) * 0.055
                backtrack_penalty = 0.0
                if last_center is not None:
                    if cy < last_center[1] - row_height * 0.75:
                        backtrack_penalty += row_height * 4.0
                    if abs(cy - last_center[1]) <= row_height and cx < last_center[0] - width * 0.18:
                        backtrack_penalty += width * 0.35
                return visual + continuity + backtrack_penalty - long_line_bonus

            min_row = min(row_of(stroke) for stroke in remaining)
            candidates = [idx for idx, stroke in enumerate(remaining) if row_of(stroke) == min_row]
            index = min(candidates, key=score)
        stroke = remaining.pop(index)
        if current is not None:
            start, end = stroke.points[0], stroke.points[-1]
            if math.hypot(end[0] - current[0], end[1] - current[1]) < math.hypot(start[0] - current[0], start[1] - current[1]):
                stroke.points = list(reversed(stroke.points))
        ordered.append(stroke)
        current = stroke.points[-1]
        last_center = _stroke_center(stroke.points)
    return ordered


def _detail_preset(stroke_detail: str, canvas_size: tuple[int, int]) -> dict[str, float | int | bool]:
    if stroke_detail not in STROKE_DETAIL_PRESETS:
        raise ValueError(f"Unknown stroke detail preset: {stroke_detail}")
    preset = dict(STROKE_DETAIL_PRESETS[stroke_detail])
    min_side = min(canvas_size)
    if preset["smooth_spacing"] == "rich":
        preset["smooth_spacing"] = max(1.65, min_side * 0.0026)
    elif preset["smooth_spacing"] == "max":
        preset["smooth_spacing"] = max(1.2, min_side * 0.0019)
    if preset["merge_gap"] == "rich":
        preset["merge_gap"] = max(7.0, min_side * 0.014)
    elif preset["merge_gap"] == "max":
        preset["merge_gap"] = max(4.0, min_side * 0.0075)
    if preset["min_length"] == "rich":
        preset["min_length"] = max(9.0, min_side * 0.0115)
    return preset


def to_strokes(
    png_path: Path,
    canvas_size: tuple[int, int],
    threshold: int | None = None,
    stroke_detail: str = "rich",
) -> list[Stroke]:
    """Convert raster line art into ordered stroke paths.

    Example:
        >>> isinstance(to_strokes(Path("missing.png"), (640, 360)), list)  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
        ...
    """

    detail = _detail_preset(stroke_detail, canvas_size)
    threshold = int(threshold if threshold is not None else detail["threshold"])
    source, bounds = load_on_canvas_with_bounds(png_path, canvas_size)
    mask = suppress_canvas_border(binarize(np.asarray(source.convert("L")), threshold), bounds=bounds)
    if not np.any(mask):
        return []
    skel = zhang_suen_skeleton(mask)
    paths = trace_8connected(skel, min_points=int(detail["min_points"]))
    strokes = [Stroke(points=[(float(x), float(y)) for x, y in path], source="skeleton") for path in paths]
    strokes = filter_canvas_border_strokes(strokes, canvas_size, bounds=bounds)
    return postprocess_strokes(
        strokes,
        canvas_size,
        smooth=True,
        merge=bool(detail["merge"]),
        smooth_spacing=float(detail["smooth_spacing"]) if detail["smooth_spacing"] is not None else None,
        merge_gap_px=float(detail["merge_gap"]) if detail["merge_gap"] is not None else None,
        min_length_px=float(detail["min_length"]),
        max_merge_strokes=int(detail["max_merge_strokes"]) if detail["max_merge_strokes"] is not None else None,
    )


def _parse_float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    match = re.search(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", value)
    return float(match.group(0)) if match else default


def _parse_points(points_text: str | None) -> list[Point]:
    if not points_text:
        return []
    nums = [float(x) for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", points_text)]
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]


def _sample_cubic(p0: Point, p1: Point, p2: Point, p3: Point, steps: int = 24) -> list[Point]:
    pts = []
    for idx in range(1, steps + 1):
        t, mt = idx / steps, 1.0 - idx / steps
        pts.append(
            (
                mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0],
                mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1],
            )
        )
    return pts


def _sample_quad(p0: Point, p1: Point, p2: Point, steps: int = 18) -> list[Point]:
    pts = []
    for idx in range(1, steps + 1):
        t, mt = idx / steps, 1.0 - idx / steps
        pts.append((mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0], mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1]))
    return pts


def _parse_svg_path(d: str) -> list[list[Point]]:
    tokens = re.findall(r"[MmLlHhVvCcQqAaZz]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", d)
    strokes: list[list[Point]] = []
    idx, cmd = 0, ""
    current = start = (0.0, 0.0)
    stroke: list[Point] = []

    def is_cmd(token: str) -> bool:
        return bool(re.match(r"^[A-Za-z]$", token))

    def read_num() -> float:
        nonlocal idx
        value = float(tokens[idx])
        idx += 1
        return value

    while idx < len(tokens):
        if is_cmd(tokens[idx]):
            cmd = tokens[idx]
            idx += 1
        if not cmd:
            break
        absolute, code = cmd.isupper(), cmd.upper()
        if code == "M":
            x, y = read_num(), read_num()
            if not absolute:
                x, y = x + current[0], y + current[1]
            if len(stroke) > 1:
                strokes.append(stroke)
            current = start = (x, y)
            stroke = [current]
            cmd = "L" if absolute else "l"
        elif code == "L":
            while idx < len(tokens) and not is_cmd(tokens[idx]):
                x, y = read_num(), read_num()
                current = (x, y) if absolute else (x + current[0], y + current[1])
                stroke.append(current)
        elif code == "H":
            while idx < len(tokens) and not is_cmd(tokens[idx]):
                x = read_num()
                current = (x if absolute else x + current[0], current[1])
                stroke.append(current)
        elif code == "V":
            while idx < len(tokens) and not is_cmd(tokens[idx]):
                y = read_num()
                current = (current[0], y if absolute else y + current[1])
                stroke.append(current)
        elif code == "C":
            while idx < len(tokens) and not is_cmd(tokens[idx]):
                p1, p2, p3 = (read_num(), read_num()), (read_num(), read_num()), (read_num(), read_num())
                if not absolute:
                    p1 = (p1[0] + current[0], p1[1] + current[1])
                    p2 = (p2[0] + current[0], p2[1] + current[1])
                    p3 = (p3[0] + current[0], p3[1] + current[1])
                stroke.extend(_sample_cubic(current, p1, p2, p3))
                current = p3
        elif code == "Q":
            while idx < len(tokens) and not is_cmd(tokens[idx]):
                p1, p2 = (read_num(), read_num()), (read_num(), read_num())
                if not absolute:
                    p1 = (p1[0] + current[0], p1[1] + current[1])
                    p2 = (p2[0] + current[0], p2[1] + current[1])
                stroke.extend(_sample_quad(current, p1, p2))
                current = p2
        elif code == "A":
            while idx < len(tokens) and not is_cmd(tokens[idx]):
                _rx, _ry, _rot, _large, _sweep = read_num(), read_num(), read_num(), read_num(), read_num()
                x, y = read_num(), read_num()
                current = (x, y) if absolute else (x + current[0], y + current[1])
                stroke.append(current)
        elif code == "Z":
            if stroke and stroke[-1] != start:
                stroke.append(start)
            if len(stroke) > 1:
                strokes.append(stroke)
            stroke, current, cmd = [], start, ""
        else:
            while idx < len(tokens) and not is_cmd(tokens[idx]):
                idx += 1
    if len(stroke) > 1:
        strokes.append(stroke)
    return strokes


def _sample_ellipse(cx: float, cy: float, rx: float, ry: float, steps: int = 80) -> list[Point]:
    return [(cx + math.cos(2 * math.pi * i / steps) * rx, cy + math.sin(2 * math.pi * i / steps) * ry) for i in range(steps + 1)]


def svg_to_strokes(svg_path: Path, canvas_size: tuple[int, int]) -> tuple[list[Stroke], Image.Image]:
    """Parse SVG primitives into stroke paths and a preview image."""

    width, height = canvas_size
    root = ET.parse(svg_path).getroot()
    view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    if view_box:
        nums = [float(x) for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", view_box)]
        vb_x, vb_y, vb_w, vb_h = nums[:4]
    else:
        vb_x, vb_y, vb_w, vb_h = 0.0, 0.0, _parse_float(root.attrib.get("width"), 100.0), _parse_float(root.attrib.get("height"), 100.0)
    scale = min(width / max(vb_w, 1.0), height / max(vb_h, 1.0))
    off_x, off_y = (width - vb_w * scale) / 2.0, (height - vb_h * scale) / 2.0
    content_bounds = (
        int(round(off_x)),
        int(round(off_y)),
        int(round(off_x + vb_w * scale)),
        int(round(off_y + vb_h * scale)),
    )

    def map_pt(point: Point) -> Point:
        return ((point[0] - vb_x) * scale + off_x, (point[1] - vb_y) * scale + off_y)

    strokes: list[Stroke] = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()
        raw: list[list[Point]] = []
        if tag == "path" and elem.attrib.get("d"):
            raw.extend(_parse_svg_path(elem.attrib["d"]))
        elif tag in {"polyline", "polygon"}:
            pts = _parse_points(elem.attrib.get("points"))
            raw.append(pts + ([pts[0]] if tag == "polygon" and pts else []))
        elif tag == "line":
            raw.append([(_parse_float(elem.attrib.get("x1")), _parse_float(elem.attrib.get("y1"))), (_parse_float(elem.attrib.get("x2")), _parse_float(elem.attrib.get("y2")))])
        elif tag == "rect":
            x, y = _parse_float(elem.attrib.get("x")), _parse_float(elem.attrib.get("y"))
            w, h = _parse_float(elem.attrib.get("width")), _parse_float(elem.attrib.get("height"))
            raw.append([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)])
        elif tag == "circle":
            cx, cy, r = _parse_float(elem.attrib.get("cx")), _parse_float(elem.attrib.get("cy")), _parse_float(elem.attrib.get("r"))
            raw.append(_sample_ellipse(cx, cy, r, r))
        elif tag == "ellipse":
            raw.append(_sample_ellipse(_parse_float(elem.attrib.get("cx")), _parse_float(elem.attrib.get("cy")), _parse_float(elem.attrib.get("rx")), _parse_float(elem.attrib.get("ry"))))
        for pts in raw:
            mapped = [map_pt(pt) for pt in pts]
            if len(mapped) > 1:
                strokes.append(Stroke(mapped, source="svg"))
    strokes = postprocess_strokes(filter_canvas_border_strokes(strokes, canvas_size, bounds=content_bounds), canvas_size, smooth=False, merge=True)
    preview = Image.new("RGB", canvas_size, "white")
    draw = ImageDraw.Draw(preview)
    for stroke in strokes:
        draw.line(stroke.points, fill=stroke.color, width=2, joint="curve")
    return strokes, preview
