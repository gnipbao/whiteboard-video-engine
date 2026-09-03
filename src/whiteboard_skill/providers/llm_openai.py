"""OpenAI LLM provider."""

from __future__ import annotations

import json

from ..config import settings
from ..prompts import load_prompt


class OpenAILLMProvider:
    """Split scripts through an OpenAI-compatible chat model."""

    def __init__(
        self,
        *,
        style_guidance: str | None = None,
        model: str | None = None,
        client=None,
    ) -> None:
        self.style_guidance = (
            style_guidance or "Use a simple hand-drawn whiteboard composition."
        )
        self.model = model or settings.llm_model
        self.base_url = settings.openai_base_url
        if client is not None:
            self._client = client
            return
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when MOCK is not enabled")
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover - optional dependency branch
            raise RuntimeError(
                "Install optional dependency `openai` or run with MOCK=1"
            ) from exc
        self._client = OpenAI(
            api_key=settings.openai_api_key, base_url=self.base_url
        )

    def planning_identity(self) -> dict[str, object]:
        """Identify every configured setting that changes planning output."""

        return {
            "model": self.model,
            "base_url": self.base_url,
            "temperature": 0.4,
            "response_format": "json_object",
        }

    def split_scenes(self, script: str, scene_count: int) -> list[dict[str, object]]:
        """Return scene dictionaries from the configured model."""

        system_prompt = load_prompt("scene_split.txt")
        user_prompt = (
            f"Scene count: {scene_count}\n"
            f"Visual style context: {self.style_guidance}\n"
            "Use this context to choose compatible shapes and composition, but do not repeat "
            f"the style recipe verbatim.\n\nScript:\n{script}"
        )
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=0.4,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        scenes = data.get("scenes", data)
        if not isinstance(scenes, list):
            raise TypeError("LLM response did not contain a scenes list")
        return scenes
