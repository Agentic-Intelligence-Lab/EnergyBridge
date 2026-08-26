from __future__ import annotations

import json

import pytest

from energybridge.harness.memory import (
    EVENT_CONTEXT_VERSION,
    MEMORY_VERSION,
    build_event_context,
    compact_memory_context,
    initialize_memory,
    retrieve_relevant_events,
    update_memory,
)


def _questionnaire() -> dict:
    return {
        "source": "observable_onboarding",
        "answers": [
            {
                "id": "vpp_priority",
                "answer": "Balance savings with comfort.",
                "selected_option_ids": ["balanced_tradeoff"],
            },
            {
                "id": "appliance_shift_consent",
                "answer": "Ask before moving a deadline.",
                "selected_option_ids": ["do_not_move_without_approval"],
            },
        ],
        "inferred_profile": {
            "comfort_priority": "medium",
            "automation_preference": "ask_before_vpp_specific_changes",
            # Evaluator-only fields must not enter controller memory.
            "scoring_weights": {"comfort": 0.9},
            "system_prompt": "SECRET SYSTEM PROMPT",
            "tags": {"control": "confirm_required"},
        },
        "hidden_persona": {"private_fact": "SECRET PRIVATE FACT"},
        "preference_rules": ["Ask before moving a protected service deadline."],
    }


def _context(
    event_id: str,
    *,
    hour: float = 18.0,
    occupied: bool = True,
    price: str = "high",
) -> dict:
    return build_event_context(
        {
            "id": event_id,
            "type": "vpp_peak",
            "trigger_h": hour,
            "end_h": hour + 1.0,
            "price_level": price,
            "persona_config": {"private_fact": "DO NOT COPY"},
        },
        calendar={"occupied": occupied, "constraints": ["dinner"]},
        home_state={"indoor_temp_c": 25.0},
        proposed_plan={"mode": "balanced", "setpoint": 26.0},
        observed_at=f"2026-08-{10 + int(event_id[-1]):02d}T10:00:00+00:00",
        observations={
            "sensor_quality": "good",
            "hiddenPersona": {"private_fact": "DO NOT COPY"},
        },
    )


def test_initialize_memory_is_observable_only_and_evidence_backed() -> None:
    questionnaire = _questionnaire()
    original = json.dumps(questionnaire, ensure_ascii=False, sort_keys=True)

    memory = initialize_memory(questionnaire, persona_id="family-7", method="agent")

    assert memory["version"] == MEMORY_VERSION
    assert memory["owner"] == {"persona_id": "family-7", "method": "agent"}
    assert memory["privacy_boundary"]["scope"] == "agent_observable_only"
    assert memory["beliefs"]["vpp_priority"]["value"] == "balanced_tradeoff"
    belief = memory["beliefs"]["comfort_priority"]
    assert 0.0 < belief["confidence"] < 1.0
    assert belief["evidence_count"] == 1
    assert belief["contradiction_count"] == 0
    assert belief["provenance"][0]["source"] == "onboarding_questionnaire"
    serialized = json.dumps(memory, ensure_ascii=False, sort_keys=True)
    assert "SECRET" not in serialized
    assert "scoring_weights" not in serialized
    assert json.dumps(questionnaire, ensure_ascii=False, sort_keys=True) == original


def test_build_event_context_filters_hidden_persona_recursively() -> None:
    context = _context("event-1")

    assert context["version"] == EVENT_CONTEXT_VERSION
    assert context["features"]["time_bucket"] == "evening"
    assert context["features"]["occupancy"] == "occupied"
    assert context["context_signature"].startswith("event_type=vpp_peak")
    serialized = json.dumps(context, ensure_ascii=False, sort_keys=True)
    assert "DO NOT COPY" not in serialized
    assert "persona_config" not in serialized
    assert "hiddenPersona" not in serialized


def test_feedback_revises_profile_with_contradiction_and_provenance() -> None:
    initial = initialize_memory(_questionnaire(), persona_id="family-7")
    context = _context("event-1")

    updated = update_memory(
        initial,
        context,
        {
            "accepted": False,
            "score": 2,
            "feedback": "Too hot and disruptive; ask me first next time.",
            "preference_observations": [
                {"key": "comfort_priority", "value": "high", "confidence": 0.9},
                {"key": "confirmation_required", "value": True, "confidence": 0.95},
            ],
        },
    )

    assert initial["beliefs"]["comfort_priority"]["value"] == "medium"
    revised = updated["beliefs"]["comfort_priority"]
    assert revised["value"] == "high"
    assert revised["evidence_count"] == 2
    assert revised["contradiction_count"] == 1
    assert revised["contradictions"][0]["incoming_value"] == "high"
    assert revised["provenance"][-1]["source"] == "user_feedback"
    assert updated["beliefs"]["confirmation_required"]["value"] is True
    assert updated["events"][0]["negative_feedback"] is True
    assert updated["events"][0]["negative_severity"] >= 0.8
    ledger_item = next(
        item
        for item in reversed(updated["profile_revision_ledger"])
        if item["belief_key"] == "comfort_priority"
    )
    assert ledger_item["contradiction"] is True
    assert ledger_item["source"] == "user_feedback"


def test_retrieval_prefers_similar_negative_event_without_promoting_irrelevant_one() -> None:
    memory = initialize_memory(_questionnaire(), persona_id="family-7")
    positive = _context("event-1")
    relevant_negative = _context("event-2")
    irrelevant_negative = _context("event-3", hour=8.0, occupied=False, price="low")

    memory = update_memory(
        memory,
        positive,
        {"accepted": True, "score": 5, "feedback": "Comfortable and acceptable."},
    )
    memory = update_memory(
        memory,
        relevant_negative,
        {"accepted": False, "score": 1, "feedback": "Too hot and disruptive."},
    )
    memory = update_memory(
        memory,
        irrelevant_negative,
        {"accepted": False, "score": 1, "feedback": "Too cold and disruptive."},
    )

    retrieved = retrieve_relevant_events(memory, _context("event-4"), k=3)

    assert [item["event_id"] for item in retrieved[:2]] == ["event-2", "event-1"]
    assert retrieved[0]["retrieval"]["negative_priority"] > 0
    assert retrieved[0]["retrieval"]["context_similarity"] == 1.0
    assert retrieved[-1]["event_id"] == "event-3"


def test_compact_context_is_bounded_and_keeps_evidence_references() -> None:
    memory = initialize_memory(_questionnaire(), persona_id="family-7")
    for index in range(1, 6):
        context = _context(f"event-{index}")
        memory = update_memory(
            memory,
            context,
            {
                "accepted": index % 2 == 0,
                "score": 2 if index % 2 else 4,
                "feedback": ("Too hot and disruptive. " * 40) if index % 2 else "Worked well.",
            },
        )

    capsule = compact_memory_context(memory, _context("event-6"), k=3, max_chars=2200)
    serialized = json.dumps(capsule, ensure_ascii=False, sort_keys=True)

    assert capsule["privacy_scope"] == "agent_observable_only"
    assert len(serialized) <= 2200
    assert capsule["serialized_chars"] == len(serialized)
    if capsule["relevant_events"]:
        assert capsule["relevant_events"][0]["evidence_ref"].startswith("family-7:")
    assert "hidden_persona" not in serialized


def test_update_rejects_an_unversioned_context() -> None:
    memory = initialize_memory(_questionnaire(), persona_id="family-7")

    with pytest.raises(ValueError, match="build_event_context"):
        update_memory(memory, {"event_id": "event-1"}, {"score": 4})
