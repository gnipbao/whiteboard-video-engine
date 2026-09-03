"""Provider interfaces and factory helpers."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import settings
from ..prompts import load_prompt

PLANNING_IDENTITY_SCHEMA = 1


class LLMProvider(Protocol):
    """Split raw scripts into storyboard scene dictionaries."""

    def split_scenes(self, script: str, scene_count: int) -> list[dict[str, object]]:
        """Return scene dictionaries containing narration and image_prompt."""

    def planning_identity(self) -> dict[str, object]:
        """Return stable provider settings that affect scene planning."""


class ImageProvider(Protocol):
    """Generate storyboard images for a scene prompt."""

    def generate(self, prompt: str, out_path: Path, size: tuple[int, int]) -> Path:
        """Write an image file and return its path."""


class TTSProvider(Protocol):
    """Synthesize narration audio."""

    default_voice: str
    file_extension: str

    def synthesize(self, text: str, out_path: Path, voice: str) -> float:
        """Write audio and return its duration in seconds."""


@dataclass(frozen=True)
class ProviderBundle:
    """Concrete providers used by a pipeline run."""

    llm: LLMProvider
    image: ImageProvider
    tts: TTSProvider


def llm_planning_identity(provider: object) -> dict[str, object]:
    """Return the complete cache identity for one scene-planning provider.

    The packaged prompt is part of the planning algorithm even though it is not
    owned by a concrete provider. Hashing it here keeps provider implementations
    small while ensuring a prompt edit invalidates resumed scene plans.
    """

    identity_method = getattr(provider, "planning_identity", None)
    provider_settings = (
        identity_method()
        if callable(identity_method)
        else {"model": getattr(provider, "model", None)}
    )
    if not isinstance(provider_settings, dict):
        raise TypeError("LLM planning_identity() must return an object")
    provider_class = provider.__class__
    template = load_prompt("scene_split.txt")
    return {
        "schema": PLANNING_IDENTITY_SCHEMA,
        "provider": f"{provider_class.__module__}.{provider_class.__qualname__}",
        "provider_settings": provider_settings,
        "scene_split_template_sha256": hashlib.sha256(
            template.encode("utf-8")
        ).hexdigest(),
    }


@dataclass(frozen=True)
class SpeechWordTiming:
    """One provider-reported spoken token interval in local scene seconds."""

    text: str
    start_sec: float
    end_sec: float
    confidence: float | None = None


@dataclass(frozen=True)
class SpeechSentenceTiming:
    """One provider-reported sentence interval and its optional word timings."""

    text: str
    start_sec: float
    end_sec: float
    words: tuple[SpeechWordTiming, ...] = ()


@dataclass(frozen=True)
class SpeechSynthesisResult:
    """Detailed TTS result used when a provider exposes timing metadata."""

    duration_sec: float
    sentences: tuple[SpeechSentenceTiming, ...] = ()


def _truthy(value: str | None) -> bool:
    return bool(value and value.lower() in {"1", "true", "yes", "on"})


def _use_mock(mock: bool | None) -> bool:
    selected = settings.mock if mock is None else mock
    return selected or _truthy(os.getenv("MOCK"))


def get_llm_provider(
    mock: bool | None = None,
    *,
    style_guidance: str | None = None,
) -> LLMProvider:
    """Return only the requested scene-planning provider."""

    if _use_mock(mock):
        from .llm_mock import MockLLMProvider

        return MockLLMProvider(style_guidance=style_guidance)

    from .llm_openai import OpenAILLMProvider

    return OpenAILLMProvider(style_guidance=style_guidance)


def get_image_provider(
    mock: bool | None = None,
    *,
    image_model: str | None = None,
    image_quality: str | None = None,
) -> ImageProvider:
    """Return only the requested storyboard-image provider."""

    if _use_mock(mock):
        from .image_mock import MockImageProvider

        return MockImageProvider()

    from .image_openai import OpenAIImageProvider

    return OpenAIImageProvider(model=image_model, quality=image_quality)


def get_tts_provider(
    mock: bool | None = None, *, name: str | None = None
) -> TTSProvider:
    """Return only the requested narration provider.

    Keeping this factory independent lets preplanned, pre-illustrated projects use
    Doubao without constructing an unrelated OpenAI client.
    """

    if _use_mock(mock):
        from .tts_mock import MockTTSProvider

        return MockTTSProvider()

    selected = (
        (name or os.getenv("TTS_PROVIDER") or settings.tts_provider).strip().lower()
    )
    if selected == "edge":
        from .tts_edge import EdgeTTSProvider

        return EdgeTTSProvider()
    if selected in {"doubao", "seed-tts-2.0", "volcengine"}:
        from .tts_doubao import DoubaoTTSProvider

        return DoubaoTTSProvider()
    raise ValueError(f"Unknown TTS provider: {selected}")


def get_providers(
    mock: bool | None = None,
    *,
    tts_provider: str | None = None,
    image_model: str | None = None,
    image_quality: str | None = None,
) -> ProviderBundle:
    """Return provider implementations.

    If `mock` is true, or `MOCK=1` is set, the returned providers run fully
    offline and produce deterministic assets. Otherwise OpenAI planning/image
    providers and the selected narration provider are initialized lazily.
    """

    use_mock = _use_mock(mock)
    return ProviderBundle(
        llm=get_llm_provider(use_mock),
        image=get_image_provider(
            use_mock,
            image_model=image_model,
            image_quality=image_quality,
        ),
        tts=get_tts_provider(use_mock, name=tts_provider),
    )
