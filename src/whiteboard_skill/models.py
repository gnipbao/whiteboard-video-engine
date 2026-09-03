"""Project data models."""

from __future__ import annotations

import math
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .styles import DEFAULT_STYLE_ID, LEGACY_STYLE_SUFFIX

# Kept as a public compatibility alias for third-party planners. New code uses
# ``build_storyboard_prompt`` and a resolved ``VisualStyle`` instead.
STYLE_SUFFIX = LEGACY_STYLE_SUFFIX
CURRENT_PROJECT_SCHEMA_VERSION = 4


class TextRole(str, Enum):
    """Semantic role of text rendered over a scene."""

    ANNOTATION = "annotation"
    CAPTION = "caption"


class Annotation(BaseModel):
    """One short explanatory label placed in normalized canvas coordinates."""

    text: str
    x: float = Field(default=0.08, ge=0.0, le=1.0)
    y: float = Field(default=0.08, ge=0.0, le=1.0)

    @field_validator("text")
    @classmethod
    def validate_short_text(cls, value: str) -> str:
        """Keep annotations label-like instead of subtitle-like."""

        cleaned = " ".join(value.split())
        visible = "".join(cleaned.split())
        if not visible:
            raise ValueError("annotation text cannot be empty")
        if len(visible) > 12:
            raise ValueError(
                "annotation text must contain at most 12 visible characters"
            )
        return cleaned


class TimingCue(BaseModel):
    """One local narration phrase interval that can steer drawing rhythm.

    ``draw_to`` is the cumulative visual progress reached at the phrase end. If
    omitted on every cue, targets are distributed evenly in narration order.
    """

    text: str
    start_sec: float = Field(ge=0.0, allow_inf_nan=False)
    end_sec: float = Field(gt=0.0, allow_inf_nan=False)
    draw_to: float | None = Field(default=None, gt=0.0, le=1.0)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        """Reject empty subtitle/timing entries."""

        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("timing cue text cannot be empty")
        return cleaned

    @model_validator(mode="after")
    def validate_interval(self) -> "TimingCue":
        """Require a positive local interval."""

        if self.end_sec <= self.start_sec:
            raise ValueError("timing cue end_sec must be greater than start_sec")
        return self


class Scene(BaseModel):
    """One storyboard scene."""

    id: int
    narration: str
    image_prompt: str
    annotations: list[Annotation] = Field(default_factory=list, max_length=2)
    timing_cues: list[TimingCue] = Field(default_factory=list, max_length=64)
    timing_source: Literal["authored", "provider", "unknown"] | None = None
    duration_sec: float | None = None
    planned_duration_sec: float | None = Field(
        default=None,
        ge=0.0,
        allow_inf_nan=False,
    )
    audio_duration_sec: float | None = None
    source_image_path: Path | None = None
    source_generation_fingerprint: str | None = None
    lineart_path: Path | None = None
    lineart_source_fingerprint: str | None = None
    image_path: Path | None = None
    audio_path: Path | None = None
    audio_fingerprint: str | None = None
    video_path: Path | None = None
    render_fingerprint: str | None = None

    @field_validator("timing_cues")
    @classmethod
    def validate_timing_cues(cls, cues: list[TimingCue]) -> list[TimingCue]:
        """Require ordered, non-overlapping cues and a coherent progress map."""

        for previous, current in zip(cues, cues[1:], strict=False):
            if current.start_sec < previous.end_sec:
                raise ValueError("timing cues must be ordered and non-overlapping")
        explicit_targets = [cue.draw_to is not None for cue in cues]
        if any(explicit_targets) and not all(explicit_targets):
            raise ValueError(
                "timing cue draw_to must be provided for every cue or omitted for all"
            )
        if cues and all(explicit_targets):
            targets = [float(cue.draw_to or 0.0) for cue in cues]
            if any(
                current <= previous
                for previous, current in zip(targets, targets[1:], strict=False)
            ):
                raise ValueError("timing cue draw_to values must increase")
            if not math.isclose(targets[-1], 1.0, abs_tol=1e-6):
                raise ValueError("the final timing cue draw_to must equal 1.0")
        return cues

    @model_validator(mode="after")
    def initialize_timing_provenance(self) -> Scene:
        """Preserve authored cues and the pre-audio scene-duration floor."""

        if self.timing_cues and self.timing_source is None:
            self.timing_source = "authored"
        if self.planned_duration_sec is None and self.audio_duration_sec is None:
            self.planned_duration_sec = self.duration_sec
        return self


class Project(BaseModel):
    """Whiteboard project state persisted under work/<project_id>/."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=CURRENT_PROJECT_SCHEMA_VERSION, ge=2)
    title: str
    planning_fingerprint: str | None = None
    voice: str = "zh-CN-XiaoxiaoNeural"
    tts_provider: str = "edge"
    image_model: str = "gpt-image-2"
    image_quality: str = "low"
    visual_style_id: str = DEFAULT_STYLE_ID
    visual_style_snapshot: dict[str, object] = Field(default_factory=dict)
    visual_theme: str = ""
    scene_asset_mode: str = "color-to-lineart"
    lineart_provider: str = "auto"
    animation_preset: str = "block-speedpaint"
    max_draw_blocks: int = 6
    draw_blocks: int | None = 4
    block_overlap: float = 0.16
    block_order: str = "reading"
    block_sequence: list[int] | None = None
    block_fill_style: Literal["crayon", "clean", "soft-wash", "dry-brush"] = "crayon"
    color_fill_scope: Literal["block", "scene"] = "block"
    stroke_detail: Literal["balanced", "rich", "max"] = "rich"
    line_thickness: int = Field(default=0, ge=0, le=16)
    line_art_snap: bool = True
    line_art_snap_threshold: int = Field(default=235, ge=1, le=254)
    fps: int = 60
    width: int = 1920
    height: int = 1080
    tail_color_seconds: float = 2.0
    bgm_path: Path | None = None
    subtitle_path: Path | None = None
    burn_subtitles: bool = False
    subtitle_font: str = "sans-serif"
    subtitle_font_size: float = Field(
        default=16.0, ge=6.0, le=72.0, allow_inf_nan=False
    )
    subtitle_margin_v: int = Field(default=22, ge=0, le=1000)
    subtitle_outline: float = Field(default=1.6, ge=0.0, le=10.0, allow_inf_nan=False)
    scenes: list[Scene] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_timing_state(cls, value: object) -> object:
        """Migrate timing provenance and pin legacy projects to the old visual style."""

        if not isinstance(value, dict):
            return value
        payload = dict(value)
        raw_version = payload.get("schema_version", 1)
        if type(raw_version) is not int or raw_version < 1:
            raise ValueError("project schema_version must be a positive integer")
        version = raw_version
        if version > CURRENT_PROJECT_SCHEMA_VERSION:
            raise ValueError(
                "project schema_version "
                f"{version} is newer than supported version "
                f"{CURRENT_PROJECT_SCHEMA_VERSION}"
            )
        if version < 2:
            raw_scenes = payload.get("scenes")
            if isinstance(raw_scenes, list):
                migrated_scenes: list[object] = []
                for raw_scene in raw_scenes:
                    if not isinstance(raw_scene, dict):
                        migrated_scenes.append(raw_scene)
                        continue
                    scene = dict(raw_scene)
                    has_audio_state = bool(scene.get("audio_fingerprint")) or (
                        scene.get("audio_duration_sec") is not None
                    )
                    if "planned_duration_sec" not in scene:
                        scene["planned_duration_sec"] = (
                            0.0 if has_audio_state else scene.get("duration_sec")
                        )
                    if scene.get("timing_cues") and "timing_source" not in scene:
                        scene["timing_source"] = (
                            "unknown" if has_audio_state else "authored"
                        )
                    migrated_scenes.append(scene)
                payload["scenes"] = migrated_scenes
        if version < 3:
            payload.setdefault("visual_style_id", DEFAULT_STYLE_ID)
            payload.setdefault("visual_style_snapshot", {})
            payload.setdefault("visual_theme", "")
            payload.setdefault("block_fill_style", "crayon")
            payload.setdefault("stroke_detail", "rich")
            payload.setdefault("line_thickness", 0)
            payload.setdefault("line_art_snap", True)
            payload.setdefault("line_art_snap_threshold", 235)
        if version < 4:
            # Historical renders used object-local color masks. Preserve that
            # appearance when loading an old project instead of silently opting
            # it into a newer style's whole-scene color treatment.
            payload.setdefault("color_fill_scope", "block")
        payload["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
        return payload

    @property
    def resolution(self) -> tuple[int, int]:
        """Return width and height as a tuple.

        Example:
            >>> Project(title="demo", width=640, height=360).resolution
            (640, 360)
        """

        return (self.width, self.height)
