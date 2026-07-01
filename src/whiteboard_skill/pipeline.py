"""End-to-end whiteboard video pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .compose import compose_project
from .config import settings
from .image_gen import generate_scene_images
from .logging_setup import logger
from .models import Project, Scene
from .providers import get_providers
from .scene_split import split_script
from .tts import synthesize_scene_audio
from .whiteboard import render_scene


def run_pipeline(
    script_path: Path,
    out_path: Path,
    scene_count: int = 4,
    fps: int = 60,
    resolution: tuple[int, int] = (1920, 1080),
    voice: str = "zh-CN-XiaoxiaoNeural",
    tail_color_seconds: float = 2.0,
    resume: bool = False,
    mock: bool | None = None,
    hand_style: str = "asian",
    hand_scale: float = 1.0,
) -> Project:
    """Run script -> scenes -> images -> narration -> rendered MP4."""

    project_id = _slug(script_path.stem)
    work_dir = settings.work_dir / project_id
    work_dir.mkdir(parents=True, exist_ok=True)
    project_path = work_dir / "project.json"

    if resume and project_path.exists():
        project = _load_project(project_path)
        logger.info("resuming project {}", project_path)
    else:
        providers = get_providers(mock)
        script = script_path.read_text(encoding="utf-8")
        scenes = split_script(script, providers.llm, scene_count)
        project = Project(title=script_path.stem, voice=voice, fps=fps, width=resolution[0], height=resolution[1], tail_color_seconds=tail_color_seconds, scenes=scenes)
        _save_project(project, project_path)

    providers = get_providers(mock)
    project.fps = fps
    project.width, project.height = resolution
    project.voice = voice
    project.tail_color_seconds = tail_color_seconds

    logger.info("generating scene images")
    project.scenes = generate_scene_images(project.scenes, providers.image, work_dir / "images", project.resolution, resume=resume)
    _save_project(project, project_path)

    logger.info("synthesizing narration")
    project.scenes = synthesize_scene_audio(project.scenes, providers.tts, work_dir / "audio", project.voice, resume=resume)
    _save_project(project, project_path)

    logger.info("rendering scene videos")
    render_dir = work_dir / "renders"
    render_dir.mkdir(parents=True, exist_ok=True)
    for scene in project.scenes:
        if not scene.image_path:
            raise RuntimeError(f"Scene {scene.id} has no image_path")
        scene_video = render_dir / f"scene_{scene.id:02d}.mp4"
        if not (resume and scene_video.exists()):
            strokes, source_image = _scene_strokes_and_source(scene, project.resolution)
            render_scene(
                scene.image_path,
                strokes,
                duration=max(2.0, float(scene.duration_sec or 4.0) + project.tail_color_seconds),
                out_path=scene_video,
                fps=project.fps,
                resolution=project.resolution,
                tail_color_sec=project.tail_color_seconds,
                source_image=source_image,
                show_cursor=hand_style != "none",
                hand_style=hand_style,
                hand_scale=hand_scale,
            )
        scene.video_path = scene_video
        _save_project(project, project_path)

    logger.info("composing final video")
    compose_project([s.video_path for s in project.scenes if s.video_path], [s.audio_path for s in project.scenes if s.audio_path], out_path)
    _save_project(project, project_path)
    return project


def _scene_strokes_and_source(scene: Scene, resolution: tuple[int, int]):
    from .preprocess import svg_to_strokes, to_strokes

    if not scene.image_path:
        return [], None
    if scene.image_path.suffix.lower() == ".svg":
        strokes, preview = svg_to_strokes(scene.image_path, resolution)
        return strokes, preview
    return to_strokes(scene.image_path, resolution), None


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff._-]+", "-", value).strip("-._")
    return slug or "whiteboard-project"


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
