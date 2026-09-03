import shutil
import subprocess
from pathlib import Path

import pytest

from whiteboard_skill import compose
from whiteboard_skill.compose import (
    _mux_scene_audio,
    burn_subtitles,
    compose_project,
    ffmpeg_path,
    ffprobe_duration,
)


def test_compose_rejects_partial_scene_audio_without_shifting_tracks(tmp_path: Path):
    with pytest.raises(ValueError, match="one audio track per scene"):
        compose_project(
            [tmp_path / "scene-1.mp4", tmp_path / "scene-2.mp4"],
            [tmp_path / "scene-1.mp3"],
            tmp_path / "story.mp4",
        )


def test_burn_subtitles_builds_deterministic_libass_command(
    monkeypatch, tmp_path: Path
):
    video_path = tmp_path / "input.mp4"
    subtitle_path = tmp_path / "captions.srt"
    out_path = tmp_path / "burned.mp4"
    video_path.write_bytes(b"source-video")
    subtitle_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n字幕\n", encoding="utf-8"
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append((command, kwargs))
        Path(command[-1]).write_bytes(b"burned-video")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(compose, "ffmpeg_path", lambda: "/test/ffmpeg")
    monkeypatch.setattr(compose.subprocess, "run", fake_run)

    result = burn_subtitles(video_path, subtitle_path, out_path)

    assert result == out_path
    assert out_path.read_bytes() == b"burned-video"
    assert subtitle_path.exists()
    command, kwargs = calls[0]
    assert command[:4] == ["/test/ffmpeg", "-y", "-i", str(video_path)]
    assert command[command.index("-vf") + 1] == (
        f"subtitles=filename={subtitle_path.resolve()}:"
        r"force_style=FontName=sans-serif\,FontSize=16\,MarginV=22\,Outline=1.6"
    )
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-preset") + 1] == "medium"
    assert command[command.index("-crf") + 1] == "18"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-fps_mode") + 1] == "passthrough"
    assert command[command.index("-c:a") + 1] == "copy"
    assert command[command.index("-movflags") + 1] == "+faststart"
    assert Path(command[-1]).parent == out_path.parent
    assert Path(command[-1]) != out_path
    assert kwargs == {"check": True, "capture_output": True, "text": True}


def test_burn_subtitles_supports_in_place_output_and_keeps_sidecar(
    monkeypatch,
    tmp_path: Path,
):
    video_path = tmp_path / "story.mp4"
    subtitle_path = tmp_path / "story.srt"
    video_path.write_bytes(b"original-video")
    subtitle_path.write_text("subtitle-sidecar", encoding="utf-8")
    temporary_outputs: list[Path] = []

    def fake_run(command: list[str], **_kwargs):
        assert video_path.read_bytes() == b"original-video"
        temporary = Path(command[-1])
        assert temporary != video_path
        temporary.write_bytes(b"video-with-subtitles")
        temporary_outputs.append(temporary)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(compose, "ffmpeg_path", lambda: "/test/ffmpeg")
    monkeypatch.setattr(compose.subprocess, "run", fake_run)

    assert burn_subtitles(video_path, subtitle_path, video_path) == video_path
    assert video_path.read_bytes() == b"video-with-subtitles"
    assert subtitle_path.read_text(encoding="utf-8") == "subtitle-sidecar"
    assert temporary_outputs and not temporary_outputs[0].exists()


def test_burn_subtitles_escapes_filter_values(monkeypatch, tmp_path: Path):
    video_path = tmp_path / "input.mp4"
    subtitle_path = tmp_path / "captions: O'Brien,[final].srt"
    out_path = tmp_path / "output.mp4"
    video_path.write_bytes(b"video")
    subtitle_path.write_text("subtitle", encoding="utf-8")
    captured: list[str] = []

    def fake_run(command: list[str], **_kwargs):
        captured.extend(command)
        Path(command[-1]).write_bytes(b"burned")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(compose, "ffmpeg_path", lambda: "/test/ffmpeg")
    monkeypatch.setattr(compose.subprocess, "run", fake_run)

    burn_subtitles(
        video_path,
        subtitle_path,
        out_path,
        font_name="Kid's Sans: Book",
        font_size=18.5,
        margin_v=30,
        outline=2.25,
    )

    subtitle_filter = captured[captured.index("-vf") + 1]
    assert r"captions\\: O\\\'Brien\,\[final\].srt" in subtitle_filter
    assert r"FontName=Kid\\\'s Sans\\: Book" in subtitle_filter
    assert r"FontSize=18.5\,MarginV=30\,Outline=2.25" in subtitle_filter


@pytest.mark.parametrize("missing", ["video", "subtitle"])
def test_burn_subtitles_rejects_missing_inputs(tmp_path: Path, missing: str):
    video_path = tmp_path / "input.mp4"
    subtitle_path = tmp_path / "captions.srt"
    if missing != "video":
        video_path.write_bytes(b"video")
    if missing != "subtitle":
        subtitle_path.write_text("subtitle", encoding="utf-8")

    message = "Input video" if missing == "video" else "Subtitle file"
    with pytest.raises(FileNotFoundError, match=message):
        burn_subtitles(video_path, subtitle_path, tmp_path / "output.mp4")


def test_burn_subtitles_reports_ffmpeg_failure_without_touching_inputs(
    monkeypatch,
    tmp_path: Path,
):
    video_path = tmp_path / "input.mp4"
    subtitle_path = tmp_path / "captions.srt"
    out_path = tmp_path / "output.mp4"
    video_path.write_bytes(b"original-video")
    subtitle_path.write_text("subtitle", encoding="utf-8")

    def fail_run(command: list[str], **_kwargs):
        raise subprocess.CalledProcessError(
            1,
            command,
            stderr="Error initializing filter 'subtitles'",
        )

    monkeypatch.setattr(compose, "ffmpeg_path", lambda: "/test/ffmpeg")
    monkeypatch.setattr(compose.subprocess, "run", fail_run)

    with pytest.raises(RuntimeError, match="FFmpeg failed to burn subtitles"):
        burn_subtitles(video_path, subtitle_path, out_path)

    assert video_path.read_bytes() == b"original-video"
    assert subtitle_path.read_text(encoding="utf-8") == "subtitle"
    assert not out_path.exists()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg is required",
)
def test_mux_scene_audio_pads_to_finite_video_duration(tmp_path: Path):
    video_path = tmp_path / "video.mp4"
    audio_path = tmp_path / "audio.wav"
    output_path = tmp_path / "output.mp4"
    subprocess.run(
        [
            ffmpeg_path(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=160x90:r=8:d=1.25",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg_path(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=24000:duration=0.25",
            str(audio_path),
        ],
        check=True,
    )

    _mux_scene_audio(video_path, audio_path, output_path)

    assert output_path.stat().st_size > 1_000
    assert ffprobe_duration(output_path) == pytest.approx(
        ffprobe_duration(video_path), abs=0.08
    )
