"""Provider interfaces and factory helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import settings


class LLMProvider(Protocol):
    """Split raw scripts into storyboard scene dictionaries."""

    def split_scenes(self, script: str, scene_count: int) -> list[dict[str, object]]:
        """Return scene dictionaries containing narration and image_prompt."""


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


def get_llm_provider(mock: bool | None = None) -> LLMProvider:
    """Return only the requested scene-planning provider."""

    if _use_mock(mock):
        from .llm_mock import MockLLMProvider

        return MockLLMProvider()

    from .llm_openai import OpenAILLMProvider

    return OpenAILLMProvider()


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


def get_tts_provider(mock: bool | None = None, *, name: str | None = None) -> TTSProvider:
    """Return only the requested narration provider.

    Keeping this factory independent lets preplanned, pre-illustrated projects use
    Doubao without constructing an unrelated OpenAI client.
    """

    if _use_mock(mock):
        from .tts_mock import MockTTSProvider

        return MockTTSProvider()

    selected = (name or os.getenv("TTS_PROVIDER") or settings.tts_provider).strip().lower()
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
