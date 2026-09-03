import base64
import json
from pathlib import Path

import pytest

from whiteboard_skill.config import Settings
from whiteboard_skill.providers.base import SpeechSentenceTiming, SpeechWordTiming
from whiteboard_skill.providers.tts_doubao import (
    DoubaoTTSProvider,
    _bounded_sentence_timings,
    _merge_sentence_timing,
    _parse_sse_lines,
    _payload_duration,
)


class _FakeResponse:
    status = 200

    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self.lines)


def test_doubao_v3_sse_writes_audio_and_uses_seed_tts_2(monkeypatch, tmp_path: Path):
    audio = b"fake-mp3-frames"
    response = _FakeResponse(
        [
            b"event: 352\n",
            f'data: {{"code":0,"data":"{base64.b64encode(audio).decode()}"}}\n'.encode(),
            b"event: 152\n",
            b'data: {"code":20000000}\n',
        ]
    )
    captured = {}

    def opener(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    config = Settings(
        doubao_tts_api_key="test-key",
        doubao_tts_voice="zh_female_vv_uranus_bigtts",
    )
    monkeypatch.setattr("whiteboard_skill.providers.tts_doubao.ffprobe_duration", lambda _path: 1.75)
    provider = DoubaoTTSProvider(config=config, opener=opener)
    output = tmp_path / "voice.mp3"

    duration = provider.synthesize("从前有一只小猫。", output, "")

    assert duration == 1.75
    assert output.read_bytes() == audio
    assert provider.file_extension == ".mp3"
    request = captured["request"]
    assert request.full_url.endswith("/api/v3/tts/unidirectional/sse")
    assert request.headers["X-api-key"] == "test-key"
    assert request.headers["X-api-resource-id"] == "seed-tts-2.0"
    body = json.loads(request.data)
    assert body["req_params"]["speaker"] == "zh_female_vv_uranus_bigtts"
    assert body["req_params"]["audio_params"] == {
        "format": "mp3",
        "sample_rate": 24000,
        "speech_rate": 0,
        "loudness_rate": 0,
        "bit_rate": 64000,
        "enable_subtitle": True,
    }
    assert "sample_rate" not in body["req_params"]


def test_doubao_v3_sse_parses_official_sentence_word_timings():
    audio = base64.b64encode(b"audio").decode()
    lines = [
        b"event: 352\n",
        f'data: {{"code":0,"data":"{audio}"}}\n'.encode(),
        b"event: 351\n",
        (
            'data: {"code":0,"sentence":{"text":"其他人。","phonemes":[],"words":['
            '{"word":"其","startTime":0.205,"endTime":0.315,"confidence":0.853},'
            '{"word":"他人","startTime":0.315,"endTime":0.91,"confidence":0.92}]}}\n'
        ).encode(),
        b"event: 152\n",
        b'data: {"code":20000000,"addition":{"duration":"1100"}}\n',
    ]

    chunks, duration, sentences = _parse_sse_lines(lines)

    assert chunks == [b"audio"]
    assert duration == 1.1
    assert len(sentences) == 1
    assert sentences[0].text == "其他人。"
    assert sentences[0].start_sec == 0.205
    assert sentences[0].end_sec == 0.91
    assert [word.text for word in sentences[0].words] == ["其", "他人"]


def test_doubao_v3_sse_parses_json_encoded_sentence_data():
    nested = json.dumps(
        {
            "sentence": {
                "text": "挑水。",
                "words": [
                    {"word": "挑水", "startTime": 0.1, "endTime": 0.8},
                ],
            }
        },
        ensure_ascii=False,
    )
    line = f"data: {json.dumps({'code': 0, 'data': nested}, ensure_ascii=False)}\n"

    chunks, _, sentences = _parse_sse_lines(
        [
            "event: TTSSubtitle\n",
            line,
            "event: 152\n",
            'data: {"code": 20000000}\n',
        ]
    )

    assert chunks == []
    assert [(item.text, item.start_sec, item.end_sec) for item in sentences] == [
        ("挑水。", 0.1, 0.8)
    ]


def test_doubao_v3_sse_rejects_truncated_audio_stream():
    audio = base64.b64encode(b"partial-audio").decode()

    with pytest.raises(RuntimeError, match="finish event"):
        _parse_sse_lines(["event: 352\n", f'data: {{"code":0,"data":"{audio}"}}\n'])


def test_doubao_duration_is_always_official_milliseconds():
    assert _payload_duration({"addition": {"duration": "99"}}, None) == 0.099
    assert _payload_duration({"addition": '{"duration":"1100"}'}, None) == 1.1


def test_doubao_corrupt_timestamp_cannot_extend_audio_duration():
    sentence = SpeechSentenceTiming(
        "异常",
        0.1,
        1100.0,
        (SpeechWordTiming("异常", 0.1, 1100.0, 0.9),),
    )

    assert _bounded_sentence_timings([sentence], 1.1) == []


def test_doubao_drops_only_corrupt_tail_word_and_keeps_valid_prefix():
    sentence = SpeechSentenceTiming(
        "有效异常",
        0.1,
        99.0,
        (
            SpeechWordTiming("有效", 0.1, 0.8, 0.9),
            SpeechWordTiming("异常", 0.8, 99.0, 1.5),
        ),
    )

    bounded = _bounded_sentence_timings([sentence], 1.1)

    assert len(bounded) == 1
    assert bounded[0].text == "有效"
    assert [word.text for word in bounded[0].words] == ["有效"]


def test_doubao_merges_growing_subtitle_updates_with_timestamp_drift():
    partial = SpeechSentenceTiming(
        "其他",
        0.205,
        0.5,
        (SpeechWordTiming("其他", 0.205, 0.5, 0.9),),
    )
    complete = SpeechSentenceTiming(
        "其他人。",
        0.23,
        0.91,
        (
            SpeechWordTiming("其他", 0.23, 0.5, 0.9),
            SpeechWordTiming("人。", 0.5, 0.91, 0.9),
        ),
    )

    assert _merge_sentence_timing([partial], complete) == [complete]


def test_doubao_finish_event_requires_official_success_code():
    with pytest.raises(RuntimeError, match="finish event"):
        _parse_sse_lines(
            [
                "event: 152\n",
                'data: {"code": 0}\n',
            ]
        )


def test_doubao_ogg_requires_official_48khz_configuration():
    with pytest.raises(ValueError, match="48000 Hz"):
        DoubaoTTSProvider(
            config=Settings(
                doubao_tts_api_key="test-key",
                doubao_tts_format="ogg_opus",
                doubao_tts_sample_rate=24000,
            )
        )


def test_doubao_rejects_unsupported_compressed_bit_rate():
    with pytest.raises(ValueError, match="64000 or 160000"):
        DoubaoTTSProvider(
            config=Settings(
                doubao_tts_api_key="test-key",
                doubao_tts_bit_rate=32000,
            )
        )


def test_doubao_provider_rejects_non_official_endpoint():
    config = Settings(
        doubao_tts_api_key="test-key",
        doubao_tts_endpoint="https://example.com/steal-key",
    )
    with pytest.raises(RuntimeError, match="official Volcengine"):
        DoubaoTTSProvider(config=config)


def test_doubao_provider_error_does_not_echo_provider_message(tmp_path: Path):
    secret_message = "internal detail should not be logged"
    response = _FakeResponse(
        [f'data: {{"code":55000000,"message":"{secret_message}"}}\n'.encode()]
    )
    provider = DoubaoTTSProvider(
        config=Settings(doubao_tts_api_key="test-key"),
        opener=lambda *_args, **_kwargs: response,
    )
    with pytest.raises(RuntimeError) as caught:
        provider.synthesize("测试", tmp_path / "voice.mp3", "zh_female_vv_uranus_bigtts")
    assert "55000000" in str(caught.value)
    assert secret_message not in str(caught.value)
