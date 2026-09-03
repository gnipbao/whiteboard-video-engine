import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from whiteboard_skill import pipeline
from whiteboard_skill.cli import _build_parser
from whiteboard_skill.config import settings
from whiteboard_skill.models import Annotation, Project, Scene, TimingCue
from whiteboard_skill.pipeline import (
    _render_fingerprint,
    _srt_timestamp,
    _write_srt_sidecar,
    run_pipeline,
)


def test_run_defaults_to_gpt_image_2_color_pipeline_and_block_animation():
    args = _build_parser().parse_args(["run", "story.md", "-o", "story.mp4"])

    assert args.image_model == "gpt-image-2"
    assert args.image_quality == "low"
    assert args.scene_assets == "auto"
    assert args.lineart_provider == "auto"
    assert args.animation_preset == "block-speedpaint"
    assert args.draw_blocks == 4
    assert args.block_overlap == 0.16
    assert args.fps == 30
    assert args.captions is False
    assert args.burn_subtitles is False
    assert args.subtitle_font == "sans-serif"
    assert args.subtitle_font_size == 16.0
    assert args.subtitle_margin_v == 22
    assert args.subtitle_outline == 1.6


def test_run_accepts_doubao_and_precomputed_storyboards():
    args = _build_parser().parse_args(
        [
            "run",
            "story.md",
            "-o",
            "story.mp4",
            "--tts-provider",
            "doubao",
            "--storyboard-dir",
            "storyboards",
            "--scene-plan",
            "scenes.json",
        ]
    )

    assert args.tts_provider == "doubao"
    assert str(args.storyboard_dir) == "storyboards"
    assert str(args.scene_plan) == "scenes.json"


def test_run_accepts_silent_mode_and_caption_opt_in():
    args = _build_parser().parse_args(
        [
            "run",
            "story.md",
            "-o",
            "story.mp4",
            "--tts-provider",
            "none",
            "--captions",
        ]
    )

    assert args.tts_provider == "none"
    assert args.captions is True


def test_run_accepts_burned_subtitle_style_options():
    args = _build_parser().parse_args(
        [
            "run",
            "story.md",
            "-o",
            "story.mp4",
            "--burn-subtitles",
            "--subtitle-font",
            "Hiragino Sans GB",
            "--subtitle-font-size",
            "15",
            "--subtitle-margin-v",
            "24",
            "--subtitle-outline",
            "1.4",
        ]
    )

    assert args.burn_subtitles is True
    assert args.subtitle_font == "Hiragino Sans GB"
    assert args.subtitle_font_size == 15.0
    assert args.subtitle_margin_v == 24
    assert args.subtitle_outline == 1.4


@pytest.mark.parametrize("legacy_flag", ["--captions", "--no-captions"])
def test_burn_subtitles_conflicts_with_legacy_caption_flags(legacy_flag: str):
    with pytest.raises(SystemExit):
        _build_parser().parse_args(
            [
                "run",
                "story.md",
                "-o",
                "story.mp4",
                legacy_flag,
                "--burn-subtitles",
            ]
        )


def test_pipeline_rejects_srt_output_before_provider_or_file_access(tmp_path: Path):
    with pytest.raises(ValueError, match="reserves that suffix"):
        run_pipeline(tmp_path / "missing-story.md", tmp_path / "story.SRT")


class _PlanTTS:
    default_voice = "plan-voice"
    file_extension = ".mp3"

    def synthesize(self, _text: str, out_path: Path, _voice: str) -> float:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"audio")
        return 2.0


class _PlanLineArt:
    name = "test-lineart"

    def extract(self, source: Path, output: Path) -> Path:
        with Image.open(source) as image:
            lineart = Image.new("RGB", image.size, "white")
        ImageDraw.Draw(lineart).line((5, 5, lineart.width - 5, lineart.height - 5), fill="black", width=2)
        output.parent.mkdir(parents=True, exist_ok=True)
        lineart.save(output)
        return output


def test_scene_plan_and_storyboards_skip_all_openai_providers(monkeypatch, tmp_path: Path):
    script = tmp_path / "three-monks.md"
    script.write_text("三个和尚没水喝。", encoding="utf-8")
    scene_plan = tmp_path / "scenes.json"
    scene_plan.write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "narration": "三个和尚来到山上的寺庙。",
                    "image_prompt": "Three monks outside a mountain temple",
                    "duration_sec": 6.0,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    storyboards = tmp_path / "storyboards"
    storyboards.mkdir()
    Image.new("RGB", (96, 64), "white").save(storyboards / "scene_01.png")
    tts_calls: list[tuple[bool | None, str | None]] = []

    def fail_openai_provider(*_args, **_kwargs):
        raise AssertionError("OpenAI providers must not be initialized")

    def fake_tts_provider(mock: bool | None = None, *, name: str | None = None):
        tts_calls.append((mock, name))
        return _PlanTTS()

    def fake_render(_image: Path, output: Path, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")

    def fake_compose(_videos: list[Path], _audio: list[Path], output: Path):
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(pipeline, "settings", replace(settings, work_dir=tmp_path / "work"))
    monkeypatch.setattr(pipeline, "get_llm_provider", fail_openai_provider)
    monkeypatch.setattr(pipeline, "get_image_provider", fail_openai_provider)
    monkeypatch.setattr(pipeline, "get_tts_provider", fake_tts_provider)
    monkeypatch.setattr(pipeline, "get_lineart_provider", lambda _name: _PlanLineArt())
    monkeypatch.setattr(pipeline, "render_image", fake_render)
    monkeypatch.setattr(pipeline, "compose_project", fake_compose)

    output = tmp_path / "three-monks.mp4"
    project = run_pipeline(
        script,
        output,
        mock=False,
        tts_provider="doubao",
        storyboard_dir=storyboards,
        scene_plan_path=scene_plan,
        resolution=(96, 64),
    )

    assert tts_calls == [(False, "doubao")]
    assert [scene.narration for scene in project.scenes] == ["三个和尚来到山上的寺庙。"]
    assert output.read_bytes() == b"final"


def test_silent_pipeline_skips_tts_but_keeps_annotations(monkeypatch, tmp_path: Path):
    script = tmp_path / "silent.md"
    script.write_text("三个和尚没水喝。", encoding="utf-8")
    scene_plan = tmp_path / "scenes.json"
    scene_plan.write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "narration": "水缸已经见底。",
                    "image_prompt": "Three monks beside an empty water jar",
                    "duration_sec": 4.25,
                    "annotations": [{"text": "没水！", "x": 0.72, "y": 0.12}],
                    "timing_cues": [
                        {
                            "text": "水缸已经见底。",
                            "start_sec": 0.5,
                            "end_sec": 2.2,
                            "draw_to": 1.0,
                        }
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    storyboards = tmp_path / "storyboards"
    storyboards.mkdir()
    Image.new("RGB", (96, 64), "white").save(storyboards / "scene_01.png")
    render_calls: list[dict[str, object]] = []
    composed_audio: list[Path] | None = None
    burned_subtitles: list[tuple[Path, Path, Path, dict[str, object]]] = []

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("silent preplanned run must not initialize this provider")

    def fake_render(_image: Path, output: Path, **kwargs):
        render_calls.append(kwargs)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")

    def fake_compose(_videos: list[Path], audio: list[Path], output: Path):
        nonlocal composed_audio
        composed_audio = audio
        output.write_bytes(b"final")
        return output

    def fake_burn(video: Path, subtitles: Path, output: Path, **kwargs):
        assert video.read_bytes() == b"final"
        assert subtitles.exists()
        burned_subtitles.append((video, subtitles, output, kwargs))
        output.write_bytes(b"burned")
        return output

    monkeypatch.setattr(pipeline, "settings", replace(settings, work_dir=tmp_path / "work"))
    monkeypatch.setattr(pipeline, "get_llm_provider", fail_provider)
    monkeypatch.setattr(pipeline, "get_image_provider", fail_provider)
    monkeypatch.setattr(pipeline, "get_tts_provider", fail_provider)
    monkeypatch.setattr(pipeline, "get_lineart_provider", lambda _name: _PlanLineArt())
    monkeypatch.setattr(pipeline, "render_image", fake_render)
    monkeypatch.setattr(pipeline, "compose_project", fake_compose)
    monkeypatch.setattr(pipeline, "burn_subtitles_into_video", fake_burn)

    output = tmp_path / "silent.mp4"
    project = run_pipeline(
        script,
        output,
        mock=False,
        tts_provider="none",
        storyboard_dir=storyboards,
        scene_plan_path=scene_plan,
        resolution=(96, 64),
        tail_color_seconds=0.75,
        burn_subtitles=True,
        subtitle_font="Hiragino Sans GB",
        subtitle_font_size=15.0,
        subtitle_margin_v=24,
        subtitle_outline=1.4,
    )

    assert project.tts_provider == "none"
    assert project.voice == ""
    assert project.scenes[0].duration_sec == 4.25
    assert project.scenes[0].audio_path is None
    assert composed_audio == []
    assert render_calls[0].get("draw_text") is None
    assert [item.text for item in render_calls[0]["annotations"]] == ["没水！"]
    assert [item.text for item in render_calls[0]["timing_cues"]] == ["水缸已经见底。"]
    assert render_calls[0]["duration"] == 5.0
    assert project.subtitle_path == output.with_suffix(".srt")
    assert output.read_bytes() == b"burned"
    assert burned_subtitles == [
        (
            output,
            output.with_suffix(".srt"),
            output,
            {
                "font_name": "Hiragino Sans GB",
                "font_size": 15.0,
                "margin_v": 24,
                "outline": 1.4,
            },
        )
    ]
    assert "00:00:00,500 --> 00:00:02,200" in output.with_suffix(".srt").read_text(
        encoding="utf-8"
    )


def test_render_fingerprint_tracks_annotation_content(tmp_path: Path):
    lineart = tmp_path / "lineart.png"
    Image.new("RGB", (32, 24), "white").save(lineart)
    project = Project(title="story", width=32, height=24)
    plain = Scene(id=1, narration="旁白", image_prompt="prompt", lineart_path=lineart)
    labeled = Scene(
        id=1,
        narration="旁白",
        image_prompt="prompt",
        lineart_path=lineart,
        annotations=[Annotation(text="水缸", x=0.4, y=0.2)],
    )

    timed = plain.model_copy(
        update={
            "timing_cues": [
                TimingCue(text="旁白", start_sec=0.0, end_sec=1.0)
            ]
        }
    )
    common = {"project": project, "hand_style": "none", "hand_scale": 1.0}

    assert _render_fingerprint(scene=plain, **common) != _render_fingerprint(
        scene=labeled,
        **common,
    )
    assert _render_fingerprint(scene=plain, **common) != _render_fingerprint(
        scene=timed,
        **common,
    )


def test_srt_uses_estimated_phrases_then_exact_cues_with_clip_offsets(tmp_path: Path):
    scenes = [
        Scene(
            id=1,
            narration="第一句。第二句。",
            image_prompt="first",
            duration_sec=1.0,
            audio_duration_sec=1.0,
        ),
        Scene(
            id=2,
            narration="精确时间。",
            image_prompt="second",
            duration_sec=2.0,
            timing_cues=[
                TimingCue(text="精确时间。", start_sec=0.25, end_sec=0.75)
            ],
        ),
    ]
    output = tmp_path / "story.srt"

    _write_srt_sidecar(scenes, output, tail_color_seconds=1.0, fps=30)

    text = output.read_text(encoding="utf-8")
    assert "第一句。" in text and "第二句。" in text
    assert "00:00:02,250 --> 00:00:02,750" in text


def test_srt_exact_cue_stops_at_audio_end_not_visual_tail(tmp_path: Path):
    scenes = [
        Scene(
            id=1,
            narration="配音结束后继续停留。",
            image_prompt="first",
            duration_sec=4.0,
            audio_duration_sec=2.0,
            timing_cues=[
                TimingCue(text="只到声音结束", start_sec=1.5, end_sec=4.5)
            ],
            timing_source="provider",
        ),
        Scene(
            id=2,
            narration="下一幕。",
            image_prompt="second",
            duration_sec=1.0,
            audio_duration_sec=1.0,
        ),
    ]
    output = tmp_path / "bounded.srt"

    _write_srt_sidecar(scenes, output, tail_color_seconds=1.0, fps=30)

    text = output.read_text(encoding="utf-8")
    assert "00:00:01,500 --> 00:00:02,000" in text
    # Scene two still starts after the full five-second visual clip.
    assert "00:00:05,000 --> 00:00:06,000" in text


def test_srt_timestamp_rounds_across_hour_boundary():
    assert _srt_timestamp(3599.9996) == "01:00:00,000"


def test_real_pipeline_rejects_color_provider_as_direct_lineart(tmp_path: Path):
    script = tmp_path / "story.md"
    script.write_text("从前有一只小猫。", encoding="utf-8")

    with pytest.raises(ValueError, match="mock-preview compatibility mode"):
        run_pipeline(
            script,
            tmp_path / "story.mp4",
            mock=False,
            scene_asset_mode="direct-lineart",
        )
