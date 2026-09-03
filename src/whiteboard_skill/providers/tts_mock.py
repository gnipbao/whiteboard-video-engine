"""Offline TTS provider that writes simple WAV audio."""

from __future__ import annotations

import math
import wave
from pathlib import Path


class MockTTSProvider:
    """Generate quiet synthetic audio without external services."""

    default_voice = "mock"
    file_extension = ".wav"

    def synthesize(self, text: str, out_path: Path, voice: str) -> float:
        """Write a low-volume sine WAV and return its duration."""

        del voice
        duration = max(1.2, min(8.0, len(text) / 18.0))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sample_rate = 22050
        frames = int(duration * sample_rate)
        with wave.open(str(out_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            for idx in range(frames):
                envelope = min(1.0, idx / (sample_rate * 0.08), (frames - idx) / (sample_rate * 0.12))
                value = int(math.sin(2 * math.pi * 220 * idx / sample_rate) * 1200 * max(0.0, envelope))
                wav.writeframesraw(value.to_bytes(2, "little", signed=True))
        return duration
