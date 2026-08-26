from __future__ import annotations

import json
import re

from energybridge.harness.profile_v3 import (
    HOUSEHOLD_MODEL_VERSION,
    PROFILE_CAPSULE_VERSION,
    build_profile_capsule,
    estimate_prompt_tokens,
    initialize_household_model,
    sanitize_observable_payload,
    update_household_model,
)


NOW = "2026-08-27T08:00:00+00:00"


def _onboarding() -> dict:
    return {
        "source": "observable_onboarding",
        "answers": [
            {
                "id": "vpp_priority",
                "selected_option_ids": ["bill_savings_first"],
                "answer": "Show me a concrete saving, while keeping us comfortable.",
            },
            {
                "id": "thermostat_flexibility",
                "selected_option_ids": ["small_1c_short"],
                "answer": "About one degree for a short event is fine.",
            },
            {
                "id": "appliance_shift_consent",
                "selected_option_ids": ["shift_1_2h_deadline_protected"],
                "answer": "A short move is fine if dinner and the finish time are protected.",
            },
        ],
        # These were authored outside the observable conversation and must not
        # be trusted or copied, even when supplied in a V2-compatible payload.
        "inferred_profile": {
            "comfort_priority": "low",
            "system_prompt": "SECRET_HIDDEN_PROMPT",
        },
        "preference_rules": ["SECRET_LLM_RULE"],
        "hidden_persona": {"private_fact": "SECRET_PRIVATE_FACT"},
        "base_model": "SECRET_MODEL_ID",
    }


def _calendar() -> dict:
    return {
        "days": [
            {
                "date": "2026-08-28",
                "day_type": "weekday",
                "events": [
                    {
                        "title": "Evening shower",
                        "start_h": 21,
                        "end_h": 21.5,
                        "location_type": "home",
                        "persona_config": {"secret": "SECRET_CALENDAR_PERSONA"},
                    }
                ],
                "system_prompt": "SECRET_CALENDAR_PROMPT",
            }
        ]
    }


def _devices() -> dict:
    return {
        "water_heater": {
            "present": True,
            "dr_adjustable": True,
            "bath_required_h": 21,
            "model_name": "SECRET_DEVICE_MODEL",
        },
        "ac": {
            "present": True,
            "setpoint_preferred_min_c": 24,
            "setpoint_preferred_max_c": 26,
        },
    }


def test_initial_model_uses_only_observable_answers_and_is_json_serializable() -> None:
    onboarding = _onboarding()
    original = json.dumps(onboarding, ensure_ascii=False, sort_keys=True)

    model = initialize_household_model(
        onboarding,
        household_id="family-7",
        calendar=_calendar(),
        devices=_devices(),
        observed_at=NOW,
    )

    assert model["schema_version"] == HOUSEHOLD_MODEL_VERSION
    assert model["privacy_boundary"]["scope"] == "controller_observable_only"
    assert model["privacy_boundary"]["sanitization"]["discarded_fields"] > 0
    assert model["observed_commitments"]["calendar"][0]["title"] == "Evening shower"
    assert {item["device"] for item in model["observed_commitments"]["devices"]} == {
        "ac",
        "water_heater",
    }
    serialized = json.dumps(model, ensure_ascii=False, sort_keys=True)
    assert "SECRET" not in serialized
    assert "inferred_profile" not in serialized
    assert "preference_rules" not in serialized
    assert "scoring_weight" not in serialized
    assert json.dumps(onboarding, ensure_ascii=False, sort_keys=True) == original


def test_traits_are_distributions_with_confidence_and_provenance() -> None:
    model = initialize_household_model(_onboarding(), household_id="family-7", observed_at=NOW)

    savings = model["traits"]["savings_interest"]
    assert sum(savings["distribution"].values()) == 1.0
    assert savings["distribution"]["high"] > savings["distribution"]["moderate"]
    assert 0 < savings["confidence"] < 1
    assert savings["provenance"][0]["evidence_id"].startswith("ev-")

    thermostat = model["traits"]["thermostat_change_tolerance_c"]
    assert thermostat["distribution"]["family"] == "empirical_normal"
    assert thermostat["distribution"]["mean"] == 1.0
    assert thermostat["distribution"]["plausible_interval"][0] < 1.0
    assert thermostat["distribution"]["plausible_interval"][1] > 1.0

    # No single archetype or caller-authored profile replaces the independent
    # trait distributions.
    assert "archetype" not in model
    assert model["traits"]["grid_support_interest"]["evidence_count"] == 0
    assert any(item["dimension"] == "grid_support_interest" for item in model["unknowns"])


def test_natural_benefit_request_is_inferred_only_from_visible_answer_text() -> None:
    onboarding = {
        "answers": [{
            "id": "decision_information",
            "selected_option_ids": [],
            "answer": "I want to know what benefit I get before deciding.",
        }],
        "inferred_profile": {"explanation_need": "brief"},
        "hidden_persona": {"explanation_need": "brief"},
    }

    model = initialize_household_model(onboarding, household_id="family-7", observed_at=NOW)
    explanation = model["traits"]["explanation_need"]

    assert explanation["distribution"]["concrete"] > explanation["distribution"]["brief"]
    evidence = next(
        item for item in model["evidence_index"]
        if item["evidence_id"] == explanation["provenance"][0]["evidence_id"]
    )
    assert evidence["source"] == "onboarding_answer"
    assert "what benefit" in evidence["fact"]


def test_calendar_and_device_facts_do_not_become_unearned_preferences() -> None:
    model = initialize_household_model(
        {"answers": []},
        household_id="family-7",
        calendar=_calendar(),
        devices=_devices(),
        observed_at=NOW,
    )

    assert all(trait["evidence_count"] == 0 for trait in model["traits"].values())
    assert len(model["unknowns"]) == len(model["traits"])
    sources = {item["source"] for item in model["evidence_index"]}
    assert sources == {"shared_calendar", "device_capability"}


def test_feedback_revision_preserves_disagreement_and_context() -> None:
    initial = initialize_household_model(
        {
            "answers": [{
                "id": "appliance_shift_consent",
                "selected_option_ids": ["automatic_optimization_ok"],
                "answer": "Automatic scheduling is usually fine.",
            }]
        },
        household_id="family-7",
        observed_at=NOW,
    )
    initial_json = json.dumps(initial, ensure_ascii=False, sort_keys=True)

    revised = update_household_model(
        initial,
        event_context={
            "event_type": "peak_event",
            "start_h": 18,
            "occupied": True,
            "affected_devices": ["water_heater"],
            "method_name": "SECRET_METHOD",
        },
        feedback={
            "event_id": "event-1",
            "accepted": False,
            "score": 2,
            "comment": "Please ask me first: moving the shower was disruptive.",
            "base_model": "SECRET_MODEL",
        },
        observed_at="2026-08-28T08:00:00+00:00",
    )

    assert json.dumps(initial, ensure_ascii=False, sort_keys=True) == initial_json
    assert revised["revision"] == 1
    control = revised["traits"]["change_control"]
    assert control["contradiction_count"] >= 1
    assert control["distribution"]["ask_first"] > 0
    assert control["distribution"]["delegated"] > 0
    contextual = revised["contextual_preferences"][0]
    assert contextual["context"]["time_bucket"] == "evening"
    assert contextual["context"]["affected_devices"] == ["water_heater"]
    assert contextual["experience"]["distribution"]["negative"] > 1 / 3
    assert contextual["traits"]["routine_protection"]["evidence_count"] == 1
    assert revised["revision_ledger"][-1]["changed_traits"]
    serialized = json.dumps(revised, ensure_ascii=False)
    assert "SECRET" not in serialized
    assert "method_name" not in serialized
    assert "base_model" not in serialized


def test_update_accepts_v2_event_envelope_and_deduplicates_same_observation() -> None:
    model = initialize_household_model({"answers": []}, household_id="family-7", observed_at=NOW)
    event_context = {
        "version": "energybridge_observable_event_context_v2",
        "event": {"type": "vpp_peak", "trigger_h": 18, "price_level": "high"},
        "features": {
            "event_type": "vpp_peak",
            "time_bucket": "evening",
            "occupancy": "occupied",
            "price_level": "high",
        },
        "persona_config": {"private_fact": "SECRET_PRIVATE"},
    }
    feedback = {
        "event_id": "event-1",
        "comment": "Please explain why this protects my evening routine.",
        "score": 3,
    }

    once = update_household_model(
        model,
        event_context=event_context,
        feedback=feedback,
        observed_at="2026-08-28T08:00:00+00:00",
    )
    twice = update_household_model(
        once,
        event_context=event_context,
        feedback=feedback,
        observed_at="2026-08-28T08:00:00+00:00",
    )

    context = once["contextual_preferences"][0]["context"]
    assert context["event_type"] == "vpp_peak"
    assert context["time_bucket"] == "evening"
    assert context["price_level"] == "high"
    assert twice["traits"] == once["traits"]
    assert twice["contextual_preferences"] == once["contextual_preferences"]
    assert "SECRET" not in json.dumps(twice, ensure_ascii=False)


def test_explicit_feedback_observations_are_evidence_not_overwrites() -> None:
    model = initialize_household_model(
        {"answers": [{
            "id": "thermostat_flexibility",
            "selected_option_ids": ["small_1c_short"],
            "answer": "One degree is fine.",
        }]},
        household_id="family-7",
        observed_at=NOW,
    )

    revised = update_household_model(
        model,
        event_context={"event_type": "peak_event", "start_h": 18},
        feedback={
            "comment": "Today the house was mild.",
            "preference_observations": [
                {"key": "thermostat_flexibility_c", "value": 2.0, "confidence": 0.8},
                {"key": "explanation_need", "value": "concrete", "confidence": 0.9},
            ],
        },
        observed_at="2026-08-28T08:00:00+00:00",
    )

    tolerance = revised["traits"]["thermostat_change_tolerance_c"]
    assert 1.0 < tolerance["distribution"]["mean"] < 2.0
    assert tolerance["contradiction_count"] == 1
    assert len(tolerance["provenance"]) == 2
    assert revised["traits"]["explanation_need"]["distribution"]["concrete"] > 1 / 3


def test_unknowns_drive_natural_active_questions() -> None:
    model = initialize_household_model({"answers": []}, household_id="family-7", observed_at=NOW)

    assert model["unknowns"][0]["priority"] == "high"
    assert 1 <= len(model["active_questions"]) <= 4
    questions = " ".join(item["question"] for item in model["active_questions"])
    assert "automatically" in questions or "routines" in questions
    assert all(item["reason"] == "not_yet_observed" for item in model["active_questions"])


def test_capsule_is_natural_contextual_evidence_cited_and_bounded() -> None:
    model = initialize_household_model(
        _onboarding(),
        household_id="family-7",
        calendar=_calendar(),
        devices=_devices(),
        feedback_history=[{
            "event_id": "event-1",
            "event_context": {
                "event_type": "peak_event",
                "start_h": 18,
                "occupied": True,
                "affected_devices": ["water_heater"],
            },
            "score": 2,
            "comment": "Please explain why and protect the evening shower.",
            "observed_at": "2026-08-26T08:00:00+00:00",
        }],
        observed_at=NOW,
    )

    capsule = build_profile_capsule(
        model,
        context={
            "event_type": "peak_event",
            "start_h": 18.5,
            "occupied": True,
            "affected_devices": ["water_heater"],
        },
        token_budget=220,
    )

    assert capsule["schema_version"] == PROFILE_CAPSULE_VERSION
    assert capsule["estimated_tokens"] == estimate_prompt_tokens(capsule)
    assert capsule["estimated_tokens"] <= 220
    assert capsule["evidence_refs"]
    assert "actually told or shown" in capsule["text"]
    assert "archetype" not in capsule["text"].lower()
    assert "accept" not in capsule["text"].lower()


def test_sanitizer_removes_private_fields_and_credentials_recursively() -> None:
    raw = {
        "visible": "Call 13800138000 or a.person@example.com; token sk-abcdefghijklmnopqrstuv.",
        "upstream_model": "AcmeZeta-X",
        "provider_name": "AcmeCloud",
        "auth_header": "Token arbitraryCredential12345",
        "apiKey": "SECRETVALUE123456",
        "refreshToken": "TOKENVALUE123456",
        "endpointUrl": "internalbox:8443/v1",
        "providerName": "zetacorp",
        "note_sk-ABCDEFGHIJKLMNO": "ordinary",
        "https://key.private.example/v1": "ordinary",
        "description": (
            "provider AcmeCloud model CustomSolver endpoint https://private.example/v1 "
            "planner BazQux algorithm AlphaBeta api key ABCDEF123456XYZ "
            "key ABCDEF123456XYZ; provider zetacorp model fooxyz planner bazqux "
            "controller gizmo42 algorithm alphabeta endpoint internalbox:8443/v1 "
            "endpoint [2001:db8::1]:8443/v1; the key routine is dinner at 18:30"
        ),
        "nested": {
            "persona_config": {"fact": "private"},
            "systemPrompt": "SECRET_SYSTEM",
            "ordinary": "safe",
        },
    }
    clean = sanitize_observable_payload(raw)
    serialized = json.dumps(clean, ensure_ascii=False)

    assert clean["nested"] == {"ordinary": "safe"}
    assert "13800138000" not in serialized
    assert "a.person@example.com" not in serialized
    assert "sk-abcdefghijklmnopqrstuv" not in serialized
    assert "AcmeZeta-X" not in serialized
    assert "AcmeCloud" not in serialized
    assert "arbitraryCredential" not in serialized
    assert "private.example" not in serialized
    assert "CustomSolver" not in serialized
    assert "BazQux" not in serialized
    assert "AlphaBeta" not in serialized
    assert "note_sk-ABCDEFGHIJKLMNO" not in serialized
    assert "key.private.example" not in serialized
    for forbidden in (
        "SECRETVALUE123456", "TOKENVALUE123456", "internalbox", "2001:db8",
        "zetacorp", "fooxyz", "bazqux", "gizmo42", "alphabeta",
    ):
        assert forbidden.lower() not in serialized.lower()
    assert "ABCDEF123456XYZ" not in serialized
    assert "the key routine is dinner at 18:30" in serialized
    assert serialized.count("[redacted]") >= 3


def test_sanitizer_preserves_json_pointers_but_redacts_arbitrary_dns() -> None:
    clean = sanitize_observable_payload({
        "note": (
            "Keep /memory/feedback/id, /profile/capabilities/ev, and "
            "/device_capabilities/washer/earliest_h; redact private.service.academy, "
            "memory.private.academy, profile.secret.com, and endpoint "
            "tenant.backend.solutions."
        ),
    })
    rendered = json.dumps(clean, ensure_ascii=False)

    for provenance in (
        "/memory/feedback/id",
        "/profile/capabilities/ev",
        "/device_capabilities/washer/earliest_h",
    ):
        assert provenance in rendered
    for endpoint in (
        "private.service.academy",
        "memory.private.academy",
        "profile.secret.com",
        "tenant.backend.solutions",
    ):
        assert endpoint not in rendered
    assert rendered.count("[redacted]") >= 4


def test_profile_removes_planner_identity_from_text_but_keeps_household_fact() -> None:
    model = initialize_household_model(
        {
            "answers": [
                {
                    "id": "refresh_token",
                    "answer": "Token abcdefghijklmnopqrstuvwxyz",
                },
                {
                    "id": "upstream_model",
                    "answer": "AcmeZeta-X",
                },
                {
                    "id": "protected_routine",
                    "question": "What should be protected?",
                    "answer": "EnergyBridge, HEMA, and OpenAI GPT-9 aside, protect dinner and the 21:00 shower.",
                }
            ]
        },
        household_id="family-identity",
        observed_at=NOW,
    )

    serialized = json.dumps(model, ensure_ascii=False, sort_keys=True).lower()
    assert re.search(r"\bhema\b", serialized) is None
    assert re.search(r"\bopenai\b", serialized) is None
    assert re.search(r"\bgpt-9\b", serialized) is None
    assert "EnergyBridge" not in json.dumps(model, ensure_ascii=False, sort_keys=True)
    assert "protect dinner" in serialized
    assert "21:00 shower" in serialized
    assert "refresh_token" not in serialized
    assert "abcdefghijklmnopqrstuvwxyz" not in serialized
    assert "upstream_model" not in serialized
    assert "acmezeta" not in serialized


def test_direct_profile_unicode_household_ids_do_not_collapse() -> None:
    first = initialize_household_model(
        {"answers": []}, household_id="家庭甲", observed_at=NOW
    )
    second = initialize_household_model(
        {"answers": []}, household_id="家庭乙", observed_at=NOW
    )

    assert first["household_id"] != second["household_id"]
    assert first["household_id"].startswith("household-")
    assert second["household_id"].startswith("household-")
