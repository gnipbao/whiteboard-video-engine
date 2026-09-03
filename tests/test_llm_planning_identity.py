import hashlib
from types import SimpleNamespace

from whiteboard_skill.prompts import load_prompt
from whiteboard_skill.providers import base as provider_base
from whiteboard_skill.providers.base import llm_planning_identity
from whiteboard_skill.providers.llm_mock import MockLLMProvider
from whiteboard_skill.providers.llm_openai import OpenAILLMProvider


class _CapturingCompletions:
    def __init__(self) -> None:
        self.request: dict[str, object] | None = None

    def create(self, **kwargs):
        self.request = kwargs
        message = SimpleNamespace(content='{"scenes": []}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _CapturingClient:
    def __init__(self) -> None:
        self.completions = _CapturingCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def test_openai_planning_identity_uses_the_actual_request_model_and_template():
    client = _CapturingClient()
    provider = OpenAILLMProvider(model="planner-model-v2", client=client)

    identity = llm_planning_identity(provider)
    provider.split_scenes("A short story.", 2)

    assert identity["schema"] == 1
    assert identity["provider"].endswith(".OpenAILLMProvider")
    assert identity["provider_settings"] == {
        "model": "planner-model-v2",
        "base_url": provider.base_url,
        "temperature": 0.4,
        "response_format": "json_object",
    }
    assert identity["scene_split_template_sha256"] == hashlib.sha256(
        load_prompt("scene_split.txt").encode("utf-8")
    ).hexdigest()
    assert client.completions.request is not None
    assert client.completions.request["model"] == "planner-model-v2"


def test_planning_identity_changes_when_the_packaged_template_changes(monkeypatch):
    provider = MockLLMProvider()
    original = llm_planning_identity(provider)

    monkeypatch.setattr(provider_base, "load_prompt", lambda _name: "revised template")
    revised = llm_planning_identity(provider)

    assert original["provider_settings"] == revised["provider_settings"]
    assert (
        original["scene_split_template_sha256"]
        != revised["scene_split_template_sha256"]
    )


def test_legacy_provider_without_identity_gets_a_stable_model_fallback():
    class LegacyProvider:
        model = "legacy-model"

    first = llm_planning_identity(LegacyProvider())
    second = llm_planning_identity(LegacyProvider())

    assert first == second
    assert first["provider_settings"] == {"model": "legacy-model"}
