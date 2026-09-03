"""End-to-end whiteboard video pipeline."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .compose import burn_subtitles as burn_subtitles_into_video
from .compose import compose_project
from .config import settings
from .fingerprints import file_sha256, provider_identity, stable_fingerprint
from .image_gen import (
    extract_scene_lineart,
    generate_scene_images,
    use_precomputed_source_images,
)
from .logging_setup import logger
from .models import Project, Scene, TimingCue
from .providers import get_image_provider, get_llm_provider, get_tts_provider
from .providers.lineart import get_lineart_provider
from .scene_plan import load_scene_plan, scene_plan_payload
from .scene_split import split_script
from .tts import synthesize_scene_audio
from .whiteboard import render_image


def run_pipeline(
    script_path: Path,
    out_path: Path,
    scene_count: int = 4,
    fps: int = 30,
    resolution: tuple[int, int] = (1920, 1080),
    voice: str | None = None,
    tail_color_seconds: float = 2.0,
    resume: bool = False,
    mock: bool | None = None,
    hand_style: str = "asian",
    hand_scale: float = 1.0,
    tts_provider: str | None = None,
    image_model: str | None = None,
    image_quality: str | None = None,
    lineart_provider: str = "auto",
    scene_asset_mode: str = "auto",
    storyboard_dir: Path | None = None,
    scene_plan_path: Path | None = None,
    animation_preset: str = "block-speedpaint",
    max_draw_blocks: int = 6,
    draw_blocks: int | None = 4,
    block_overlap: float = 0.16,
    block_order: str = "reading",
    block_sequence: list[int] | None = None,
    captions: bool = False,
    burn_subtitles: bool = False,
    subtitle_font: str = "sans-serif",
    subtitle_font_size: float = 16.0,
    subtitle_margin_v: int = 22,
    subtitle_outline: float = 1.6,
) -> Project:
    """Run script -> color storyboards -> local line art -> optional TTS -> MP4."""

    script_path = Path(script_path)
    out_path = Path(out_path)
    if out_path.suffix.lower() == ".srt":
        raise ValueError(
            "out_path must be a video path, not .srt; the pipeline reserves that "
            "suffix for the subtitle sidecar"
        )
    if scene_asset_mode not in {"auto", "color-to-lineart", "direct-lineart"}:
        raise ValueError("scene_asset_mode must be auto, color-to-lineart, or direct-lineart")
    if animation_preset not in {"classic", "block-speedpaint"}:
        raise ValueError("animation_preset must be classic or block-speedpaint")
    if max_draw_blocks < 1:
        raise ValueError("max_draw_blocks must be at least 1")
    if draw_blocks is not None and draw_blocks < 0:
        raise ValueError("draw_blocks must be 0 (automatic) or a positive integer")
    if not 0 <= block_overlap <= 0.65:
        raise ValueError("block_overlap must be between 0 and 0.65")
    if block_order not in {"reading", "source"}:
        raise ValueError("block_order must be reading or source")
    if not subtitle_font.strip():
        raise ValueError("subtitle_font cannot be empty")
    if any(character in subtitle_font for character in ("\r", "\n", ",", "\0")):
        raise ValueError("subtitle_font cannot contain commas or control characters")
    if not 6.0 <= subtitle_font_size <= 72.0:
        raise ValueError("subtitle_font_size must be between 6 and 72")
    if isinstance(subtitle_margin_v, bool) or not 0 <= subtitle_margin_v <= 1000:
        raise ValueError("subtitle_margin_v must be between 0 and 1000")
    if not 0.0 <= subtitle_outline <= 10.0:
        raise ValueError("subtitle_outline must be between 0 and 10")
    if captions and burn_subtitles:
        raise ValueError("--captions is deprecated; use --burn-subtitles by itself")
    if captions:
        logger.warning(
            "--captions is deprecated and has no effect; use --burn-subtitles to burn the sidecar SRT"
        )
    resolved_draw_blocks = draw_blocks or None
    use_mock = bool(settings.mock if mock is None else mock) or _truthy(os.getenv("MOCK"))
    resolved_asset_mode = scene_asset_mode
    if storyboard_dir is not None:
        resolved_asset_mode = "color-to-lineart"
    elif resolved_asset_mode == "auto":
        resolved_asset_mode = "direct-lineart" if use_mock else "color-to-lineart"
    if resolved_asset_mode == "direct-lineart" and not use_mock:
        raise ValueError(
            "direct-lineart is a mock-preview compatibility mode; real runs must use "
            "color-to-lineart so generated color storyboards are locally registered and traced"
        )
    selected_tts_provider = (tts_provider or settings.tts_provider).strip().lower()
    selected_image_model = image_model or settings.image_model
    selected_image_quality = image_quality or settings.image_quality
    explicit_scenes = load_scene_plan(scene_plan_path) if scene_plan_path is not None else None
    narration_provider = (
        None
        if selected_tts_provider == "none"
        else get_tts_provider(use_mock, name=selected_tts_provider)
    )
    image_provider = None
    if explicit_scenes is not None:
        if storyboard_dir is None:
            image_provider = get_image_provider(
                use_mock,
                image_model=selected_image_model,
                image_quality=selected_image_quality,
            )
        planning_provider = None
    else:
        planning_provider = get_llm_provider(use_mock)
        image_provider = get_image_provider(
            use_mock,
            image_model=selected_image_model,
            image_quality=selected_image_quality,
        )
    resolved_voice = voice or (
        getattr(narration_provider, "default_voice", "zh-CN-XiaoxiaoNeural")
        if narration_provider is not None
        else ""
    )

    project_id = _slug(script_path.stem)
    work_dir = settings.work_dir / project_id
    work_dir.mkdir(parents=True, exist_ok=True)
    project_path = work_dir / "project.json"
    script = script_path.read_text(encoding="utf-8")
    if explicit_scenes is not None:
        planning_fingerprint = stable_fingerprint(
            {
                "mode": "scene-plan",
                "scenes": scene_plan_payload(explicit_scenes),
            }
        )
    else:
        assert planning_provider is not None
        planning_fingerprint = stable_fingerprint(
            {
                "llm_model": getattr(planning_provider, "model", None),
                "llm_provider": provider_identity(planning_provider),
                "scene_count": scene_count,
                "script": script,
            }
        )

    resumed_project = _load_project(project_path) if resume and project_path.exists() else None
    if resumed_project is not None and resumed_project.planning_fingerprint == planning_fingerprint:
        project = resumed_project
        if explicit_scenes is not None:
            _restore_explicit_scene_plan(project.scenes, explicit_scenes)
        logger.info("resuming project {}", project_path)
    else:
        if explicit_scenes is not None:
            scenes = explicit_scenes
        else:
            assert planning_provider is not None
            scenes = split_script(script, planning_provider, scene_count)
        project = Project(
            title=script_path.stem,
            planning_fingerprint=planning_fingerprint,
            voice=resolved_voice,
            tts_provider=selected_tts_provider,
            image_model=selected_image_model,
            image_quality=selected_image_quality,
            scene_asset_mode=resolved_asset_mode,
            lineart_provider=lineart_provider,
            animation_preset=animation_preset,
            max_draw_blocks=max_draw_blocks,
            draw_blocks=resolved_draw_blocks,
            block_overlap=block_overlap,
            block_order=block_order,
            block_sequence=block_sequence,
            fps=fps,
            width=resolution[0],
            height=resolution[1],
            tail_color_seconds=tail_color_seconds,
            burn_subtitles=burn_subtitles,
            subtitle_font=subtitle_font,
            subtitle_font_size=subtitle_font_size,
            subtitle_margin_v=subtitle_margin_v,
            subtitle_outline=subtitle_outline,
            scenes=scenes,
        )
        _save_project(project, project_path)

    project.fps = fps
    project.width, project.height = resolution
    project.voice = resolved_voice
    project.tts_provider = selected_tts_provider
    project.image_model = selected_image_model
    project.image_quality = selected_image_quality
    project.scene_asset_mode = resolved_asset_mode
    project.lineart_provider = lineart_provider
    project.animation_preset = animation_preset
    project.max_draw_blocks = max_draw_blocks
    project.draw_blocks = resolved_draw_blocks
    project.block_overlap = block_overlap
    project.block_order = block_order
    project.block_sequence = block_sequence
    project.tail_color_seconds = tail_color_seconds
    project.burn_subtitles = burn_subtitles
    project.subtitle_font = subtitle_font
    project.subtitle_font_size = subtitle_font_size
    project.subtitle_margin_v = subtitle_margin_v
    project.subtitle_outline = subtitle_outline

    if resolved_asset_mode == "color-to-lineart":
        if storyboard_dir is not None:
            logger.info("using precomputed color storyboards from {}", storyboard_dir)
            project.scenes = use_precomputed_source_images(
                project.scenes,
                storyboard_dir,
                output_dir=work_dir / "images" / "color",
                size=project.resolution,
                resume=resume,
            )
        else:
            logger.info("generating color storyboards with {}", selected_image_model)
            if image_provider is None:
                raise RuntimeError("No image provider is available for storyboard generation")
            project.scenes = generate_scene_images(
                project.scenes,
                image_provider,
                work_dir / "images" / "color",
                project.resolution,
                resume=resume,
                asset_role="source",
            )
        logger.info("extracting registered local line art with {}", lineart_provider)
        project.scenes = extract_scene_lineart(
            project.scenes,
            get_lineart_provider(lineart_provider),
            work_dir / "images" / "lineart",
            project.resolution,
            resume=resume,
        )
    else:
        logger.info("generating direct line-art scene images")
        if image_provider is None:
            raise RuntimeError("No image provider is available for line-art generation")
        project.scenes = generate_scene_images(
            project.scenes,
            image_provider,
            work_dir / "images" / "lineart",
            project.resolution,
            resume=resume,
            asset_role="lineart",
        )
    _save_project(project, project_path)

    if narration_provider is None:
        logger.info("TTS disabled; preserving planned scene durations")
        for scene in project.scenes:
            if scene.timing_source in {"provider", "unknown"}:
                scene.timing_cues = []
                scene.timing_source = None
            scene.audio_path = None
            scene.audio_duration_sec = None
            scene.audio_fingerprint = None
            if scene.planned_duration_sec is not None:
                scene.duration_sec = max(2.0, scene.planned_duration_sec)
    else:
        logger.info("synthesizing narration")
        project.scenes = synthesize_scene_audio(
            project.scenes,
            narration_provider,
            work_dir / "audio",
            project.voice,
            resume=resume,
        )
    for scene in project.scenes:
        if scene.timing_cues:
            scene.duration_sec = max(
                float(scene.duration_sec or 0.0),
                scene.timing_cues[-1].end_sec,
            )
    _save_project(project, project_path)

    logger.info("rendering scene videos")
    render_dir = work_dir / "renders"
    render_dir.mkdir(parents=True, exist_ok=True)
    for scene in project.scenes:
        if not scene.image_path:
            raise RuntimeError(f"Scene {scene.id} has no image_path")
        scene_video = render_dir / f"scene_{scene.id:02d}.mp4"
        render_fingerprint = _render_fingerprint(
            scene=scene,
            project=project,
            hand_style=hand_style,
            hand_scale=hand_scale,
        )
        if not (
            resume
            and scene_video.exists()
            and scene.render_fingerprint == render_fingerprint
        ):
            render_image(
                scene.lineart_path or scene.image_path,
                scene_video,
                duration=max(2.0, float(scene.duration_sec or 4.0) + project.tail_color_seconds),
                fps=project.fps,
                resolution=project.resolution,
                tail_color_sec=project.tail_color_seconds,
                source_image_path=scene.source_image_path,
                source_fit="exact",
                hand_style=hand_style,
                hand_scale=hand_scale,
                line_reveal_mode="detail-wipe",
                color_fill_mode="left-to-right-gradient",
                animation_preset=animation_preset,
                annotations=scene.annotations,
                timing_cues=scene.timing_cues,
                max_draw_blocks=max_draw_blocks,
                draw_blocks=resolved_draw_blocks,
                block_overlap=block_overlap,
                block_order=block_order,
                block_sequence=block_sequence,
            )
        scene.render_fingerprint = render_fingerprint
        scene.video_path = scene_video
        _save_project(project, project_path)

    logger.info("composing final video")
    scene_videos: list[Path] = []
    scene_audio: list[Path] = []
    for scene in project.scenes:
        if scene.video_path is None:
            raise RuntimeError(f"Scene {scene.id} has no rendered video")
        scene_videos.append(scene.video_path)
        if narration_provider is not None:
            if scene.audio_path is None:
                raise RuntimeError(f"Scene {scene.id} has no narration audio")
            scene_audio.append(scene.audio_path)
    compose_project(
        scene_videos,
        scene_audio,
        out_path,
    )
    project.subtitle_path = _write_srt_sidecar(
        project.scenes,
        out_path.with_suffix(".srt"),
        tail_color_seconds=project.tail_color_seconds,
        fps=project.fps,
    )
    if project.burn_subtitles:
        logger.info("burning narration subtitles into final video")
        burn_subtitles_into_video(
            out_path,
            project.subtitle_path,
            out_path,
            font_name=project.subtitle_font,
            font_size=project.subtitle_font_size,
            margin_v=project.subtitle_margin_v,
            outline=project.subtitle_outline,
        )
    _save_project(project, project_path)
    return project


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff._-]+", "-", value).strip("-._")
    return slug or "whiteboard-project"


def _restore_explicit_scene_plan(
    resumed_scenes: list[Scene],
    planned_scenes: list[Scene],
) -> None:
    """Restore authored fields while retaining resumable generated artifacts."""

    if len(resumed_scenes) != len(planned_scenes):
        raise RuntimeError("Resumed project no longer matches the explicit scene plan")
    for resumed, planned in zip(resumed_scenes, planned_scenes, strict=True):
        if resumed.id != planned.id:
            raise RuntimeError("Resumed project scene ids no longer match the scene plan")
        resumed.narration = planned.narration
        resumed.image_prompt = planned.image_prompt
        resumed.annotations = list(planned.annotations)
        resumed.planned_duration_sec = planned.planned_duration_sec
        resumed.duration_sec = planned.planned_duration_sec
        resumed.timing_cues = list(planned.timing_cues)
        resumed.timing_source = "authored" if planned.timing_cues else None


def _render_fingerprint(
    *,
    scene: Scene,
    project: Project,
    hand_style: str,
    hand_scale: float,
) -> str:
    lineart_path = scene.lineart_path or scene.image_path
    if lineart_path is None:
        raise RuntimeError(f"Scene {scene.id} has no line-art path")
    return stable_fingerprint(
        {
            "schema": 3,
            "annotations": [
                annotation.model_dump(mode="json")
                for annotation in scene.annotations
            ],
            "timing_cues": [
                cue.model_dump(mode="json", exclude_none=True)
                for cue in scene.timing_cues
            ],
            "animation_preset": project.animation_preset,
            "block_order": project.block_order,
            "block_overlap": project.block_overlap,
            "block_sequence": project.block_sequence,
            "draw_blocks": project.draw_blocks,
            "duration": max(2.0, float(scene.duration_sec or 4.0) + project.tail_color_seconds),
            "fps": project.fps,
            "hand_scale": hand_scale,
            "hand_style": str(hand_style),
            "lineart_sha256": file_sha256(lineart_path),
            "max_draw_blocks": project.max_draw_blocks,
            "resolution": project.resolution,
            "source_sha256": file_sha256(scene.source_image_path) if scene.source_image_path else None,
            "tail_color_seconds": project.tail_color_seconds,
        }
    )


def _scene_clip_duration(scene: Scene, tail_color_seconds: float, fps: int) -> float:
    """Return the frame-aligned duration produced by the renderer."""

    nominal = max(2.0, float(scene.duration_sec or 4.0) + tail_color_seconds)
    return max(1, int(round(nominal * fps))) / max(1, fps)


def _subtitle_phrases(text: str, max_visible_chars: int = 18) -> list[str]:
    """Split narration into deterministic SRT-sized phrases."""

    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    clauses = [
        match.group(0).strip()
        for match in re.finditer(r"[^。！？!?；;\n]+[。！？!?；;]?", cleaned)
        if match.group(0).strip()
    ]
    phrases: list[str] = []
    for clause in clauses or [cleaned]:
        remaining = clause
        while len("".join(remaining.split())) > max_visible_chars:
            split_at = min(len(remaining), max_visible_chars)
            punctuation = max(
                remaining.rfind("，", 0, split_at + 1),
                remaining.rfind(",", 0, split_at + 1),
                remaining.rfind(" ", 0, split_at + 1),
            )
            if punctuation >= max(1, split_at // 2):
                split_at = punctuation + 1
            phrase = remaining[:split_at].strip()
            if phrase:
                phrases.append(phrase)
            remaining = remaining[split_at:].strip()
        if remaining:
            phrases.append(remaining)
    return phrases


def _estimated_subtitle_cues(scene: Scene, audible_duration: float) -> list[TimingCue]:
    """Estimate phrase timings only for subtitles; drawing remains unwarped."""

    phrases = _subtitle_phrases(scene.narration)
    if not phrases or audible_duration <= 0:
        return []
    weights = [max(1, len(re.sub(r"\s+", "", phrase))) for phrase in phrases]
    total_weight = sum(weights)
    cursor = 0.0
    cues: list[TimingCue] = []
    for index, (phrase, weight) in enumerate(zip(phrases, weights, strict=True)):
        end = (
            audible_duration
            if index == len(phrases) - 1
            else cursor + audible_duration * weight / total_weight
        )
        cues.append(TimingCue(text=phrase, start_sec=cursor, end_sec=end))
        cursor = end
    return cues


def _srt_timestamp(seconds: float) -> str:
    """Format seconds as a rollover-safe SubRip timestamp."""

    total_ms = max(0, int(round(seconds * 1000.0)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _write_srt_sidecar(
    scenes: list[Scene],
    out_path: Path,
    *,
    tail_color_seconds: float,
    fps: int,
) -> Path:
    """Write exact provider cues, or estimated phrase cues, beside the video."""

    entries: list[tuple[float, float, str]] = []
    offset = 0.0
    for scene in scenes:
        clip_duration = _scene_clip_duration(scene, tail_color_seconds, fps)
        requested_audible_duration = (
            scene.audio_duration_sec
            if scene.audio_duration_sec is not None
            else scene.duration_sec
        )
        audible_duration = min(
            clip_duration,
            max(0.001, float(requested_audible_duration or clip_duration)),
        )
        cues = scene.timing_cues or _estimated_subtitle_cues(scene, audible_duration)
        for cue in cues:
            local_start = max(0.0, min(audible_duration, cue.start_sec))
            local_end = max(local_start, min(audible_duration, cue.end_sec))
            if local_end - local_start <= 1e-6:
                continue
            entries.append((offset + local_start, offset + local_end, cue.text))
        offset += clip_duration

    lines: list[str] = []
    for index, (start, end, text) in enumerate(entries, start=1):
        lines.extend(
            [
                str(index),
                f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}",
                text,
                "",
            ]
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _truthy(value: str | None) -> bool:
    return bool(value and value.lower() in {"1", "true", "yes", "on"})


def _save_project(project: Project, path: Path) -> None:
    path.write_text(_project_to_json(project), encoding="utf-8")


def _load_project(path: Path) -> Project:
    text = path.read_text(encoding="utf-8")
    if hasattr(Project, "model_validate_json"):
        return Project.model_validate_json(text)  # type: ignore[attr-defined]
    return Project.parse_raw(text)


def _project_to_json(project: Project) -> str:
    if hasattr(project, "model_dump"):
        payload: dict[str, Any] = project.model_dump(mode="json")  # type: ignore[attr-defined]
    else:
        payload = json.loads(project.json())
    return json.dumps(payload, ensure_ascii=False, indent=2)
