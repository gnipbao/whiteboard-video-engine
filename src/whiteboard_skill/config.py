"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _truthy(value: str | None) -> bool:
    return bool(value and value.lower() in {"1", "true", "yes", "on"})


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc


@dataclass(frozen=True)
class Settings:
    """Environment-backed settings.

    Example:
        >>> Settings(mock=True).mock
        True
    """

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4.1-mini"
    image_model: str = "gpt-image-2"
    image_quality: str = "low"
    visual_style: str = "warm-crayon-storybook"
    tts_provider: str = "edge"
    doubao_tts_api_key: str | None = None
    doubao_tts_app_id: str | None = None
    doubao_tts_access_key: str | None = None
    doubao_tts_endpoint: str = (
        "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
    )
    doubao_tts_resource_id: str = "seed-tts-2.0"
    doubao_tts_voice: str = "zh_female_vv_uranus_bigtts"
    doubao_tts_format: str = "mp3"
    doubao_tts_sample_rate: int = 24000
    doubao_tts_speech_rate: int = 0
    doubao_tts_pitch_rate: int = 0
    doubao_tts_loudness_rate: int = 0
    doubao_tts_bit_rate: int = 64000
    doubao_tts_timeout_sec: float = 90.0
    work_dir: Path = Path("./work")
    mock: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            llm_model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
            image_model=os.getenv("IMAGE_MODEL", "gpt-image-2"),
            image_quality=os.getenv("IMAGE_QUALITY", "low"),
            visual_style=os.getenv("WHITEBOARD_STYLE", "warm-crayon-storybook"),
            tts_provider=os.getenv("TTS_PROVIDER", "edge"),
            doubao_tts_api_key=(
                os.getenv("DOUBAO_TTS_API_KEY")
                or os.getenv("DOUBAO_API_KEY")
                or os.getenv("MODEL_SPEECH_API_KEY")
            ),
            doubao_tts_app_id=os.getenv("DOUBAO_TTS_APP_ID")
            or os.getenv("DOUBAO_APP_ID"),
            doubao_tts_access_key=os.getenv("DOUBAO_TTS_ACCESS_KEY")
            or os.getenv("DOUBAO_ACCESS_KEY"),
            doubao_tts_endpoint=os.getenv(
                "DOUBAO_TTS_ENDPOINT",
                "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse",
            ),
            doubao_tts_resource_id=(
                os.getenv("DOUBAO_TTS_RESOURCE_ID")
                or os.getenv("MODEL_SPEECH_TTS_RESOURCE_ID")
                or "seed-tts-2.0"
            ),
            doubao_tts_voice=os.getenv(
                "DOUBAO_TTS_VOICE", "zh_female_vv_uranus_bigtts"
            ),
            doubao_tts_format=os.getenv("DOUBAO_TTS_FORMAT", "mp3"),
            doubao_tts_sample_rate=_int_env("DOUBAO_TTS_SAMPLE_RATE", 24000),
            doubao_tts_speech_rate=_int_env("DOUBAO_TTS_SPEECH_RATE", 0),
            doubao_tts_pitch_rate=_int_env("DOUBAO_TTS_PITCH_RATE", 0),
            doubao_tts_loudness_rate=_int_env("DOUBAO_TTS_LOUDNESS_RATE", 0),
            doubao_tts_bit_rate=_int_env("DOUBAO_TTS_BIT_RATE", 64000),
            doubao_tts_timeout_sec=_float_env("DOUBAO_TTS_TIMEOUT_SEC", 90.0),
            work_dir=Path(os.getenv("WORK_DIR", "./work")),
            mock=_truthy(os.getenv("MOCK")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )


settings = Settings.from_env()
