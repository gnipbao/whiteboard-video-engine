from pathlib import Path

from whiteboard_skill.models import Project, Scene, TimingCue
from whiteboard_skill.providers.base import (
    SpeechSentenceTiming,
    SpeechSynthesisResult,
    SpeechWordTiming,
)
from whiteboard_skill.tts import _speech_timing_cues, synthesize_scene_audio


class _FakeTTS:
    default_voice = "story-voice"
    file_extension = ".mp3"

    def __init__(self) -> None:
        self.calls = 0
        self.voices: list[str] = []

    def synthesize(self, _text: str, out_path: Path, voice: str) -> float:
        self.calls += 1
        self.voices.append(voice)
        out_path.write_bytes(b"audio")
        return 2.25


def test_tts_duration_is_recorded_and_extends_planned_scene(tmp_path: Path):
    scenes = [Scene(id=1, narration="故事", image_prompt="image", duration_sec=1.0)]
    provider = _FakeTTS()

    result = synthesize_scene_audio(scenes, provider, tmp_path, voice=None)

    assert result[0].audio_path == tmp_path / "scene_01.mp3"
    assert result[0].audio_duration_sec == 2.25
    assert result[0].duration_sec == 2.25
    assert provider.calls == 1
    assert provider.voices == ["story-voice"]


def test_narrated_scene_uses_voice_duration_instead_of_plan_dead_air(tmp_path: Path):
    scene = Scene(
        id=1,
        narration="很短的旁白",
        image_prompt="image",
        duration_sec=8.0,
    )

    synthesize_scene_audio([scene], _FakeTTS(), tmp_path, voice=None)

    assert scene.planned_duration_sec == 8.0
    assert scene.duration_sec == 2.25


def test_tts_resume_invalidates_audio_when_voice_changes(monkeypatch, tmp_path: Path):
    scene = Scene(id=1, narration="故事", image_prompt="image")
    provider = _FakeTTS()
    synthesize_scene_audio([scene], provider, tmp_path, voice="voice-a")
    monkeypatch.setattr("whiteboard_skill.tts.ffprobe_duration", lambda _path: 2.25)

    synthesize_scene_audio([scene], provider, tmp_path, voice="voice-a", resume=True)
    assert provider.calls == 1

    synthesize_scene_audio([scene], provider, tmp_path, voice="voice-b", resume=True)
    assert provider.calls == 2


class _DetailedTTS(_FakeTTS):
    def synthesize(self, _text: str, _out_path: Path, _voice: str) -> float:
        raise AssertionError("detailed timing path should be preferred")

    def synthesize_detailed(
        self,
        _text: str,
        out_path: Path,
        _voice: str,
    ) -> SpeechSynthesisResult:
        out_path.write_bytes(b"audio")
        return SpeechSynthesisResult(
            duration_sec=2.4,
            sentences=(
                SpeechSentenceTiming("先画和尚", 0.1, 1.0),
                SpeechSentenceTiming("再画水缸", 1.2, 2.2),
            ),
        )


def test_detailed_tts_timings_override_authored_cues(tmp_path: Path):
    scene = Scene(
        id=1,
        narration="先画和尚，再画水缸。",
        image_prompt="image",
        duration_sec=1.0,
        timing_cues=[TimingCue(text="旧估计", start_sec=0.0, end_sec=0.5)],
    )

    synthesize_scene_audio([scene], _DetailedTTS(), tmp_path, voice=None)

    assert [(cue.text, cue.start_sec, cue.end_sec) for cue in scene.timing_cues] == [
        ("先画和尚", 0.1, 1.0),
        ("再画水缸", 1.2, 2.2),
    ]
    assert scene.audio_duration_sec == 2.4
    assert scene.duration_sec == 2.4


def test_word_timings_form_short_phrase_cues_and_keep_true_onset():
    text = "和尚们一起把水挑回寺院，水缸又满了。"
    words = tuple(
        SpeechWordTiming(
            character,
            0.205 + index * 0.12,
            0.305 + index * 0.12,
            0.9,
        )
        for index, character in enumerate(text)
    )
    result = SpeechSynthesisResult(
        duration_sec=3.0,
        sentences=(SpeechSentenceTiming(text, words[0].start_sec, words[-1].end_sec, words),),
    )

    cues = _speech_timing_cues(result)

    assert len(cues) == 2
    assert cues[0].start_sec == 0.205
    assert cues[0].text.endswith("，")
    assert cues[-1].end_sec == words[-1].end_sec
    assert all(len("".join(cue.text.split())) <= 14 for cue in cues)
    assert all(cue.draw_to is not None for cue in cues)
    assert cues[-1].draw_to == 1.0


def test_legacy_audio_timing_is_migrated_as_unknown_not_authored():
    project = Project.model_validate(
        {
            "title": "legacy",
            "scenes": [
                {
                    "id": 1,
                    "narration": "旧旁白",
                    "image_prompt": "image",
                    "duration_sec": 4.0,
                    "audio_duration_sec": 4.0,
                    "audio_fingerprint": "old",
                    "timing_cues": [
                        {"text": "旧旁白", "start_sec": 0.1, "end_sec": 3.8}
                    ],
                }
            ],
        }
    )

    assert project.schema_version == 2
    assert project.scenes[0].planned_duration_sec == 0.0
    assert project.scenes[0].timing_source == "unknown"


def test_provider_timing_is_coalesced_to_persisted_scene_limit():
    sentences = tuple(
        SpeechSentenceTiming(
            text=f"句子{index}",
            start_sec=index * 0.2,
            end_sec=index * 0.2 + 0.1,
        )
        for index in range(70)
    )

    cues = _speech_timing_cues(
        SpeechSynthesisResult(duration_sec=14.1, sentences=sentences)
    )
    scene = Scene(
        id=1,
        narration="长旁白",
        image_prompt="image",
        timing_cues=cues,
        timing_source="provider",
    )

    assert len(cues) == 64
    assert Project(title="story", scenes=[scene]).model_dump_json()


class _CachedDetailedTTS(_FakeTTS):
    def __init__(self) -> None:
        super().__init__()
        self.detailed_calls = 0

    def synthesize_detailed(
        self,
        _text: str,
        out_path: Path,
        _voice: str,
    ) -> SpeechSynthesisResult:
        self.detailed_calls += 1
        out_path.write_bytes(b"audio")
        words = (
            SpeechWordTiming("开始", 0.2, 0.8, 0.95),
            SpeechWordTiming("挑水", 0.9, 1.7, 0.94),
        )
        return SpeechSynthesisResult(
            duration_sec=2.0,
            sentences=(SpeechSentenceTiming("开始挑水", 0.2, 1.7, words),),
        )


def test_detailed_timing_sidecar_restores_resume_and_missing_cache_regenerates(
    monkeypatch,
    tmp_path: Path,
):
    scene = Scene(id=1, narration="开始挑水", image_prompt="image", duration_sec=1.0)
    provider = _CachedDetailedTTS()

    synthesize_scene_audio([scene], provider, tmp_path, voice=None)
    assert provider.detailed_calls == 1
    assert (tmp_path / "scene_01.alignment.json").exists()

    scene.timing_cues = []
    scene.timing_source = None
    monkeypatch.setattr("whiteboard_skill.tts.ffprobe_duration", lambda _path: 2.0)
    synthesize_scene_audio([scene], provider, tmp_path, voice=None, resume=True)
    assert provider.detailed_calls == 1
    assert scene.timing_source == "provider"
    assert [cue.text for cue in scene.timing_cues] == ["开始挑水"]

    (tmp_path / "scene_01.alignment.json").unlink()
    synthesize_scene_audio([scene], provider, tmp_path, voice=None, resume=True)
    assert provider.detailed_calls == 2


def test_resume_regenerates_when_cached_audio_no_longer_matches_alignment(
    monkeypatch,
    tmp_path: Path,
):
    scene = Scene(id=1, narration="开始挑水", image_prompt="image")
    provider = _CachedDetailedTTS()
    synthesize_scene_audio([scene], provider, tmp_path, voice=None)

    monkeypatch.setattr("whiteboard_skill.tts.ffprobe_duration", lambda _path: 1.0)
    synthesize_scene_audio([scene], provider, tmp_path, voice=None, resume=True)

    assert provider.detailed_calls == 2
    assert scene.audio_duration_sec == 2.0


class _NoTimingDetailedTTS(_FakeTTS):
    def synthesize_detailed(
        self,
        _text: str,
        out_path: Path,
        _voice: str,
    ) -> SpeechSynthesisResult:
        out_path.write_bytes(b"audio")
        return SpeechSynthesisResult(duration_sec=1.5)


def test_fresh_provider_without_timing_clears_old_provider_cues(tmp_path: Path):
    scene = Scene(
        id=1,
        narration="故事",
        image_prompt="image",
        timing_cues=[TimingCue(text="旧结果", start_sec=0.1, end_sec=1.0)],
        timing_source="provider",
    )

    synthesize_scene_audio([scene], _NoTimingDetailedTTS(), tmp_path, voice=None)

    assert scene.timing_cues == []
    assert scene.timing_source is None


class _ConfiguredTTS(_FakeTTS):
    setting = 0

    def synthesis_identity(self):
        return {"speech_rate": self.setting}


def test_provider_synthesis_settings_invalidate_resume(monkeypatch, tmp_path: Path):
    scene = Scene(id=1, narration="故事", image_prompt="image")
    provider = _ConfiguredTTS()
    synthesize_scene_audio([scene], provider, tmp_path, voice=None)
    monkeypatch.setattr("whiteboard_skill.tts.ffprobe_duration", lambda _path: 2.25)

    synthesize_scene_audio([scene], provider, tmp_path, voice=None, resume=True)
    assert provider.calls == 1

    provider.setting = 20
    synthesize_scene_audio([scene], provider, tmp_path, voice=None, resume=True)
    assert provider.calls == 2
