"""Narration synthesis helpers."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .compose import ffprobe_duration
from .fingerprints import provider_identity, stable_fingerprint
from .models import Scene, TimingCue
from .providers import (
    SpeechSentenceTiming,
    SpeechSynthesisResult,
    SpeechWordTiming,
    TTSProvider,
)

_PHRASE_PUNCTUATION = frozenset("，,。！？!?；;：:")


def synthesize_scene_audio(
    scenes: list[Scene],
    provider: TTSProvider,
    audio_dir: Path,
    voice: str | None,
    resume: bool = False,
) -> list[Scene]:
    """Generate or reuse narration audio and provider timing for every scene."""

    audio_dir.mkdir(parents=True, exist_ok=True)
    extension = getattr(provider, "file_extension", ".wav")
    if not extension.startswith(".") or "/" in extension or "\\" in extension:
        raise ValueError("TTS provider returned an invalid file extension")
    selected_voice = voice or getattr(provider, "default_voice", "zh-CN-XiaoxiaoNeural")
    synthesize_detailed = getattr(provider, "synthesize_detailed", None)
    detailed_timing = callable(synthesize_detailed)
    provider_settings = _synthesis_identity(provider)

    for scene in scenes:
        if scene.timing_source == "unknown":
            scene.timing_cues = []
            scene.timing_source = None
        audio_fingerprint = stable_fingerprint(
            {
                "extension": extension,
                "narration": scene.narration,
                "provider": provider_identity(provider),
                "provider_settings": provider_settings,
                "timing_schema": 2,
                "voice": selected_voice,
            }
        )
        out_path = audio_dir / f"scene_{scene.id:02d}{extension}"
        alignment_path = audio_dir / f"scene_{scene.id:02d}.alignment.json"
        existing = out_path
        legacy = audio_dir / f"scene_{scene.id:02d}.wav"
        if resume and not existing.exists() and legacy.exists():
            existing = legacy
        cached_alignment = (
            _load_alignment(alignment_path, audio_fingerprint)
            if detailed_timing
            else None
        )
        can_resume = (
            resume
            and existing.exists()
            and scene.audio_fingerprint == audio_fingerprint
            and (not detailed_timing or cached_alignment is not None)
        )
        if can_resume and cached_alignment is not None:
            measured_duration = ffprobe_duration(existing)
            duration_tolerance = max(0.15, cached_alignment.duration_sec * 0.03)
            if (
                not math.isfinite(measured_duration)
                or measured_duration <= 0
                or abs(measured_duration - cached_alignment.duration_sec)
                > duration_tolerance
            ):
                can_resume = False

        if can_resume:
            duration = ffprobe_duration(existing)
            if duration <= 0 and cached_alignment is not None:
                duration = cached_alignment.duration_sec
            if cached_alignment is not None:
                _apply_provider_timing(scene, _speech_timing_cues(cached_alignment))
            scene.audio_path = existing
        else:
            if scene.timing_source in {"provider", "unknown"}:
                scene.timing_cues = []
                scene.timing_source = None
            synthesis = (
                synthesize_detailed(scene.narration, out_path, selected_voice)
                if detailed_timing
                else provider.synthesize(scene.narration, out_path, selected_voice)
            )
            if isinstance(synthesis, SpeechSynthesisResult):
                duration = synthesis.duration_sec
                _apply_provider_timing(scene, _speech_timing_cues(synthesis))
                _write_alignment(alignment_path, audio_fingerprint, synthesis)
            else:
                duration = synthesis
            scene.audio_path = out_path

        if not math.isfinite(duration) or duration <= 0:
            raise RuntimeError(f"Scene {scene.id} narration duration could not be measured")
        scene.audio_duration_sec = duration
        scene.audio_fingerprint = audio_fingerprint
        cue_end = scene.timing_cues[-1].end_sec if scene.timing_cues else 0.0
        # A narrated scene follows the produced audio. The authored duration is
        # retained separately for silent renders, instead of adding dead air.
        scene.duration_sec = max(duration, cue_end)
    return scenes


def _speech_timing_cues(result: SpeechSynthesisResult) -> list[TimingCue]:
    """Normalize provider words into readable phrase cues for drawing and SRT."""

    if not math.isfinite(result.duration_sec) or result.duration_sec <= 0:
        return []
    cues: list[TimingCue] = []
    previous_end = 0.0
    for sentence in sorted(
        result.sentences,
        key=lambda item: (item.start_sec, item.end_sec, item.text),
    ):
        if (
            not math.isfinite(sentence.start_sec)
            or not math.isfinite(sentence.end_sec)
            or sentence.start_sec < 0
            or sentence.end_sec <= sentence.start_sec
            or sentence.start_sec >= result.duration_sec
        ):
            continue
        phrase_cues = _word_phrase_cues(sentence)
        if not phrase_cues:
            text = " ".join(sentence.text.split())
            if text and sentence.end_sec > sentence.start_sec:
                phrase_cues = [
                    TimingCue(
                        text=text,
                        start_sec=sentence.start_sec,
                        end_sec=min(result.duration_sec, sentence.end_sec),
                    )
                ]
        for cue in phrase_cues:
            start = min(result.duration_sec, cue.start_sec)
            end = min(result.duration_sec, cue.end_sec)
            if start < previous_end:
                if previous_end - start > 0.03 or end <= previous_end:
                    continue
                start = previous_end
            if end <= start:
                continue
            cues.append(TimingCue(text=cue.text, start_sec=start, end_sec=end))
            previous_end = end
    if not cues:
        return []
    cues = _coalesce_timing_cues(cues, limit=64)

    # Allocate visual progress by actual spoken time. This yields nearly
    # constant drawing speed during speech while punctuation gaps still pause.
    spoken_duration = sum(cue.end_sec - cue.start_sec for cue in cues)
    cumulative = 0.0
    paced: list[TimingCue] = []
    for index, cue in enumerate(cues):
        cumulative += cue.end_sec - cue.start_sec
        draw_to = 1.0 if index == len(cues) - 1 else cumulative / spoken_duration
        paced.append(
            TimingCue(
                text=cue.text,
                start_sec=cue.start_sec,
                end_sec=cue.end_sec,
                draw_to=draw_to,
            )
        )
    return paced


def _coalesce_timing_cues(cues: list[TimingCue], limit: int) -> list[TimingCue]:
    """Keep provider timing within the persisted Scene model's cue limit."""

    reduced = list(cues)
    while len(reduced) > limit:
        # Preserve the largest pauses. Merging across the smallest gap loses the
        # least meaningful stop while retaining the full spoken time range.
        index = min(
            range(len(reduced) - 1),
            key=lambda item: reduced[item + 1].start_sec - reduced[item].end_sec,
        )
        first = reduced[index]
        second = reduced[index + 1]
        separator = (
            " "
            if first.text[-1:].isascii()
            and first.text[-1:].isalnum()
            and second.text[:1].isascii()
            and second.text[:1].isalnum()
            else ""
        )
        reduced[index : index + 2] = [
            TimingCue(
                text=f"{first.text}{separator}{second.text}",
                start_sec=first.start_sec,
                end_sec=second.end_sec,
            )
        ]
    return reduced


def _word_phrase_cues(sentence: SpeechSentenceTiming) -> list[TimingCue]:
    """Group word timestamps into short, semantically punctuated subtitle beats."""

    words = sorted(sentence.words, key=lambda item: (item.start_sec, item.end_sec))
    groups: list[list[SpeechWordTiming]] = []
    current: list[SpeechWordTiming] = []
    visible_chars = 0
    for word in words:
        if (
            not word.text.strip()
            or not math.isfinite(word.start_sec)
            or not math.isfinite(word.end_sec)
            or word.start_sec < 0
            or word.end_sec <= word.start_sec
        ):
            continue
        if current and word.start_sec < current[-1].end_sec - 0.03:
            continue
        current.append(word)
        visible_chars += len(re.sub(r"\s+", "", word.text))
        elapsed = current[-1].end_sec - current[0].start_sec
        punctuation_break = bool(set(word.text) & _PHRASE_PUNCTUATION)
        if (visible_chars >= 6 and punctuation_break) or visible_chars >= 14 or elapsed >= 2.5:
            groups.append(current)
            current = []
            visible_chars = 0
    if current:
        groups.append(current)
    if len(groups) > 1:
        last = groups[-1]
        last_chars = len(re.sub(r"\s+", "", _join_tokens(last)))
        last_duration = last[-1].end_sec - last[0].start_sec
        if last_chars < 3 or last_duration < 0.6:
            groups[-2].extend(groups.pop())

    cues: list[TimingCue] = []
    for group in groups:
        text = _join_tokens(group)
        if text:
            cues.append(
                TimingCue(
                    text=text,
                    start_sec=group[0].start_sec,
                    end_sec=group[-1].end_sec,
                )
            )
    if len(cues) == 1 and sentence.text.strip():
        cues[0].text = " ".join(sentence.text.split())
    return cues


def _join_tokens(words: list[SpeechWordTiming]) -> str:
    text = ""
    for item in words:
        token = item.text.strip()
        if not token:
            continue
        if (
            text
            and text[-1].isascii()
            and text[-1].isalnum()
            and token[0].isascii()
            and token[0].isalnum()
        ):
            text += " "
        text += token
    return text


def _apply_provider_timing(scene: Scene, cues: list[TimingCue]) -> None:
    if cues:
        scene.timing_cues = cues
        scene.timing_source = "provider"
    elif scene.timing_source == "provider":
        scene.timing_cues = []
        scene.timing_source = None


def _synthesis_identity(provider: object) -> object:
    identity = getattr(provider, "synthesis_identity", None)
    return identity() if callable(identity) else None


def _write_alignment(
    path: Path,
    audio_fingerprint: str,
    result: SpeechSynthesisResult,
) -> None:
    payload = {
        "schema": 1,
        "audio_fingerprint": audio_fingerprint,
        "duration_sec": result.duration_sec,
        "sentences": [
            {
                "text": sentence.text,
                "start_sec": sentence.start_sec,
                "end_sec": sentence.end_sec,
                "words": [
                    {
                        "text": word.text,
                        "start_sec": word.start_sec,
                        "end_sec": word.end_sec,
                        "confidence": word.confidence,
                    }
                    for word in sentence.words
                ],
            }
            for sentence in result.sentences
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_alignment(
    path: Path,
    audio_fingerprint: str,
) -> SpeechSynthesisResult | None:
    if not path.exists():
        return None
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != 1
            or payload.get("audio_fingerprint") != audio_fingerprint
        ):
            return None
        duration = float(payload["duration_sec"])
        if not math.isfinite(duration) or duration <= 0:
            return None
        raw_sentences = payload.get("sentences", [])
        if not isinstance(raw_sentences, list):
            return None
        sentences_list: list[SpeechSentenceTiming] = []
        tolerance = max(0.25, duration * 0.03)
        for item in raw_sentences:
            if not isinstance(item, dict):
                return None
            start_sec = float(item["start_sec"])
            end_sec = float(item["end_sec"])
            if (
                not math.isfinite(start_sec)
                or not math.isfinite(end_sec)
                or start_sec < 0
                or end_sec <= start_sec
                or end_sec > duration + tolerance
            ):
                return None
            raw_words = item.get("words", [])
            if not isinstance(raw_words, list):
                return None
            words_list: list[SpeechWordTiming] = []
            previous_word_end = 0.0
            for word in raw_words:
                if not isinstance(word, dict):
                    return None
                word_start = float(word["start_sec"])
                word_end = float(word["end_sec"])
                confidence = (
                    float(word["confidence"])
                    if word.get("confidence") is not None
                    else None
                )
                if (
                    not math.isfinite(word_start)
                    or not math.isfinite(word_end)
                    or word_start < 0
                    or word_end <= word_start
                    or word_end > duration + tolerance
                    or word_start < previous_word_end - 0.03
                    or (
                        confidence is not None
                        and (not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0)
                    )
                ):
                    return None
                words_list.append(
                    SpeechWordTiming(
                        text=str(word["text"]),
                        start_sec=word_start,
                        end_sec=min(duration, word_end),
                        confidence=confidence,
                    )
                )
                previous_word_end = word_end
            sentences_list.append(
                SpeechSentenceTiming(
                    text=str(item["text"]),
                    start_sec=start_sec,
                    end_sec=min(duration, end_sec),
                    words=tuple(words_list),
                )
            )
        sentences = tuple(sentences_list)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return SpeechSynthesisResult(duration_sec=duration, sentences=sentences)
