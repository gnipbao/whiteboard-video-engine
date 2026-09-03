"""Offline scene splitter used for tests and demos."""

from __future__ import annotations

import re


class MockLLMProvider:
    """Deterministic storyboard generator with no network dependency."""

    def __init__(self, *, style_guidance: str | None = None) -> None:
        self.style_guidance = style_guidance or "simple hand-drawn whiteboard"
        self.model = "mock-scene-planner-v1"

    def planning_identity(self) -> dict[str, object]:
        """Identify the deterministic mock planning algorithm."""

        return {"model": self.model, "algorithm_schema": 1}

    def split_scenes(self, script: str, scene_count: int) -> list[dict[str, object]]:
        """Split script text into simple scene dictionaries."""

        chunks = _split_text(script, max(1, scene_count))
        scenes: list[dict[str, object]] = []
        for idx, chunk in enumerate(chunks, start=1):
            subject = _subject(chunk)
            scenes.append(
                {
                    "id": idx,
                    "narration": chunk,
                    "image_prompt": f"{subject}, clean explanatory diagram",
                    "duration_sec": max(3.0, min(8.0, len(chunk) / 18.0)),
                }
            )
        return scenes


def _split_text(script: str, scene_count: int) -> list[str]:
    cleaned = re.sub(r"\s+", " ", script).strip()
    if not cleaned:
        cleaned = "A simple idea is introduced, explained, and summarized."
    sentences = [
        s.strip()
        for s in re.findall(r"[^。！？.!?]+[。！？.!?]?", cleaned)
        if s.strip()
    ]
    if len(sentences) >= scene_count:
        buckets = ["" for _ in range(scene_count)]
        for idx, sentence in enumerate(sentences):
            buckets[min(scene_count - 1, idx * scene_count // len(sentences))] += (
                " "
                if buckets[min(scene_count - 1, idx * scene_count // len(sentences))]
                else ""
            ) + sentence
        return [b for b in buckets if b]
    words = cleaned.split()
    if len(words) <= scene_count:
        return sentences or [cleaned]
    step = max(1, len(words) // scene_count)
    return [" ".join(words[i : i + step]) for i in range(0, len(words), step)][
        :scene_count
    ]


def _subject(text: str) -> str:
    words = re.findall(r"[\w\u4e00-\u9fff]+", text)
    if not words:
        return "simple concept"
    return " ".join(words[:8])
