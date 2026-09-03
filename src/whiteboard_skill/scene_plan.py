"""Load user-authored scene plans without invoking a remote planner."""

from __future__ import annotations

import json
import math
from pathlib import Path

from .models import Scene

_REQUIRED_SCENE_FIELDS = {"id", "narration", "image_prompt", "duration_sec"}
_OPTIONAL_SCENE_FIELDS = {"annotations", "timing_cues"}


def load_scene_plan(path: Path) -> list[Scene]:
    """Read and strictly validate an ordered JSON scene list.

    The root may be either the list itself or an object with a ``scenes`` list.
    IDs must be exactly 1..N so they map unambiguously to ``scene_01.png`` and
    subsequent storyboard filenames.
    """

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid scene-plan JSON in {path}: {exc.msg}") from exc

    raw_scenes = payload.get("scenes") if isinstance(payload, dict) else payload
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ValueError("scene-plan must contain a non-empty scene list")

    scenes: list[Scene] = []
    for position, item in enumerate(raw_scenes, start=1):
        if not isinstance(item, dict):
            raise TypeError(f"scene-plan item {position} must be an object")
        missing = _REQUIRED_SCENE_FIELDS.difference(item)
        unexpected = set(item).difference(
            _REQUIRED_SCENE_FIELDS | _OPTIONAL_SCENE_FIELDS
        )
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"scene-plan item {position} is missing: {names}")
        if unexpected:
            names = ", ".join(sorted(str(name) for name in unexpected))
            raise ValueError(f"scene-plan item {position} has unsupported fields: {names}")

        scene_id = item["id"]
        if isinstance(scene_id, bool) or not isinstance(scene_id, int):
            raise TypeError(f"scene-plan item {position} id must be an integer")
        if scene_id != position:
            raise ValueError(
                f"scene-plan ids must be consecutive in list order; "
                f"item {position} must have id {position}"
            )

        narration = item["narration"]
        image_prompt = item["image_prompt"]
        if not isinstance(narration, str) or not narration.strip():
            raise ValueError(f"scene-plan item {position} narration must be non-empty text")
        if not isinstance(image_prompt, str) or not image_prompt.strip():
            raise ValueError(f"scene-plan item {position} image_prompt must be non-empty text")

        duration = item["duration_sec"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(float(duration))
            or float(duration) <= 0
        ):
            raise ValueError(f"scene-plan item {position} duration_sec must be a positive number")

        scenes.append(
            Scene(
                id=scene_id,
                narration=narration.strip(),
                image_prompt=image_prompt.strip(),
                duration_sec=float(duration),
                annotations=item.get("annotations", []),
                timing_cues=item.get("timing_cues", []),
                timing_source="authored" if item.get("timing_cues") else None,
            )
        )
    return scenes


def scene_plan_payload(scenes: list[Scene]) -> list[dict[str, object]]:
    """Return the canonical content used by resume fingerprints."""

    payload: list[dict[str, object]] = []
    for scene in scenes:
        item: dict[str, object] = {
            "id": scene.id,
            "narration": scene.narration,
            "image_prompt": scene.image_prompt,
            "duration_sec": scene.duration_sec,
        }
        if scene.annotations:
            item["annotations"] = [
                annotation.model_dump(mode="json")
                for annotation in scene.annotations
            ]
        if scene.timing_cues:
            item["timing_cues"] = [
                cue.model_dump(mode="json", exclude_none=True)
                for cue in scene.timing_cues
            ]
        payload.append(item)
    return payload
