"""Script-to-scene planning."""

from __future__ import annotations

from pydantic import ValidationError

from .models import STYLE_SUFFIX, Annotation, Scene, TimingCue
from .providers import LLMProvider


def split_script(script: str, provider: LLMProvider, scene_count: int = 4) -> list[Scene]:
    """Split raw script text into validated scenes."""

    raw_scenes = provider.split_scenes(script, scene_count)
    scenes: list[Scene] = []
    for idx, item in enumerate(raw_scenes[:scene_count], start=1):
        narration = str(item.get("narration") or "").strip()
        if not narration:
            continue
        prompt = str(item.get("image_prompt") or narration).strip()
        if STYLE_SUFFIX not in prompt:
            prompt = f"{prompt}{STYLE_SUFFIX}"
        duration = item.get("duration_sec")
        annotations: list[Annotation] = []
        raw_annotations = item.get("annotations", [])
        if isinstance(raw_annotations, list):
            for raw_annotation in raw_annotations[:2]:
                try:
                    annotations.append(Annotation.model_validate(raw_annotation))
                except (TypeError, ValidationError):
                    continue
        timing_cues: list[TimingCue] = []
        raw_timing_cues = item.get("timing_cues", [])
        if isinstance(raw_timing_cues, list):
            for raw_cue in raw_timing_cues[:64]:
                try:
                    timing_cues.append(TimingCue.model_validate(raw_cue))
                except (TypeError, ValidationError):
                    continue
        scenes.append(
            Scene(
                id=int(item.get("id") or idx),
                narration=narration,
                image_prompt=prompt,
                duration_sec=float(duration) if isinstance(duration, (int, float, str)) and str(duration).strip() else None,
                annotations=annotations,
                timing_cues=timing_cues,
            )
        )
    if not scenes:
        scenes.append(Scene(id=1, narration=script.strip() or "A simple whiteboard explanation.", image_prompt=f"simple concept diagram{STYLE_SUFFIX}", duration_sec=4.0))
    return scenes
