from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

import whiteboard_skill.whiteboard as whiteboard
from whiteboard_skill.models import Annotation, TimingCue
from whiteboard_skill.preprocess import Stroke, StrokeBlock
from whiteboard_skill.whiteboard import (
    SKETCH_INK_COLOR,
    _advance_timeline,
    _annotation_cursor_pose,
    _annotation_render_states,
    _annotation_typewriter_mask,
    _base_sketch_ink_mask,
    _block_animation_end,
    _block_timeline_start,
    _block_windows,
    _build_block_region_masks,
    _color_fill_frame,
    _complete_line_art_canvas,
    _crayon_left_right_fill,
    _detail_wipe_frame,
    _estimate_line_art_width,
    _horizontal_gradient_mask,
    _layout_annotation,
    _layout_text,
    _line_art_ink_mask,
    _line_art_tone_layer,
    _load_source_image_canvas,
    _narration_paced_time,
    _naturalize_story_strokes,
    _naturalize_stroke_path,
    _prepare_timeline,
    _resolve_line_thickness,
    _reveal_line_art_canvas,
    _simplify_ink_mask,
    _stroke_segment_between,
    _text_to_strokes,
    _text_wipe_strokes,
    _top_down_block_fill,
    split_story_strokes,
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


def test_narration_paced_time_holds_drawing_between_phrase_cues():
    cues = [
        TimingCue(text="和尚上山", start_sec=1.0, end_sec=2.0, draw_to=0.4),
        TimingCue(text="发现水缸", start_sec=4.0, end_sec=6.0, draw_to=1.0),
    ]

    assert _narration_paced_time(0.5 / 8, cues, 8.0, 0.75) == 0.0
    assert _narration_paced_time(1.5 / 8, cues, 8.0, 0.75) == pytest.approx(0.15)
    assert _narration_paced_time(3.0 / 8, cues, 8.0, 0.75) == pytest.approx(0.30)
    assert _narration_paced_time(5.0 / 8, cues, 8.0, 0.75) == pytest.approx(0.525)
    assert _narration_paced_time(7.0 / 8, cues, 8.0, 0.75) == pytest.approx(0.75)


def test_narration_paced_time_is_identity_without_cues():
    assert _narration_paced_time(0.423, [], 8.0, 0.75) == 0.423


def test_stroke_segment_between_interpolates_partial_path():
    stroke = Stroke(points=[(0, 0), (10, 0), (10, 10)])
    timeline = _prepare_timeline([stroke], draw_frames=30)[0]

    segment = _stroke_segment_between(stroke.points, timeline.cumulative, 5, 15)
    assert segment[0] == (5, 0)
    assert segment[-1] == (10, 5)


def test_naturalize_stroke_path_reduces_pixel_stair_steps_and_keeps_real_corner():
    staircase = [(float(x), float(x % 2)) for x in range(25)]

    natural = _naturalize_stroke_path(staircase, (1920, 1080))
    corner = _naturalize_stroke_path(
        [(0.0, 0.0), (12.0, 0.0), (12.0, 12.0)],
        (1920, 1080),
    )

    assert natural[0] == staircase[0]
    assert natural[-1] == staircase[-1]
    assert len(natural) < len(staircase) / 2
    assert (12.0, 0.0) in corner


def test_naturalize_story_strokes_only_drops_microscopic_raster_fragments():
    tiny_raster = Stroke(
        points=[(2.0, 2.0), (3.0, 2.0)],
        source="skeleton",
    )
    eye_detail = Stroke(
        points=[(10.0, 10.0), (16.0, 10.0)],
        source="skeleton",
    )
    tiny_vector = Stroke(
        points=[(20.0, 20.0), (21.0, 20.0)],
        source="svg",
    )

    natural = _naturalize_story_strokes(
        [tiny_raster, eye_detail, tiny_vector],
        (1920, 1080),
    )

    assert len(natural) == 2
    assert any(stroke.source.startswith("skeleton") for stroke in natural)
    assert any(stroke.source == "svg" for stroke in natural)


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


def test_horizontal_gradient_mask_reveals_left_before_right():
    mask = _horizontal_gradient_mask((100, 20), 0.5)

    assert mask.getpixel((10, 10)) > 240
    assert mask.getpixel((90, 10)) < 15
    assert 0 < mask.getpixel((50, 10)) < 255


def test_simplify_ink_mask_removes_thin_detail_but_keeps_heavy_lines():
    mask = Image.new("L", (30, 20), 0)
    for y in range(3, 17):
        for x in range(4, 9):
            mask.putpixel((x, y), 255)
    for x in range(12, 27):
        mask.putpixel((x, 10), 255)

    simplified = _simplify_ink_mask(mask)

    assert simplified.getpixel((6, 10)) == 255
    assert simplified.getpixel((20, 10)) == 0


def test_base_sketch_keeps_heavy_contours_and_neutral_black_regions():
    line_art = Image.new("RGB", (60, 40), "white")
    detail = Image.new("L", (60, 40), 0)
    source = Image.new("RGB", (60, 40), "white")
    for y in range(4, 36):
        for x in range(3, 8):
            line_art.putpixel((x, y), (0, 0, 0))
            detail.putpixel((x, y), 255)
        line_art.putpixel((20, y), (0, 0, 0))
        detail.putpixel((20, y), 255)
    for y in range(8, 24):
        for x in range(30, 38, 2):
            line_art.putpixel((x, y), (0, 0, 0))
            detail.putpixel((x, y), 255)
        for x in range(46, 54, 2):
            line_art.putpixel((x, y), (0, 0, 0))
            detail.putpixel((x, y), 255)
    for y in range(6, 26):
        for x in range(28, 40):
            source.putpixel((x, y), (20, 20, 20))
        for x in range(44, 56):
            source.putpixel((x, y), (15, 45, 150))

    base = _base_sketch_ink_mask(line_art, detail, source)

    assert base.getpixel((5, 20)) == 255
    assert base.getpixel((20, 20)) == 0
    assert base.getpixel((30, 16)) == 255
    assert base.getpixel((46, 16)) == 0


def test_detail_wipe_starts_with_base_and_adds_full_detail():
    detail = Image.new("L", (40, 20), 0)
    detail.putpixel((5, 10), 255)
    detail.putpixel((35, 10), 255)
    base = Image.new("L", (40, 20), 0)
    base.putpixel((5, 10), 255)

    first = _detail_wipe_frame(detail, 0.0, base_mask=base, base_opacity=0.5)
    final = _detail_wipe_frame(detail, 1.0, base_mask=base, base_opacity=0.5)

    assert first.getpixel((5, 10))[0] < 255
    assert first.getpixel((35, 10)) == (255, 255, 255)
    assert final.getpixel((35, 10))[0] < 128


def test_left_to_right_gradient_color_fill_reveals_left_first():
    canvas = Image.new("RGB", (100, 20), "white")
    source = Image.new("RGB", (100, 20), (200, 20, 20))

    frame, _cursor, _angle = _color_fill_frame(canvas, source, 0.5, mode="left-to-right-gradient")

    assert frame.getpixel((5, 10))[0] == 200
    assert frame.getpixel((95, 10)) == (255, 255, 255)


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


def test_complete_tonal_line_art_never_lightens_existing_color():
    canvas = Image.new("RGB", (20, 20), "white")
    canvas.putpixel((10, 10), (35, 25, 20))
    line_art = Image.new("L", (20, 20), 255)
    line_art.putpixel((10, 10), 180)

    completed = _complete_line_art_canvas(canvas, line_art.convert("RGB"))

    assert completed.getpixel((10, 10)) == (35, 25, 20)


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


def test_line_art_ink_mask_keeps_cleaned_light_pencil_but_rejects_noise():
    line_art = Image.new("L", (20, 10), 255)
    line_art.putpixel((5, 5), 223)
    line_art.putpixel((10, 5), 238)

    mask = _line_art_ink_mask(line_art.convert("RGB"), line_art.size)

    assert mask.getpixel((5, 5)) == 255
    assert mask.getpixel((10, 5)) == 0


def test_line_art_tone_layer_preserves_relative_pencil_weight():
    line_art = Image.new("L", (20, 10), 255)
    line_art.putpixel((5, 5), 32)
    line_art.putpixel((10, 5), 180)

    tones = _line_art_tone_layer(line_art, line_art.size).convert("L")

    assert tones.getpixel((5, 5)) < tones.getpixel((10, 5)) < 255
    assert tones.getpixel((0, 0)) == 255


def test_text_to_strokes_generates_drawable_paths():
    strokes = _text_to_strokes("Hi", (240, 160))

    assert strokes
    assert all(len(stroke.points) >= 2 for stroke in strokes)


def test_layout_text_preserves_newlines_and_wraps_chinese():
    layout = _layout_text(
        "第一行\n这是一段需要自动换行的中文文案",
        (360, 480),
        "top",
        align="left",
        width_ratio=0.55,
        max_height_ratio=0.5,
        font_size=28,
    )

    assert layout.lines[0] == "第一行"
    assert len(layout.lines) >= 3
    assert layout.mask.getbbox() is not None
    assert all(right - left <= 198 for left, _top, right, _bottom in layout.line_boxes)
    assert [box[1] for box in layout.line_boxes] == sorted(box[1] for box in layout.line_boxes)


def test_annotation_typewriter_reveals_chinese_one_glyph_at_a_time():
    layout = _layout_annotation(
        Annotation(text="没水！", x=0.10, y=0.12),
        (640, 360),
    )

    partial = _annotation_typewriter_mask(layout, 0.5)
    first = layout.glyph_boxes[0]
    last = layout.glyph_boxes[-1]

    assert len(layout.glyph_boxes) == 3
    assert partial.crop(first).getbbox() is not None
    assert partial.crop(last).getbbox() is None
    assert _annotation_typewriter_mask(layout, 1.0).getextrema() == (255, 255)


def test_annotation_cursor_tracks_active_glyph_and_stops_after_reveal():
    layout = _layout_annotation(
        Annotation(text="没水！", x=0.10, y=0.12),
        (640, 360),
    )

    first_pose = _annotation_cursor_pose(layout, 0.10)
    last_pose = _annotation_cursor_pose(layout, 0.90)

    assert first_pose is not None and last_pose is not None
    assert first_pose[0][0] < last_pose[0][0]
    assert _annotation_cursor_pose(layout, 1.0) is None


def test_annotation_font_stays_small_at_full_hd():
    layout = _layout_annotation(Annotation(text="水缸"), (1920, 1080))

    assert 36 <= layout.font_size <= 46


def test_annotations_are_scheduled_late_and_finish_quickly():
    layouts = [
        _layout_annotation(Annotation(text="滑轮", x=0.58, y=0.16), (640, 360)),
        _layout_annotation(Annotation(text="竹槽", x=0.72, y=0.54), (640, 360)),
    ]

    states = _annotation_render_states(
        layouts,
        total_frames=210,
        fps=30,
        animation_end=5 / 7,
    )

    assert states[0].start >= (5 / 7) * 0.84
    assert states[-1].end <= (5 / 7) * 0.99
    assert states[1].start > states[0].end
    durations = [(state.end - state.start) * 7 for state in states]
    assert all(0.3 - 1e-6 <= duration <= 0.6 + 1e-6 for duration in durations)


def test_one_short_annotation_waits_until_drawing_is_almost_complete():
    layout = _layout_annotation(Annotation(text="没水！"), (640, 360))

    state = _annotation_render_states(
        [layout],
        total_frames=240,
        fps=30,
        animation_end=1.0,
    )[0]

    assert state.start == pytest.approx(0.94)
    assert state.end < 0.99


def test_block_annotation_stays_out_of_drawing_strokes(monkeypatch, tmp_path: Path):
    image = tmp_path / "lineart.png"
    Image.new("RGB", (96, 64), "white").save(image)
    image_strokes = [Stroke(points=[(4.0, 4.0), (80.0, 40.0)], source="skeleton")]
    captured: dict[str, object] = {}

    monkeypatch.setattr(whiteboard, "to_strokes", lambda *_args, **_kwargs: image_strokes)

    def fake_block_render(*args, **kwargs):
        captured["strokes"] = args[1]
        captured["annotations"] = kwargs["annotations"]
        captured["story_text"] = kwargs["story_text"]
        captured["timing_cues"] = kwargs["timing_cues"]

    monkeypatch.setattr(whiteboard, "render_block_story_scene", fake_block_render)

    whiteboard.render_image(
        image,
        tmp_path / "scene.mp4",
        resolution=(96, 64),
        animation_preset="block-speedpaint",
        draw_text="没水！",
        timing_cues=[TimingCue(text="水缸空了", start_sec=0.0, end_sec=1.0)],
    )

    assert captured["strokes"] == image_strokes
    assert [item.text for item in captured["annotations"]] == ["没水！"]
    assert [item.text for item in captured["timing_cues"]] == ["水缸空了"]

    legacy_caption = "这是一整句兼容旧参数的故事字幕，不应当被当作十二字以内的注释。"
    whiteboard.render_image(
        image,
        tmp_path / "caption.mp4",
        resolution=(96, 64),
        animation_preset="block-speedpaint",
        story_text=legacy_caption,
    )

    assert captured["annotations"] == []
    assert captured["strokes"] == image_strokes
    assert captured["story_text"] == legacy_caption


def test_text_wipe_creates_one_wide_reveal_path_per_visible_line():
    layout = _layout_text("第一行\n第二行", (360, 480), "top", font_size=30)

    strokes = _text_wipe_strokes(layout)

    assert len(strokes) == 2
    assert all(stroke.source == "text-wipe" for stroke in strokes)
    assert all(stroke.reveal_width and stroke.reveal_width > 30 for stroke in strokes)
    assert all(stroke.points[0][0] < stroke.points[-1][0] for stroke in strokes)


def test_split_story_strokes_keeps_long_path_in_coarse_group():
    long_stroke = Stroke(points=[(0, 0), (100, 0)])
    short_strokes = [
        Stroke(points=[(float(i), 10), (float(i + 2), 10)])
        for i in range(8)
    ]

    coarse, details = split_story_strokes(
        [*short_strokes[:4], long_stroke, *short_strokes[4:]]
    )

    assert long_stroke in coarse
    assert details


def test_advance_timeline_returns_latest_pen_pose():
    canvas = Image.new("RGB", (120, 80), "white")
    strokes = [Stroke(points=[(10, 20), (90, 20)])]
    timeline = _prepare_timeline(strokes, draw_frames=20)

    pose = _advance_timeline(
        ImageDraw.Draw(canvas),
        timeline,
        [0.0],
        0.5,
        2,
        1,
    )

    assert pose is not None
    assert 10 < pose[0][0] < 90


def test_advance_timeline_can_reveal_original_ink_without_drawing_guide():
    canvas = Image.new("RGB", (120, 80), "white")
    reveal = Image.new("L", (120, 80), 0)
    strokes = [Stroke(points=[(10, 20), (90, 20)])]
    timeline = _prepare_timeline(strokes, draw_frames=20)

    pose = _advance_timeline(
        ImageDraw.Draw(canvas),
        timeline,
        [0.0],
        0.5,
        2,
        1,
        reveal_draw=ImageDraw.Draw(reveal),
        reveal_width=9,
        draw_guide=False,
    )

    assert pose is not None
    assert canvas.getbbox() == (0, 0, 120, 80)
    assert canvas.getpixel((20, 20)) == (255, 255, 255)
    assert reveal.getbbox() is not None
    assert reveal.getpixel((20, 20)) == 255


def test_block_windows_give_dense_block_more_time():
    windows = _block_windows(2, overlap=0.0, weights=[1.0, 100.0])

    first_duration = windows[0][1] - windows[0][0]
    second_duration = windows[1][1] - windows[1][0]
    assert second_duration > first_duration
    assert round(windows[-1][1], 6) == 0.94


def test_block_animation_end_reserves_exact_tail_hold():
    assert _block_animation_end(300, 30, 2.0) == 0.8
    assert _block_animation_end(30, 30, 5.0) == pytest.approx(1 / 30)


def test_block_timeline_only_reserves_lead_in_for_full_caption():
    assert _block_timeline_start(False, 0.8) == 0.0
    assert _block_timeline_start(True, 0.8) == pytest.approx(0.024)


def test_block_region_masks_are_disjoint():
    blocks = [
        StrokeBlock(0, [], (0, 0, 70, 60), 10.0, []),
        StrokeBlock(1, [], (40, 0, 100, 60), 10.0, []),
    ]

    masks = _build_block_region_masks(blocks, (100, 60))

    assert len(masks) == 2
    assert not ((masks[0] > 0) & (masks[1] > 0)).any()
    assert masks[0][30, 10] == 1
    assert masks[1][30, 90] == 1


def test_crayon_fill_respects_block_region():
    canvas = Image.new("RGB", (100, 40), "white")
    source = Image.new("RGB", (100, 40), "white")
    ImageDraw.Draw(source).rectangle((5, 5, 94, 34), fill=(40, 100, 220))
    region = np.zeros((40, 100), dtype=np.float32)
    region[:, :50] = 1.0

    frame, _cursor, _angle = _crayon_left_right_fill(
        canvas,
        source,
        progress=1.0,
        region_mask=region,
        bounds=(0, 0, 50, 40),
    )

    assert frame.getpixel((20, 20)) != (255, 255, 255)
    assert frame.getpixel((80, 20)) == (255, 255, 255)
