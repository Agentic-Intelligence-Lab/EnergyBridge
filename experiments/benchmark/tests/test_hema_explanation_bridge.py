from __future__ import annotations

from types import SimpleNamespace

from experiments.benchmark.baselines.hema.message_utils import (
    explanation_output_fields,
    extract_assistant_explanation,
    schedule_prompt_fields,
)


def test_extracts_final_hema_assistant_explanation_after_tool_calls() -> None:
    result = {
        "messages": [
            SimpleNamespace(type="human", content="control the home"),
            SimpleNamespace(type="ai", content="", tool_calls=[{"name": "control_device"}]),
            SimpleNamespace(type="tool", content="ok"),
            SimpleNamespace(
                type="ai",
                content=(
                    "I shifted the washer until after the event and kept hot water ready "
                    "for the evening shower."
                ),
            ),
        ]
    }

    explanation = extract_assistant_explanation(result)

    assert explanation.startswith("I shifted the washer")
    assert "evening shower" in explanation


def test_explanation_extractor_handles_content_blocks_and_never_uses_human_text() -> None:
    result = {
        "messages": [
            {"role": "human", "content": "PRIVATE HUMAN QUERY"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "The schedule keeps comfort stable."},
                    {"type": "tool_call", "name": "ignored"},
                ],
            },
        ]
    }

    explanation = extract_assistant_explanation(result)

    assert explanation == "The schedule keeps comfort stable."
    assert "PRIVATE" not in explanation


def test_explanation_extractor_returns_empty_when_no_assistant_text_exists() -> None:
    assert extract_assistant_explanation({"messages": [{"role": "human", "content": "hello"}]}) == ""


def test_tool_call_preamble_is_not_promoted_when_no_final_answer_exists() -> None:
    result = {
        "messages": [
            {"role": "human", "content": "control the home"},
            {
                "type": "ai",
                "content": "I will query the device first.",
                "tool_calls": [{"name": "get_device_status"}],
            },
            {"role": "tool", "content": "online"},
        ]
    }

    assert extract_assistant_explanation(result) == ""


def test_native_explanation_fields_are_v2_only() -> None:
    text = "I moved the washer after the event and kept hot water ready."
    assert explanation_output_fields(text, adaptive_v2=False) == {"reason": "HEMA Agent"}
    assert explanation_output_fields(text, adaptive_v2=True) == {
        "reason": text,
        "strategy_explanation": {"natural_language": text},
    }


def test_v2_schedule_guidance_uses_actual_window_without_score_target() -> None:
    fields = schedule_prompt_fields(
        {"trigger_h": 42.0, "end_h": 43.5},
        adaptive_v2=True,
    )
    rendered = " ".join(fields.values())

    assert "[18.00, 19.50)" in rendered
    assert "18:00-19:00" not in rendered
    assert "1/5" not in rendered
    assert "satisfaction" not in rendered.lower()
    assert "incomplete" in fields["missing_commands"]


def test_legacy_schedule_guidance_remains_frozen() -> None:
    fields = schedule_prompt_fields(
        {"trigger_h": 42.0, "end_h": 43.5},
        adaptive_v2=False,
    )
    rendered = " ".join(fields.values())

    assert "18:00-19:00" in rendered
    assert "user satisfaction will be 1/5" in rendered
    assert fields["event_check"] == ""
