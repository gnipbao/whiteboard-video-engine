"""OpenAI LLM provider."""

from __future__ import annotations

import json

from ..config import settings
from ..models import STYLE_SUFFIX
from ..prompts import load_prompt


class OpenAILLMProvider:
    """Split scripts through an OpenAI-compatible chat model."""

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when MOCK is not enabled")
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover - optional dependency branch
            raise RuntimeError("Install optional dependency `openai` or run with MOCK=1") from exc
        self._client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

    def split_scenes(self, script: str, scene_count: int) -> list[dict[str, object]]:
        """Return scene dictionaries from the configured model."""

        system_prompt = load_prompt("scene_split.txt")
        user_prompt = f"Scene count: {scene_count}\nStyle suffix: {STYLE_SUFFIX}\n\nScript:\n{script}"
        response = self._client.chat.completions.create(
            model=settings.llm_model,
            temperature=0.4,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        scenes = data.get("scenes", data)
        if not isinstance(scenes, list):
            raise TypeError("LLM response did not contain a scenes list")
        return scenes
