from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from whiteboard_skill.preprocess import Stroke, binarize, merge_nearby_strokes, order_strokes, smooth_strokes, svg_to_strokes, to_strokes, trace_8connected, zhang_suen_skeleton


def test_binarize_marks_dark_pixels():
    assert binarize(np.array([[255, 0]], dtype=np.uint8)).tolist() == [[False, True]]


def test_skeleton_preserves_shape():
    mask = np.zeros((9, 9), dtype=bool)
    mask[2:7, 4] = True
    skel = zhang_suen_skeleton(mask)
    assert skel.sum() > 0


def test_trace_8connected_continues_straight_through_junctions():
    skeleton = np.zeros((13, 13), dtype=bool)
    skeleton[6, 2:11] = True
    skeleton[6:11, 6] = True

    strokes = trace_8connected(skeleton, min_points=2)

    assert len(strokes) == 2
    assert any(path[:, 0].min() == 2 and path[:, 0].max() == 10 for path in strokes)


def test_svg_to_strokes_fixture():
    strokes, preview = svg_to_strokes(Path("tests/fixtures/apple.svg"), (320, 240))
    assert strokes
    assert preview.size == (320, 240)


def test_raster_to_strokes_ignores_placed_image_border(tmp_path):
    image = Image.new("RGB", (80, 80), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 79, 79), outline="black", width=2)
    draw.line((20, 20, 60, 60), fill="black", width=3)
    path = tmp_path / "framed.png"
    image.save(path)

    strokes = to_strokes(path, (120, 200))
    assert strokes
    points = [point for stroke in strokes for point in stroke.points]
    assert min(y for _, y in points) > 52
    assert max(y for _, y in points) < 148


def test_svg_to_strokes_ignores_viewbox_border(tmp_path):
    svg = tmp_path / "framed.svg"
    svg.write_text(
        """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
          <rect x="0" y="0" width="100" height="100" fill="none" stroke="black"/>
          <line x1="20" y1="20" x2="80" y2="80" stroke="black"/>
        </svg>
        """,
        encoding="utf-8",
    )

    strokes, _ = svg_to_strokes(svg, (120, 200))
    assert len(strokes) == 1
    points = [point for stroke in strokes for point in stroke.points]
    assert min(y for _, y in points) > 52
    assert max(y for _, y in points) < 148


def test_merge_nearby_strokes_joins_compatible_gaps():
    strokes = [
        Stroke(points=[(0, 0), (10, 0)]),
        Stroke(points=[(12, 0), (24, 0)]),
    ]

    merged = merge_nearby_strokes(strokes, (100, 100), max_gap_px=3)
    assert len(merged) == 1
    assert merged[0].points[0] == (0, 0)
    assert merged[0].points[-1] == (24, 0)


def test_merge_nearby_strokes_joins_touching_corners():
    strokes = [
        Stroke(points=[(0, 0), (10, 0)]),
        Stroke(points=[(10, 0), (10, 12)]),
    ]

    merged = merge_nearby_strokes(strokes, (100, 100), max_gap_px=3)
    assert len(merged) == 1
    assert merged[0].points[0] == (0, 0)
    assert merged[0].points[-1] == (10, 12)


def test_smooth_strokes_resamples_pixel_steps():
    strokes = [Stroke(points=[(0, 0), (5, 0), (5, 5), (10, 5)])]

    smoothed = smooth_strokes(strokes, spacing=2.0)
    assert len(smoothed) == 1
    assert len(smoothed[0].points) > 3


def test_rich_detail_keeps_short_marks_but_filters_micro_fragments(tmp_path):
    image = Image.new("RGB", (60, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.line((20, 30, 24, 30), fill="black", width=1)
    draw.line((20, 40, 29, 40), fill="black", width=1)
    path = tmp_path / "tiny-detail.png"
    image.save(path)

    rich = to_strokes(path, (60, 60), stroke_detail="rich")
    max_detail = to_strokes(path, (60, 60), stroke_detail="max")

    assert rich
    assert len(rich) < len(max_detail)


def test_order_strokes_prefers_top_to_bottom_left_to_right():
    lower = Stroke(points=[(10, 80), (30, 80)])
    upper_right = Stroke(points=[(70, 10), (90, 10)])
    upper_left = Stroke(points=[(5, 12), (25, 12)])

    ordered = order_strokes([lower, upper_right, upper_left], (100, 100))
    assert ordered[0] is upper_left
    assert ordered[1] is upper_right
    assert ordered[2] is lower
