"""Volcengine Doubao Seed-TTS 2.0 provider."""

from __future__ import annotations

import base64
import json
import math
import urllib.error
import urllib.request
import uuid
import wave
from collections.abc import Callable, Iterable
from pathlib import Path

from ..compose import ffprobe_duration
from ..config import Settings, settings
from .base import SpeechSentenceTiming, SpeechSynthesisResult, SpeechWordTiming

DOUBAO_TTS_SSE_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
SUCCESS_CODES = {0, 20_000_000}
VALID_SAMPLE_RATES = {8000, 16000, 22050, 24000, 32000, 44100, 48000}


class DoubaoTTSProvider:
    """Synthesize narration with the official V3 SSE Seed-TTS endpoint."""

    def __init__(
        self,
        config: Settings | None = None,
        opener: Callable[..., object] | None = None,
    ) -> None:
        self.config = config or settings
        self._opener = opener or urllib.request.urlopen
        self.default_voice = self.config.doubao_tts_voice
        self._format = self.config.doubao_tts_format.strip().lower()
        if self._format not in {"mp3", "pcm", "ogg_opus"}:
            raise ValueError("DOUBAO_TTS_FORMAT must be mp3, pcm, or ogg_opus")
        self.file_extension = {"mp3": ".mp3", "pcm": ".wav", "ogg_opus": ".ogg"}[self._format]
        self._validate_configuration()

    def synthesize(self, text: str, out_path: Path, voice: str) -> float:
        """Write narration audio and return its measured duration."""

        return self.synthesize_detailed(text, out_path, voice).duration_sec

    def synthesis_identity(self) -> dict[str, object]:
        """Return non-secret settings that affect audio or alignment output."""

        return {
            "schema": 2,
            "endpoint": DOUBAO_TTS_SSE_ENDPOINT,
            "resource_id": self.config.doubao_tts_resource_id,
            "format": self._format,
            "sample_rate": self.config.doubao_tts_sample_rate,
            "speech_rate": self.config.doubao_tts_speech_rate,
            "pitch_rate": self.config.doubao_tts_pitch_rate,
            "loudness_rate": self.config.doubao_tts_loudness_rate,
            "bit_rate": self.config.doubao_tts_bit_rate,
            "enable_subtitle": True,
        }

    def synthesize_detailed(
        self,
        text: str,
        out_path: Path,
        voice: str,
    ) -> SpeechSynthesisResult:
        """Write narration audio and return official Seed-TTS timing metadata."""

        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Doubao TTS text cannot be empty")
        speaker = (voice or self.default_voice).strip()
        if not speaker:
            raise ValueError("Doubao TTS voice cannot be empty")

        request_id = str(uuid.uuid4())
        request = urllib.request.Request(
            DOUBAO_TTS_SSE_ENDPOINT,
            data=json.dumps(self._request_body(clean_text, speaker), ensure_ascii=False).encode("utf-8"),
            headers=self._request_headers(request_id),
            method="POST",
        )
        try:
            response = self._opener(request, timeout=self.config.doubao_tts_timeout_sec)
            with response:
                status = int(getattr(response, "status", 200))
                if status < 200 or status >= 300:
                    raise RuntimeError(f"Doubao TTS HTTP error: {status}")
                chunks, _provider_duration, sentences = _parse_sse_lines(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Doubao TTS HTTP error: {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError("Doubao TTS network error") from exc

        if not chunks:
            raise RuntimeError("Doubao TTS returned no audio data")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = b"".join(chunks)
        if self._format == "pcm":
            _write_pcm_wav(out_path, payload, self.config.doubao_tts_sample_rate)
        else:
            out_path.write_bytes(payload)
        measured = ffprobe_duration(out_path)
        if measured > 0:
            duration = measured
        elif self._format == "pcm":
            duration = len(payload) / max(1, self.config.doubao_tts_sample_rate * 2)
        else:
            raise RuntimeError("Doubao TTS audio duration could not be measured")
        safe_sentences = _bounded_sentence_timings(sentences, duration)
        return SpeechSynthesisResult(duration_sec=duration, sentences=tuple(safe_sentences))

    def _validate_configuration(self) -> None:
        if self.config.doubao_tts_endpoint != DOUBAO_TTS_SSE_ENDPOINT:
            raise RuntimeError("DOUBAO_TTS_ENDPOINT must use the official Volcengine V3 SSE endpoint")
        for name, value in (
            ("DOUBAO_TTS_API_KEY", self.config.doubao_tts_api_key),
            ("DOUBAO_TTS_APP_ID", self.config.doubao_tts_app_id),
            ("DOUBAO_TTS_ACCESS_KEY", self.config.doubao_tts_access_key),
        ):
            if value:
                _validate_credential(name, value)
        if not self.config.doubao_tts_api_key and not (
            self.config.doubao_tts_app_id and self.config.doubao_tts_access_key
        ):
            raise RuntimeError(
                "Doubao TTS requires DOUBAO_API_KEY (new console) or "
                "DOUBAO_APP_ID plus DOUBAO_ACCESS_KEY (legacy console)"
            )
        if self.config.doubao_tts_resource_id != "seed-tts-2.0":
            raise RuntimeError("DOUBAO_TTS_RESOURCE_ID must be seed-tts-2.0 for Doubao Voice 2")
        if self.config.doubao_tts_sample_rate not in VALID_SAMPLE_RATES:
            choices = ", ".join(str(rate) for rate in sorted(VALID_SAMPLE_RATES))
            raise ValueError(f"DOUBAO_TTS_SAMPLE_RATE must be one of: {choices}")
        if self._format == "ogg_opus" and self.config.doubao_tts_sample_rate != 48000:
            raise ValueError("Doubao ogg_opus output requires a 48000 Hz sample rate")
        if (
            self._format in {"mp3", "ogg_opus"}
            and self.config.doubao_tts_bit_rate not in {64000, 160000}
        ):
            raise ValueError(
                "Doubao compressed output bit rate must be 64000 or 160000"
            )
        _bounded("DOUBAO_TTS_SPEECH_RATE", self.config.doubao_tts_speech_rate, -50, 100)
        _bounded("DOUBAO_TTS_PITCH_RATE", self.config.doubao_tts_pitch_rate, -12, 12)
        _bounded("DOUBAO_TTS_LOUDNESS_RATE", self.config.doubao_tts_loudness_rate, -50, 100)
        _bounded("DOUBAO_TTS_BIT_RATE", self.config.doubao_tts_bit_rate, 16000, 320000)
        _bounded("DOUBAO_TTS_TIMEOUT_SEC", self.config.doubao_tts_timeout_sec, 1, 300)

    def _request_headers(self, request_id: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Api-Resource-Id": self.config.doubao_tts_resource_id,
            "X-Api-Request-Id": request_id,
        }
        if self.config.doubao_tts_api_key:
            headers["X-Api-Key"] = self.config.doubao_tts_api_key
        else:
            assert self.config.doubao_tts_app_id and self.config.doubao_tts_access_key
            headers["X-Api-App-Id"] = self.config.doubao_tts_app_id
            headers["X-Api-Access-Key"] = self.config.doubao_tts_access_key
        return headers

    def _request_body(self, text: str, speaker: str) -> dict[str, object]:
        audio_params: dict[str, object] = {
            "format": self._format,
            "sample_rate": self.config.doubao_tts_sample_rate,
            "speech_rate": self.config.doubao_tts_speech_rate,
            "loudness_rate": self.config.doubao_tts_loudness_rate,
            "enable_subtitle": True,
        }
        if self._format in {"mp3", "ogg_opus"}:
            audio_params["bit_rate"] = self.config.doubao_tts_bit_rate
        additions = {
            "post_process": {"pitch": self.config.doubao_tts_pitch_rate},
            "disable_markdown_filter": True,
            "enable_latex_tn": False,
        }
        return {
            "user": {"uid": "whiteboard-video"},
            "req_params": {
                "text": text,
                "speaker": speaker,
                "audio_params": audio_params,
                "additions": json.dumps(additions, ensure_ascii=False),
            },
        }


def _parse_sse_lines(
    lines: Iterable[bytes | str],
) -> tuple[list[bytes], float | None, list[SpeechSentenceTiming]]:
    audio_chunks: list[bytes] = []
    duration_sec: float | None = None
    sentences: list[SpeechSentenceTiming] = []
    current_event: str | None = None
    data_lines: list[str] = []
    completed = False

    def consume_event() -> None:
        nonlocal completed, current_event, data_lines, duration_sec, sentences
        event = current_event
        if not data_lines:
            current_event = None
            return
        payload_text = "\n".join(data_lines).strip()
        data_lines = []
        if not payload_text.startswith(("{", "[")):
            current_event = None
            return
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Doubao TTS returned invalid SSE JSON") from exc
        if not isinstance(payload, dict):
            current_event = None
            return
        code = payload.get("code")
        numeric_code: int | None = None
        if code is not None:
            try:
                numeric_code = int(code)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Doubao TTS returned an invalid provider code") from exc
            if numeric_code not in SUCCESS_CODES:
                raise RuntimeError(f"Doubao TTS provider error: code {numeric_code}")
        if event in {"151", "153", "SessionCancel", "SessionFailed"}:
            raise RuntimeError("Doubao TTS provider ended the session without audio completion")
        if event in {"152", "finish", "finished", "SessionFinish", "SessionFinished"}:
            if numeric_code != 20_000_000:
                raise RuntimeError("Doubao TTS finish event was not successful")
            completed = True
        duration_sec = _payload_duration(payload, duration_sec)
        sentence = _payload_sentence_timing(payload)
        if sentence is not None:
            sentences = _merge_sentence_timing(sentences, sentence)
        data = payload.get("data")
        if isinstance(data, str) and event in {None, "352", "audio", "TTSResponse"}:
            encoded = data.strip()
            if encoded and not encoded.startswith(("{", "[")):
                try:
                    chunk = base64.b64decode(encoded, validate=True)
                except Exception as exc:
                    raise RuntimeError("Doubao TTS returned invalid base64 audio") from exc
                if chunk:
                    audio_chunks.append(chunk)
        current_event = None

    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
        stripped = line.rstrip("\r\n")
        if not stripped:
            consume_event()
            continue
        if stripped.startswith(":"):
            continue
        if stripped.startswith("event:"):
            consume_event()
            current_event = stripped[6:].strip()
            if current_event in {"151", "153", "SessionCancel", "SessionFailed"}:
                raise RuntimeError(
                    "Doubao TTS provider ended the session without audio completion"
                )
            continue
        if stripped.startswith("data:"):
            data_lines.append(stripped[5:].lstrip())
            continue
        consume_event()
        data_lines.append(stripped.strip())
        consume_event()
    consume_event()
    if not completed:
        raise RuntimeError("Doubao TTS stream ended before the finish event")
    sentences.sort(key=lambda item: (item.start_sec, item.end_sec, item.text))
    return audio_chunks, duration_sec, sentences


def _payload_sentence_timing(payload: dict[str, object]) -> SpeechSentenceTiming | None:
    """Extract one V3 ``sentence`` payload without relying on an SSE event id."""

    candidate: object = payload.get("sentence")
    if candidate is None:
        data = payload.get("data")
        if isinstance(data, dict):
            candidate = data.get("sentence", data)
        elif isinstance(data, str) and data.lstrip().startswith("{"):
            try:
                decoded = json.loads(data)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                candidate = decoded.get("sentence", decoded)
    if isinstance(candidate, str) and candidate.lstrip().startswith("{"):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError:
            return None
    if not isinstance(candidate, dict):
        return None
    raw_words = candidate.get("words")
    if not isinstance(raw_words, list):
        return None
    words: list[SpeechWordTiming] = []
    for raw_word in raw_words:
        if not isinstance(raw_word, dict):
            continue
        text = raw_word.get("word", raw_word.get("text", ""))
        if not isinstance(text, str) or not text:
            continue
        start = _finite_float(raw_word.get("startTime"))
        end = _finite_float(raw_word.get("endTime"))
        if start is None or end is None or start < 0 or end <= start:
            continue
        confidence = _finite_float(raw_word.get("confidence"))
        words.append(
            SpeechWordTiming(
                text=text,
                start_sec=start,
                end_sec=end,
                confidence=confidence,
            )
        )
    if not words:
        return None
    words.sort(key=lambda item: (item.start_sec, item.end_sec))
    sentence_text = candidate.get("text")
    if not isinstance(sentence_text, str) or not sentence_text.strip():
        sentence_text = "".join(word.text for word in words)
    return SpeechSentenceTiming(
        text=sentence_text.strip(),
        start_sec=words[0].start_sec,
        end_sec=max(word.end_sec for word in words),
        words=tuple(words),
    )


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _merge_sentence_timing(
    sentences: list[SpeechSentenceTiming],
    incoming: SpeechSentenceTiming,
) -> list[SpeechSentenceTiming]:
    """Replace a partial repeated subtitle event with its more complete update."""

    incoming_text = "".join(incoming.text.split())
    incoming_duration = incoming.end_sec - incoming.start_sec
    for index, existing in enumerate(sentences):
        existing_text = "".join(existing.text.split())
        existing_duration = existing.end_sec - existing.start_sec
        overlap = min(existing.end_sec, incoming.end_sec) - max(
            existing.start_sec,
            incoming.start_sec,
        )
        shortest = max(1e-6, min(existing_duration, incoming_duration))
        same_span = (
            abs(existing.start_sec - incoming.start_sec) <= 0.08
            or overlap / shortest >= 0.55
        )
        related_text = (
            existing_text == incoming_text
            or existing_text in incoming_text
            or incoming_text in existing_text
        )
        if not (same_span and related_text):
            continue
        existing_score = (
            len(existing_text),
            len(existing.words),
            existing.end_sec - existing.start_sec,
        )
        incoming_score = (
            len(incoming_text),
            len(incoming.words),
            incoming.end_sec - incoming.start_sec,
        )
        if incoming_score > existing_score:
            sentences[index] = incoming
        return sentences
    sentences.append(incoming)
    return sentences


def _bounded_sentence_timings(
    sentences: list[SpeechSentenceTiming],
    audio_duration: float,
) -> list[SpeechSentenceTiming]:
    """Reject corrupt timestamp outliers and clamp encoder-padding drift."""

    if not math.isfinite(audio_duration) or audio_duration <= 0:
        return []
    tolerance = max(0.5, audio_duration * 0.05)
    safe: list[SpeechSentenceTiming] = []
    for sentence in sorted(
        sentences,
        key=lambda item: (item.start_sec, item.end_sec, item.text),
    ):
        words_list: list[SpeechWordTiming] = []
        previous_end = 0.0
        for word in sorted(
            sentence.words,
            key=lambda item: (item.start_sec, item.end_sec),
        ):
            if (
                not math.isfinite(word.start_sec)
                or not math.isfinite(word.end_sec)
                or word.start_sec < 0
                or word.end_sec <= word.start_sec
                or word.start_sec >= audio_duration
                or word.end_sec > audio_duration + tolerance
            ):
                continue
            start = word.start_sec
            if start < previous_end:
                if previous_end - start > 0.03 or word.end_sec <= previous_end:
                    continue
                start = previous_end
            end = min(audio_duration, word.end_sec)
            if end <= start:
                continue
            confidence = word.confidence
            if confidence is not None and (
                not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0
            ):
                confidence = None
            words_list.append(
                SpeechWordTiming(
                    text=word.text,
                    start_sec=start,
                    end_sec=end,
                    confidence=confidence,
                )
            )
            previous_end = end
        words = tuple(words_list)
        if not words:
            continue
        preserved_all_words = len(words) == len(sentence.words)
        sentence_text = (
            sentence.text
            if preserved_all_words and sentence.text.strip()
            else "".join(word.text for word in words)
        )
        safe.append(
            SpeechSentenceTiming(
                text=sentence_text,
                start_sec=words[0].start_sec,
                end_sec=max(word.end_sec for word in words),
                words=words,
            )
        )
    return safe


def _payload_duration(payload: dict[str, object], current: float | None) -> float | None:
    addition = payload.get("addition")
    candidates = [payload.get("duration")]
    if isinstance(addition, str) and addition.lstrip().startswith("{"):
        try:
            addition = json.loads(addition)
        except json.JSONDecodeError:
            addition = None
    if isinstance(addition, dict):
        candidates.append(addition.get("duration"))
    for candidate in candidates:
        try:
            value = float(candidate)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        seconds = value / 1000.0
        current = max(current or 0.0, seconds)
    return current


def _write_pcm_wav(path: Path, payload: bytes, sample_rate: int) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(payload)


def _validate_credential(name: str, value: str) -> None:
    if not value or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise RuntimeError(f"{name} must contain only visible ASCII characters")
    if value.lower().startswith(("http://", "https://", "wss://")):
        raise RuntimeError(f"{name} is not a credential")
    if value.startswith("S_"):
        raise RuntimeError(f"{name} looks like a speaker ID, not a credential")


def _bounded(name: str, value: float, minimum: float, maximum: float) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
