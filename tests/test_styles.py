import json
import re
from pathlib import Path

import pytest

from whiteboard_skill.fingerprints import stable_fingerprint
from whiteboard_skill.styles import (
    DEFAULT_STYLE_ID,
    LEGACY_STYLE_SUFFIX,
    STYLE_PROMPT_MARKER,
    available_styles,
    build_storyboard_prompt,
    custom_style_from_text,
    load_custom_style,
    recommend_styles,
    resolve_builtin_style,
    resolve_style,
    style_planning_payload,
    style_semantic_payload,
)


def test_builtin_styles_are_thirty_unique_recipes_in_stable_order():
    styles = available_styles()

    assert len(styles) == 30
    assert [style.order for style in styles] == list(range(1, 31))
    assert len({style.id for style in styles}) == 30
    assert len({style.name_zh for style in styles}) == 30
    assert len({style.name_en for style in styles}) == 30
    assert styles[0].id == DEFAULT_STYLE_ID
    assert styles[0].render.color_fill_scope == "block"
    assert resolve_builtin_style("anime-graphite").render.color_fill_scope == "scene"


def test_every_builtin_resolves_by_id_order_localized_names_and_aliases():
    for style in available_styles():
        selectors = (
            style.id,
            str(style.order),
            style.name_zh,
            style.name_en,
            *style.aliases,
        )
        for selector in selectors:
            assert resolve_builtin_style(selector).id == style.id

    assert resolve_builtin_style("WARM_CRAYON_STORYBOOK").id == DEFAULT_STYLE_ID
    assert resolve_builtin_style("whiteboard-explainer").id == "clean-whiteboard"
    assert resolve_builtin_style("rawkid-crayon").id == "raw-kid-crayon"
    assert resolve_builtin_style("ms-paint-bad-doodle").id == "ms-paint-doodle"
    assert resolve_builtin_style().id == DEFAULT_STYLE_ID


def test_unknown_builtin_selection_lists_actionable_choices():
    with pytest.raises(ValueError) as exc_info:
        resolve_builtin_style("not-a-real-style")

    message = str(exc_info.value)
    assert "Unknown visual style 'not-a-real-style'" in message
    assert "Available styles:" in message
    assert DEFAULT_STYLE_ID in message
    assert available_styles()[-1].id in message


@pytest.mark.parametrize(
    ("script", "expected_style_id"),
    [
        ("三个和尚没水喝，是一个发生在古代寺庙里的寓言。", "ink-wash-minimal"),
        ("用流程说明分步骤讲解一个商业教程。", "clean-whiteboard"),
    ],
)
def test_auto_style_recommendation_matches_story_semantics(
    script: str,
    expected_style_id: str,
):
    assert resolve_style("auto", script=script).id == expected_style_id
    assert recommend_styles(script, limit=1)[0].id == expected_style_id


def test_recommendation_can_return_the_full_catalog_without_duplicates():
    recommendations = recommend_styles("通用故事", limit=100)

    assert len(recommendations) == 30
    assert len({style.id for style in recommendations}) == 30


def test_storyboard_prompt_is_idempotent_and_enforces_production_contract():
    style = resolve_builtin_style("ink-wash-minimal")
    content = f"Three complete monks cooperate beside a temple{LEGACY_STYLE_SUFFIX}"

    prompt = build_storyboard_prompt(content, style, theme="合作解决缺水问题")
    rebuilt = build_storyboard_prompt(prompt, style, theme="合作解决缺水问题")

    assert rebuilt == prompt
    assert prompt.count(STYLE_PROMPT_MARKER.strip()) == 1
    assert LEGACY_STYLE_SUFFIX not in prompt
    assert style.aesthetic in prompt
    assert "Keep every person and important prop complete inside the canvas" in prompt
    assert "never cut one connected person or object into artificial blocks" in prompt
    assert "no text, letters, numbers, pseudo-writing" in prompt
    assert "no text, letters, numbers, pseudo-writing" in rebuilt
    assert prompt.splitlines()[-1].startswith("Production contract:")


def test_anime_graphite_prompt_requires_one_coherent_background_plane():
    prompt = build_storyboard_prompt(
        "A complete girl walks through a snowy street",
        resolve_builtin_style("anime-graphite"),
    )

    assert "Background layer contract:" in prompt
    assert "one coherent low-contrast background plane" in prompt
    assert "hard-edged rectangular islands" in prompt


def test_inline_custom_style_has_stable_content_id():
    first = custom_style_from_text(
        "Loose graphite contours with one muted blue accent.",
        name="First display name",
    )
    second = custom_style_from_text(
        "  Loose graphite contours   with one muted blue accent.  ",
        name="Another display name",
    )

    assert first.id == second.id
    assert re.fullmatch(r"custom-[0-9a-f]{12}", first.id)
    assert resolve_style(custom_style=first.aesthetic).id == first.id


def test_inline_custom_style_description_length_is_bounded():
    assert custom_style_from_text("x" * 4_000).id.startswith("custom-")
    with pytest.raises(ValueError, match="at most 4000 characters"):
        custom_style_from_text("x" * 4_001)
    with pytest.raises(ValueError, match="cannot be empty"):
        custom_style_from_text(" \n\t ")


def test_json_custom_style_extends_builtin_and_overrides_render_only(
    tmp_path: Path,
):
    base = resolve_builtin_style("minimal-line-explainer")
    style_file = tmp_path / "custom-style.json"
    style_file.write_text(
        json.dumps(
            {
                "id": "custom-minimal-process",
                "extends": "minimal-line-explainer",
                "name_zh": "极简流程定制",
                "name_en": "Custom minimal process",
                "render": {
                    "draw_blocks": 2,
                    "block_overlap": 0.24,
                    "color_fill_scope": "scene",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    custom = load_custom_style(style_file)

    assert custom.id == "custom-minimal-process"
    assert custom.aesthetic == base.aesthetic
    assert custom.paper == base.paper
    assert custom.render.line_thickness == base.render.line_thickness
    assert custom.render.draw_blocks == 2
    assert custom.render.block_overlap == pytest.approx(0.24)
    assert custom.render.color_fill_scope == "scene"


def test_json_custom_style_accepts_strict_json_types_and_array_metadata(
    tmp_path: Path,
):
    style_file = tmp_path / "strict-valid-style.json"
    style_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "custom-strict-valid",
                "order": 2,
                "best_for": ["stories", "explainers"],
                "aliases": ["strict-valid"],
                "render": {
                    "line_art_snap": False,
                    "line_thickness": 2,
                    "block_overlap": 0.2,
                },
            }
        ),
        encoding="utf-8",
    )

    custom = load_custom_style(style_file)

    assert custom.schema_version == 1
    assert custom.order == 2
    assert custom.best_for == ("stories", "explainers")
    assert custom.aliases == ("strict-valid",)
    assert custom.render.line_art_snap is False
    assert custom.render.line_thickness == 2
    assert custom.render.block_overlap == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("invalid_overlay", "field_name"),
    [
        ({"schema_version": True}, "schema_version"),
        ({"schema_version": 1.0}, "schema_version"),
        ({"order": 1.9}, "order"),
        ({"order": "2"}, "order"),
        ({"best_for": "stories"}, "best_for"),
        ({"render": {"line_art_snap": "false"}}, "line_art_snap"),
        ({"render": {"line_thickness": "2"}}, "line_thickness"),
        ({"render": {"block_overlap": "0.2"}}, "block_overlap"),
    ],
)
def test_json_custom_style_rejects_values_that_require_type_coercion(
    tmp_path: Path,
    invalid_overlay: dict[str, object],
    field_name: str,
):
    style_file = tmp_path / f"invalid-{field_name}.json"
    style_file.write_text(
        json.dumps({"extends": DEFAULT_STYLE_ID, **invalid_overlay}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        load_custom_style(style_file)

    assert field_name in str(exc_info.value)


def test_json_custom_style_rejects_unknown_top_level_field(tmp_path: Path):
    style_file = tmp_path / "extra-field.json"
    style_file.write_text(
        json.dumps(
            {
                "extends": DEFAULT_STYLE_ID,
                "name_en": "Unexpected field",
                "mood_strength": 0.8,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError, match="Unsupported custom style fields: mood_strength"
    ):
        load_custom_style(style_file)


def test_json_custom_style_cannot_impersonate_a_builtin_id(tmp_path: Path):
    style_file = tmp_path / "impersonated-style.json"
    style_file.write_text(
        json.dumps({"id": "clean-whiteboard", "extends": "clean-whiteboard"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must start with 'custom-'"):
        load_custom_style(style_file)


@pytest.mark.parametrize(
    "dangerous_payload",
    [
        {"command": "sh -c 'do something'"},
        {"lineart_command": "/tmp/custom-extractor"},
        {"environment": {"TOKEN": "secret"}},
        {"render": {"shell": "bash"}},
        {"provenance": "official upstream recipe"},
    ],
)
def test_json_custom_style_rejects_dangerous_configuration_fields(
    tmp_path: Path,
    dangerous_payload: dict[str, object],
):
    style_file = tmp_path / "dangerous-style.json"
    style_file.write_text(
        json.dumps(
            {
                "extends": DEFAULT_STYLE_ID,
                "name_en": "Unsafe custom style",
                **dangerous_payload,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_custom_style(style_file)


def test_semantic_payload_ignores_display_metadata_but_tracks_visual_changes():
    style = resolve_builtin_style(DEFAULT_STYLE_ID)
    cosmetic_variant = style.model_copy(
        update={
            "name_zh": "仅修改展示名称",
            "name_en": "Display name only",
            "family": "Different display family",
            "summary": "Different display summary",
            "best_for": ("different",),
            "aliases": ("different-alias",),
            "compatibility": "experimental",
            "provenance": "different display provenance",
        }
    )
    visual_variant = style.model_copy(
        update={"palette": f"{style.palette} Add one cobalt accent."}
    )

    original_payload = style_semantic_payload(style)
    assert style_semantic_payload(cosmetic_variant) == original_payload
    assert stable_fingerprint(
        style_semantic_payload(cosmetic_variant)
    ) == stable_fingerprint(original_payload)
    assert style_semantic_payload(visual_variant) != original_payload


def test_planning_payload_ignores_renderer_only_changes():
    style = resolve_builtin_style(DEFAULT_STYLE_ID)
    render_variant = style.model_copy(
        update={
            "render": style.render.model_copy(
                update={"draw_blocks": 2, "line_thickness": 3}
            )
        }
    )
    visual_variant = style.model_copy(
        update={"palette": f"{style.palette} Add one cobalt accent."}
    )

    assert style_planning_payload(render_variant) == style_planning_payload(style)
    assert style_planning_payload(visual_variant) != style_planning_payload(style)


def test_json_overlay_generated_id_and_semantic_payload_are_key_order_stable(
    tmp_path: Path,
):
    first_payload = {
        "extends": "clean-whiteboard",
        "name_zh": "定制流程",
        "name_en": "Custom process",
        "aesthetic": "Loose black marker with a single blue accent.",
        "render": {"draw_blocks": 3, "block_overlap": 0.2},
    }
    second_payload = {
        "render": {"block_overlap": 0.2, "draw_blocks": 3},
        "aesthetic": "Loose black marker with a single blue accent.",
        "name_en": "Custom process",
        "name_zh": "定制流程",
        "extends": "clean-whiteboard",
    }
    first_file = tmp_path / "first.json"
    second_file = tmp_path / "second.json"
    first_file.write_text(
        json.dumps(first_payload, ensure_ascii=False), encoding="utf-8"
    )
    second_file.write_text(
        json.dumps(second_payload, ensure_ascii=False), encoding="utf-8"
    )

    first = load_custom_style(first_file)
    second = load_custom_style(second_file)

    assert first.id == second.id
    assert style_semantic_payload(first) == style_semantic_payload(second)
    assert stable_fingerprint(style_semantic_payload(first)) == stable_fingerprint(
        style_semantic_payload(second)
    )
