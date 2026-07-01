from whiteboard_skill.preprocess import Stroke
from pathlib import Path

from PIL import Image

from whiteboard_skill.whiteboard import (
    SKETCH_INK_COLOR,
    _color_fill_frame,
    _complete_line_art_canvas,
    _estimate_line_art_width,
    _line_art_ink_mask,
    _load_source_image_canvas,
    _prepare_timeline,
    _resolve_line_thickness,
    _reveal_line_art_canvas,
    _stroke_segment_between,
    _text_to_strokes,
    _top_down_block_fill,
)


def test_resolve_line_thickness_adapts_unless_overridden():
    assert _resolve_line_thickness(0, 4) == 4
    assert _resolve_line_thickness(None, 3) == 3
    assert _resolve_line_thickness(1, 5) == 1


def test_prepare_timeline_keeps_stroke_intervals_ordered():
    strokes = [
        Stroke(points=[(0, 0), (20, 0)]),
        Stroke(points=[(30, 0), (40, 0)]),
    ]

    timeline = _prepare_timeline(strokes, draw_frames=60)
    assert len(timeline) == 2
    assert timeline[0].start_unit == 0
    assert timeline[0].end_unit <= timeline[0].pause_end_unit
    assert timeline[0].pause_end_unit <= timeline[1].start_unit


def test_prepare_timeline_drops_pauses_for_dense_short_duration():
    strokes = [Stroke(points=[(0, float(i)), (20, float(i))]) for i in range(80)]

    timeline = _prepare_timeline(strokes, draw_frames=30)

    assert timeline
    assert all(item.pause_end_unit == item.end_unit for item in timeline)


def test_stroke_segment_between_interpolates_partial_path():
    stroke = Stroke(points=[(0, 0), (10, 0), (10, 10)])
    timeline = _prepare_timeline([stroke], draw_frames=30)[0]

    segment = _stroke_segment_between(stroke.points, timeline.cumulative, 5, 15)
    assert segment[0] == (5, 0)
    assert segment[-1] == (10, 5)


def test_load_source_image_canvas_blur_fill_covers_portrait(tmp_path: Path):
    source = tmp_path / "source.png"
    Image.new("RGB", (100, 100), (200, 40, 40)).save(source)

    canvas = _load_source_image_canvas(source, (90, 160), "blur-fill")
    assert canvas.size == (90, 160)
    assert canvas.getpixel((2, 2)) != (255, 255, 255)


def test_load_source_image_canvas_exact_resizes_to_canvas(tmp_path: Path):
    source = tmp_path / "source.png"
    Image.new("RGB", (30, 60), (20, 80, 200)).save(source)

    canvas = _load_source_image_canvas(source, (90, 160), "exact")
    assert canvas.size == (90, 160)


def test_top_down_block_fill_reveals_hard_rows():
    canvas = Image.new("RGB", (10, 10), "white")
    source = Image.new("RGB", (10, 10), "black")

    frame = _top_down_block_fill(canvas, source, progress=0.5, blocks=2)
    assert frame.getpixel((5, 1)) == (0, 0, 0)
    assert frame.getpixel((5, 8)) == (255, 255, 255)


def test_brush_scan_fill_returns_moving_cursor():
    canvas = Image.new("RGB", (10, 10), "white")
    source = Image.new("RGB", (10, 10), "black")

    frame, cursor, angle = _color_fill_frame(canvas, source, progress=0.3, mode="brush-scan", blocks=4)
    assert cursor is not None
    assert frame.getpixel((1, 1)) == (0, 0, 0)
    assert angle in (0.0, 3.141592653589793)


def test_contour_wipe_fill_delays_color_at_ink_contours():
    canvas = Image.new("RGB", (40, 40), "white")
    for y in range(17, 20):
        for x in range(6, 34):
            canvas.putpixel((x, y), (18, 18, 18))
    source = Image.new("RGB", (40, 40), (210, 30, 30))

    frame, cursor, _angle = _color_fill_frame(canvas, source, progress=0.55, mode="contour-wipe", blocks=4)

    assert cursor is not None
    assert frame.getpixel((20, 5)) == (210, 30, 30)
    assert frame.getpixel((20, 18)) != (210, 30, 30)


def test_complete_line_art_canvas_restores_missing_ink():
    canvas = Image.new("RGB", (20, 20), "white")
    line_art = Image.new("RGB", (20, 20), "white")
    line_art.putpixel((10, 10), (0, 0, 0))

    completed = _complete_line_art_canvas(canvas, line_art)

    pixel = completed.getpixel((10, 10))
    assert all(SKETCH_INK_COLOR[idx] <= pixel[idx] < 255 for idx in range(3))


def test_complete_line_art_canvas_can_blend_missing_ink():
    canvas = Image.new("RGB", (20, 20), "white")
    line_art = Image.new("RGB", (20, 20), "white")
    line_art.putpixel((10, 10), (0, 0, 0))

    completed = _complete_line_art_canvas(canvas, line_art, alpha=0.5)

    assert completed.getpixel((10, 10))[0] > 18
    assert completed.getpixel((10, 10))[0] < 255


def test_complete_line_art_canvas_ignores_light_gray_noise_by_default():
    canvas = Image.new("RGB", (20, 20), "white")
    line_art = Image.new("RGB", (20, 20), "white")
    line_art.putpixel((10, 10), (238, 238, 238))

    completed = _complete_line_art_canvas(canvas, line_art)

    assert completed.getpixel((10, 10)) == (255, 255, 255)


def test_reveal_line_art_canvas_uses_reveal_mask():
    canvas = Image.new("RGB", (20, 20), "white")
    line_art = Image.new("RGB", (20, 20), "white")
    line_art.putpixel((5, 5), (0, 0, 0))
    line_art.putpixel((15, 15), (0, 0, 0))
    reveal = Image.new("L", (20, 20), 0)
    reveal.putpixel((5, 5), 255)

    frame = _reveal_line_art_canvas(canvas, line_art, reveal)

    pixel = frame.getpixel((5, 5))
    assert all(SKETCH_INK_COLOR[idx] <= pixel[idx] < 255 for idx in range(3))
    assert frame.getpixel((15, 15)) == (255, 255, 255)


def test_line_art_canvas_accepts_precomputed_ink_mask():
    canvas = Image.new("RGB", (20, 20), "white")
    line_art = Image.new("RGB", (20, 20), "white")
    line_art.putpixel((5, 5), (0, 0, 0))
    line_art.putpixel((8, 8), (0, 0, 0))
    reveal = Image.new("L", (20, 20), 0)
    reveal.putpixel((5, 5), 255)
    ink_mask = _line_art_ink_mask(line_art, (20, 20))

    completed = _complete_line_art_canvas(canvas, line_art)
    completed_cached = _complete_line_art_canvas(canvas, None, ink_mask=ink_mask)
    revealed = _reveal_line_art_canvas(canvas, line_art, reveal)
    revealed_cached = _reveal_line_art_canvas(canvas, None, reveal, ink_mask=ink_mask)

    assert completed.tobytes() == completed_cached.tobytes()
    assert revealed.tobytes() == revealed_cached.tobytes()


def test_estimate_line_art_width_from_ink_area():
    line_art = Image.new("RGB", (40, 40), "white")
    for y in range(19, 22):
        for x in range(5, 35):
            line_art.putpixel((x, y), (0, 0, 0))

    assert _estimate_line_art_width(line_art) >= 3


def test_line_art_ink_mask_softens_thick_source_lines():
    line_art = Image.new("RGB", (40, 40), "white")
    for y in range(18, 23):
        for x in range(5, 35):
            line_art.putpixel((x, y), (0, 0, 0))

    mask = _line_art_ink_mask(line_art, (40, 40))

    assert mask.getpixel((20, 20)) == 255
    assert 0 < mask.getpixel((20, 19)) < 255
    assert mask.getpixel((20, 18)) == 0


def test_text_to_strokes_generates_drawable_paths():
    strokes = _text_to_strokes("Hi", (240, 160))

    assert strokes
    assert all(len(stroke.points) >= 2 for stroke in strokes)
