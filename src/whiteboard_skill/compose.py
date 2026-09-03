"""FFmpeg-based video and audio composition."""

from __future__ import annotations

import math
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path


def ffmpeg_path() -> str:
    """Return ffmpeg path or raise a clear error."""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found on PATH")
    return ffmpeg


def ffprobe_duration(path: Path) -> float:
    """Return media duration in seconds via ffprobe."""

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def burn_subtitles(
    video_path: Path,
    subtitle_path: Path,
    out_path: Path,
    *,
    font_name: str = "sans-serif",
    font_size: float = 16.0,
    margin_v: int = 22,
    outline: float = 1.6,
) -> Path:
    """Burn an SRT/ASS sidecar into a video with FFmpeg and libass.

    Rendering always targets a temporary sibling before an atomic replacement,
    so ``video_path == out_path`` is safe and a failed encode leaves the input
    untouched. The subtitle sidecar is read-only and is never removed.
    """

    video_path = Path(video_path)
    subtitle_path = Path(subtitle_path)
    out_path = Path(out_path)
    if not video_path.is_file():
        raise FileNotFoundError(f"Input video does not exist: {video_path}")
    if not subtitle_path.is_file():
        raise FileNotFoundError(f"Subtitle file does not exist: {subtitle_path}")
    if subtitle_path.resolve() == out_path.resolve():
        raise ValueError("Output path must not overwrite the subtitle sidecar")

    resolved_font_name = font_name.strip()
    if not resolved_font_name:
        raise ValueError("font_name cannot be empty")
    if any(character in resolved_font_name for character in ("\r", "\n", ",", "\0")):
        raise ValueError("font_name cannot contain commas or control characters")
    resolved_font_size = _finite_style_number("font_size", font_size, minimum=0.0)
    resolved_outline = _finite_style_number(
        "outline", outline, minimum=0.0, inclusive=True
    )
    if isinstance(margin_v, bool) or not isinstance(margin_v, int) or margin_v < 0:
        raise ValueError("margin_v must be a non-negative integer")

    force_style = ",".join(
        (
            f"FontName={resolved_font_name}",
            f"FontSize={resolved_font_size}",
            f"MarginV={margin_v}",
            f"Outline={resolved_outline}",
        )
    )
    subtitle_filter = (
        "subtitles="
        f"filename={_escape_filter_value(str(subtitle_path.resolve()))}:"
        f"force_style={_escape_filter_value(force_style)}"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = out_path.suffix or video_path.suffix or ".mp4"
    source_mode = stat.S_IMODE(video_path.stat().st_mode)
    with tempfile.TemporaryDirectory(
        prefix=f".{out_path.stem}.subtitles-",
        dir=out_path.parent,
    ) as temporary_dir:
        temporary_path = Path(temporary_dir) / f"output{suffix}"
        command = [
            ffmpeg_path(),
            "-y",
            "-i",
            str(video_path),
            "-vf",
            subtitle_filter,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "passthrough",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(temporary_path),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            reason = (
                detail.splitlines()[-1] if detail else f"exit code {exc.returncode}"
            )
            raise RuntimeError(
                f"FFmpeg failed to burn subtitles from {subtitle_path} into "
                f"{video_path}: {reason}"
            ) from exc
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise RuntimeError(
                "FFmpeg completed without producing a non-empty subtitled video"
            )
        temporary_path.chmod(source_mode)
        temporary_path.replace(out_path)
    return out_path


def _finite_style_number(
    name: str,
    value: float,
    *,
    minimum: float,
    inclusive: bool = False,
) -> str:
    """Validate and format one numeric ASS style value."""

    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    if number < minimum or (not inclusive and number <= minimum):
        qualifier = "non-negative" if inclusive and minimum == 0 else "positive"
        raise ValueError(f"{name} must be {qualifier}")
    return f"{number:g}"


def _escape_filter_value(value: str) -> str:
    """Escape one value through FFmpeg's option and filtergraph parsers.

    ``subtitles`` values cross two parsers even though ``subprocess`` bypasses a
    shell. Escaping the inner option syntax first and the outer filtergraph
    syntax second preserves colons, quotes, commas and brackets literally.
    """

    option_escaped = "".join(
        f"\\{character}" if character in "\\':" else character for character in value
    )
    return "".join(
        f"\\{character}" if character in "\\'[],;" else character
        for character in option_escaped
    )


def compose_project(
    video_paths: list[Path], audio_paths: list[Path], out_path: Path
) -> Path:
    """Mux each scene with padded narration, then concatenate synchronized A/V."""

    if not video_paths:
        raise ValueError("No scene videos to compose")
    if audio_paths and len(audio_paths) != len(video_paths):
        raise ValueError(
            "Narrated composition requires exactly one audio track per scene"
        )
    missing_videos = [path for path in video_paths if not path.exists()]
    if missing_videos:
        raise FileNotFoundError(f"Scene video does not exist: {missing_videos[0]}")
    missing_audio = [path for path in audio_paths if not path.exists()]
    if missing_audio:
        raise FileNotFoundError(f"Scene narration does not exist: {missing_audio[0]}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="whiteboard-compose-") as tmp:
        tmp_dir = Path(tmp)
        if audio_paths:
            scene_paths: list[Path] = []
            for index, (video_path, audio_path) in enumerate(
                zip(video_paths, audio_paths), start=1
            ):
                scene_path = tmp_dir / f"scene-av-{index:03d}.mp4"
                _mux_scene_audio(video_path, audio_path, scene_path)
                scene_paths.append(scene_path)
            _concat_videos(scene_paths, out_path)
            return out_path

        concat_video = tmp_dir / "video.mp4"
        _concat_videos(video_paths, concat_video)
        shutil.copyfile(concat_video, out_path)
    return out_path


def _mux_scene_audio(video_path: Path, audio_path: Path, out_path: Path) -> None:
    """Pad one narration track with silence to exactly match its scene video."""

    duration = ffprobe_duration(video_path)
    if duration <= 0:
        raise RuntimeError(f"Could not determine scene video duration: {video_path}")
    duration_arg = f"{duration:.6f}"
    subprocess.run(
        [
            ffmpeg_path(),
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-filter_complex",
            f"[1:a]apad=whole_dur={duration_arg}[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-t",
            duration_arg,
            "-movflags",
            "+faststart",
            str(out_path),
        ],
        check=True,
    )


def _concat_videos(video_paths: list[Path], out_path: Path) -> None:
    if len(video_paths) == 1:
        shutil.copyfile(video_paths[0], out_path)
        return
    list_file = out_path.with_suffix(".txt")
    list_file.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in video_paths), encoding="utf-8"
    )
    subprocess.run(
        [
            ffmpeg_path(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(out_path),
        ],
        check=True,
    )


def _concat_audio(audio_paths: list[Path], out_path: Path) -> None:
    if len(audio_paths) == 1:
        subprocess.run(
            [
                ffmpeg_path(),
                "-y",
                "-i",
                str(audio_paths[0]),
                "-c:a",
                "aac",
                str(out_path),
            ],
            check=True,
        )
        return
    cmd = [ffmpeg_path(), "-y"]
    for path in audio_paths:
        cmd.extend(["-i", str(path)])
    inputs = "".join(f"[{idx}:a]" for idx in range(len(audio_paths)))
    filter_complex = f"{inputs}concat=n={len(audio_paths)}:v=0:a=1[a]"
    cmd.extend(
        ["-filter_complex", filter_complex, "-map", "[a]", "-c:a", "aac", str(out_path)]
    )
    subprocess.run(cmd, check=True)
