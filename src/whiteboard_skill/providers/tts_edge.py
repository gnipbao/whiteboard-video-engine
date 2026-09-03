"""Edge TTS provider."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..compose import ffprobe_duration


class EdgeTTSProvider:
    """Synthesize narration through edge-tts."""

    default_voice = "zh-CN-XiaoxiaoNeural"
    file_extension = ".mp3"

    def synthesize(self, text: str, out_path: Path, voice: str) -> float:
        """Write audio and return measured duration."""

        try:
            import edge_tts
        except Exception as exc:  # pragma: no cover - optional dependency branch
            raise RuntimeError("Install optional dependency `edge-tts` or run with MOCK=1") from exc

        async def _run() -> None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            communicate = edge_tts.Communicate(text, voice=voice)
            await communicate.save(str(out_path))

        asyncio.run(_run())
        return ffprobe_duration(out_path)
