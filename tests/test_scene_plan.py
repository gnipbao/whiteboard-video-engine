import json
from pathlib import Path

import pytest

from whiteboard_skill.scene_plan import load_scene_plan, scene_plan_payload


def _scene(scene_id: int = 1) -> dict[str, object]:
    return {
        "id": scene_id,
        "narration": "三个和尚来到山上的寺庙。",
        "image_prompt": "Three young monks outside a mountain temple",
        "duration_sec": 7.5,
    }


@pytest.mark.parametrize("wrapped", [False, True])
def test_load_scene_plan_accepts_ordered_list_or_scenes_object(tmp_path: Path, wrapped: bool):
    path = tmp_path / "scenes.json"
    scenes = [_scene()]
    payload: object = {"scenes": scenes} if wrapped else scenes
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = load_scene_plan(path)

    assert scene_plan_payload(result) == scenes


def test_load_scene_plan_requires_consecutive_ids(tmp_path: Path):
    path = tmp_path / "scenes.json"
    path.write_text(json.dumps([_scene(2)], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="must have id 1"):
        load_scene_plan(path)


def test_load_scene_plan_rejects_runtime_asset_fields(tmp_path: Path):
    path = tmp_path / "scenes.json"
    payload = _scene()
    payload["audio_path"] = "/tmp/untrusted.mp3"
    path.write_text(json.dumps([payload], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported fields: audio_path"):
        load_scene_plan(path)


def test_scene_plan_round_trips_positioned_annotations(tmp_path: Path):
    path = tmp_path / "scenes.json"
    payload = _scene()
    payload["annotations"] = [
        {"text": "滑轮", "x": 0.58, "y": 0.16},
        {"text": "竹槽", "x": 0.72, "y": 0.54},
    ]
    payload["timing_cues"] = [
        {"text": "装上滑轮", "start_sec": 0.5, "end_sec": 1.8, "draw_to": 0.55},
        {"text": "接好竹槽", "start_sec": 2.1, "end_sec": 3.6, "draw_to": 1.0},
    ]
    path.write_text(json.dumps([payload], ensure_ascii=False), encoding="utf-8")

    scenes = load_scene_plan(path)

    assert [annotation.text for annotation in scenes[0].annotations] == ["滑轮", "竹槽"]
    assert [cue.draw_to for cue in scenes[0].timing_cues] == [0.55, 1.0]
    assert scene_plan_payload(scenes) == [payload]


def test_scene_plan_rejects_subtitle_length_annotation(tmp_path: Path):
    path = tmp_path / "scenes.json"
    payload = _scene()
    payload["annotations"] = [
        {"text": "这不是短注释而是一整句旁白字幕", "x": 0.1, "y": 0.1}
    ]
    path.write_text(json.dumps([payload], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="at most 12 visible characters"):
        load_scene_plan(path)


@pytest.mark.parametrize(
    ("timing_cues", "message"),
    [
        (
            [
                {"text": "第一句", "start_sec": 0.0, "end_sec": 2.0},
                {"text": "重叠句", "start_sec": 1.5, "end_sec": 3.0},
            ],
            "ordered and non-overlapping",
        ),
        (
            [
                {"text": "第一句", "start_sec": 0.0, "end_sec": 1.0, "draw_to": 0.4},
                {"text": "第二句", "start_sec": 1.0, "end_sec": 2.0},
            ],
            "provided for every cue",
        ),
        (
            [
                {"text": "第一句", "start_sec": 0.0, "end_sec": 1.0, "draw_to": 0.7},
                {"text": "第二句", "start_sec": 1.0, "end_sec": 2.0, "draw_to": 0.9},
            ],
            "final timing cue draw_to must equal 1.0",
        ),
    ],
)
def test_scene_plan_rejects_invalid_timing_maps(
    tmp_path: Path,
    timing_cues: list[dict[str, object]],
    message: str,
):
    path = tmp_path / "scenes.json"
    payload = _scene()
    payload["timing_cues"] = timing_cues
    path.write_text(json.dumps([payload], ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_scene_plan(path)
