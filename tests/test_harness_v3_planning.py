from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

from energybridge.harness.planning import (
    PLANNING_SCHEMA_VERSION,
    analyze_pareto,
    build_decision_episode_record,
    build_planning_prompts,
    derive_planning_constraints,
    evaluate_planning_response,
    normalize_information_requests,
    parse_planning_response,
    validate_plan_candidate,
)
from energybridge.harness.decision_evidence_v3 import DECISION_EVIDENCE_LEDGER_VERSION


def _inputs() -> dict:
    return {
        "observable_state": {
            "indoor_temp_c": 25.4,
            "control_limits": {"setpoint": {"min": 22.0, "max": 29.0}},
            "method": "energybridge",
        },
        "observable_profile": {
            "beliefs": {
                "comfort_priority": {
                    "value": "high",
                    "confidence": 0.72,
                    "provenance": [
                        {"source": "onboarding", "evidence_id": "answer-2"},
                    ],
                }
            },
            "hidden_persona": {"acceptance_probability": 0.91},
        },
        "memory": {
            "owner": {"method": "agent", "model": "gpt-9"},
            "events": [
                {
                    "event_id": "past-1",
                    "feedback": "Too warm last time",
                    "provenance": {"source": "user_feedback"},
                }
            ],
        },
        "event": {"id": "event-1", "trigger_h": 18.0, "end_h": 19.0},
    }


def test_prompt_is_method_blind_but_keeps_observable_evidence_provenance() -> None:
    inputs = _inputs()
    system, user = build_planning_prompts(
        **inputs,
        advisor_candidates=[
            {
                "method": "MPC",
                "model": "secret-controller-v4",
                "source": "mpc_dynamic",
                "plan": {"setpoint": 28.0, "appliance_actions": {"washer_start_h": 20.0}},
                "reason": "MPC predicts a cheaper schedule",
            }
        ],
    )

    lowered = user.lower()
    assert "mpc" not in lowered
    assert "gpt-9" not in lowered
    assert "secret-controller" not in lowered
    assert "acceptance_probability" not in lowered
    assert "hidden_persona" not in lowered
    assert '"source": "onboarding"' in user
    assert '"source": "user_feedback"' in user
    assert '"advisor_ref": "advisor_01"' in user
    assert '"setpoint": 28.0' in user
    assert "choose how many are useful" in system
    assert "canned strategy grid" in system
    assert "acceptance probability" in system


def test_prompt_exposes_epistemic_precedence_without_prescribing_an_action() -> None:
    inputs = _inputs()
    inputs["event"]["current_user_message"] = (
        "You may shift laundry if it finishes today; tell me the supported savings."
    )
    system, user = build_planning_prompts(**inputs)
    payload = json.loads(user.split("[PLANNING PAYLOAD]\n", 1)[1])
    ledger = payload["decision_evidence_ledger"]

    assert ledger["schema_version"] == DECISION_EVIDENCE_LEDGER_VERSION
    assert ledger["entries"][0]["evidence_path"] == "/event/current_user_message"
    assert ledger["selection_performed"] is False
    assert ledger["action_recommendation"] is None
    assert "acceptance_probability" not in ledger
    assert "current statement governs the same topic" in system
    assert "washer_start_h" not in json.dumps(ledger)


def test_selected_candidate_explanation_survives_portfolio_boundary() -> None:
    explanation = {
        "natural_language": "Move the washer earlier while preserving today's deadline.",
        "expected_benefit": "The supplied evidence supports a lower normalized tariff cost.",
        "protected_constraints": "The cycle still finishes today.",
    }
    result = evaluate_planning_response(
        {
            "candidate_plans": [
                {
                    "candidate_id": "earlier_washer",
                    "plan": {
                        "setpoint": 26.0,
                        "appliances": {"washer_start_h": 8.0},
                    },
                    "strategy_explanation": explanation,
                }
            ],
            "selected_candidate_id": "earlier_washer",
        },
        observable_state={},
        observable_profile={},
        memory={},
        event={},
    )

    assert result["selection_status"] == "selected"
    assert result["selected_executable_plan"]["strategy_explanation"] == explanation
    lifecycle = result["portfolio_audit"]["candidate_lifecycles"][0]
    assert lifecycle["strategy_explanation"] == explanation
    assert "strategy_explanation" not in lifecycle["raw_snapshot"]


def test_one_candidate_without_selected_id_is_unambiguous_model_selection() -> None:
    result = evaluate_planning_response(
        {
            "candidate_plans": [
                {
                    "candidate_id": "only_model_candidate",
                    "plan": {"setpoint": 26.0, "appliances": {}},
                }
            ]
        },
        observable_state={},
        observable_profile={},
        memory={},
        event={},
    )

    assert result["selection_status"] == "selected"
    assert result["selected_candidate_id"] == "only_model_candidate"
    assert result["selected_executable_plan"]["setpoint"] == 26.0
    assert (
        result["portfolio_audit"]["selection_inference"]
        == "single_candidate_unambiguous"
    )


def test_prompt_recursively_redacts_private_fields_credentials_and_identity_text() -> None:
    secret_key = "sk-thisMustNeverReachThePlanningPrompt12345"
    system, user = build_planning_prompts(
        observable_state={
            "api_key": secret_key,
            "developer_prompt": "SECRET_DEVELOPER_INSTRUCTION",
            "upstream_model": "AcmeZeta-X",
            "provider_name": "AcmeCloud",
            "planner_name": "SecretPlanner",
            "source_method": "CustomSolver",
            "auth_header": "Token arbitraryCredentialValue12345",
            "developer_message": "DO_SECRET_DEVELOPER_MESSAGE",
            "private_key": "-----BEGIN PRIVATE KEY----- ABCSUPERSECRETXYZ -----END PRIVATE KEY-----",
            "api_base": "https://AcmeCloud.invalid/v1",
            "endpoint_url": "https://SecretProvider.invalid/api",
            "llm_host": "ModelVendor.invalid",
            "note_sk-ABCDEFGHIJKLMNO": "ordinary",
            "https://key.private.example/v1": "ordinary",
            "path_value": Path("/tmp/sk-PATHCREDENTIAL123456"),
            "hard_constraints": [
                {
                    "constraint_id": "private_constraint",
                    "kind": "range",
                    "path": "/setpoint",
                    "min": 22,
                    "max": 29,
                    "method": "HEMA",
                    "source_method": "MPC",
                    "reason": "OpenAI GPT-9 suggested it",
                    "provider": "DeepSeek",
                }
            ],
            "nested": {
                "authorization": "Bearer thisMustNeverReachThePrompt12345",
                "note": "The MPC plan came from OpenAI GPT-9.",
            },
        },
        observable_profile={
            "evaluator_state": {"target_score": 5},
            "fact": "Dinner is protected.",
        },
        memory={
            "credentials": {"access_token": "tokenMustNeverReachThePrompt12345"},
            "feedback": "HEMA was too disruptive; protect the shower deadline.",
        },
        event={
            "secret": "SECRET_EVENT_VALUE",
            "id": "event-privacy",
        },
        advisor_candidates=[
            {
                "provider": "DeepSeek",
                "api_key": secret_key,
                "plan": {"setpoint": 26.0},
                "reason": "PPO from Qwen chose this plan",
            }
        ],
        explicit_constraints=[
            {
                "constraint_id": "explicit_private_constraint",
                "kind": "required",
                "path": "/appliances",
                "authorization": "Bearer anotherSecretToken12345",
                "reason": "Qwen PPO generated this constraint",
            }
        ],
    )

    rendered = f"{system}\n{user}"
    lowered = rendered.lower()
    for forbidden in (
        secret_key,
        "secret_developer_instruction",
        "thismustneverreachtheprompt",
        "secret_event_value",
        "target_score",
        "acmezeta-x",
        "acmecloud",
        "secretplanner",
        "customsolver",
        "arbitrarycredentialvalue",
        "do_secret_developer_message",
        "abcsupersecretxyz",
        "acmecloud.invalid",
        "secretprovider.invalid",
        "modelvendor.invalid",
        "note_sk-abcdefghijklmnop",
        "key.private.example",
        "sk-pathcredential123456",
    ):
        assert forbidden.lower() not in lowered
    for identity in ("mpc", "openai", "gpt-9", "hema", "deepseek", "ppo", "qwen"):
        assert re.search(rf"\b{re.escape(identity)}\b", lowered) is None
    assert "Dinner is protected." in rendered
    assert "protect the shower deadline" in rendered
    assert "[redacted credential]" not in rendered  # private keys are dropped entirely


def test_reviewer_regression_redacts_unknown_identities_endpoints_and_bare_keys_everywhere() -> None:
    """Free text must not bypass the structured method/privacy boundary."""
    system, user = build_planning_prompts(
        observable_state={
            "note": (
                "provider NebulaForge, model ZX-Private-9, planner quartz, "
                "algorithm pebble; api key StateSecret987; "
                "see https://state.private.invalid/v1"
            )
        },
        observable_profile={
            "household_note": (
                "The utility portal says dinner at 18:00 remains protected. "
                "Provider: ProfileWorks and key PROFILE7"
            )
        },
        memory={
            "feedback": (
                "Produced by MemoryPilot; retry wss://memory.private.invalid/socket "
                "or memory-host:9443/v1"
            )
        },
        event={
            "notice": "Planner Atlas, endpoint 10.20.30.40:9000/v1, key EVENT99"
        },
        advisor_candidates=[
            {
                "plan": {"setpoint": 26.0},
                "reason": "solver Rhea; api key AdvisorSecret8; advisor.private.invalid/v1",
            }
        ],
        explicit_constraints=[
            {
                "constraint_id": "service_window",
                "kind": "required",
                "path": "/appliances",
                "note": "model is ConstraintBrain; key CONSTRAINT8; [2001:db8::1]:8443/v1",
            }
        ],
    )

    rendered = f"{system}\n{user}"
    lowered = rendered.lower()
    for forbidden in (
        "nebulaforge",
        "zx-private-9",
        "planner quartz",
        "algorithm pebble",
        "statesecret987",
        "state.private.invalid",
        "profileworks",
        "profile7",
        "memorypilot",
        "memory.private.invalid",
        "memory-host:9443",
        "planner atlas",
        "10.20.30.40",
        "event99",
        "solver rhea",
        "advisorsecret8",
        "advisor.private.invalid",
        "constraintbrain",
        "constraint8",
        "2001:db8::1",
    ):
        assert forbidden not in lowered
    assert "The utility portal says dinner at 18:00 remains protected." in rendered
    assert "[private endpoint]" in rendered
    assert "[sensitive value removed]" in rendered


def test_planning_text_sanitizer_preserves_clock_time_ranges() -> None:
    system_prompt, user_prompt = build_planning_prompts(
        observable_state={
            "note": (
                "Shift flexible loads after 15:00-18:00 and keep the 18:00-19:00 event clear. "
                "Use /device_capabilities/washer/earliest_h, /memory/feedback/id, and "
                "/profile/capabilities/ev as unambiguous provenance. Never expose "
                "private.service.academy, memory.private.academy, profile.secret.com, "
                "or endpoint tenant.backend.solutions."
            ),
        },
        observable_profile={},
        memory={},
        event={"trigger_h": 18.0, "end_h": 19.0},
    )

    rendered = system_prompt + user_prompt
    assert "15:00-18:00" in rendered
    assert "18:00-19:00" in rendered
    assert "/device_capabilities/washer/earliest_h" in rendered
    assert "/memory/feedback/id" in rendered
    assert "/profile/capabilities/ev" in rendered
    for endpoint in (
        "private.service.academy",
        "memory.private.academy",
        "profile.secret.com",
        "tenant.backend.solutions",
    ):
        assert endpoint not in rendered
    assert rendered.count("[private endpoint]") >= 4


def test_direct_candidate_validator_sanitizes_public_labels() -> None:
    lifecycle = validate_plan_candidate(
        {"plan": {"setpoint": 25.0}},
        candidate_id="sk-CANDIDATESECRET123456",
        origin="provider zetacorp",
    )

    rendered = json.dumps(lifecycle, sort_keys=True).lower()
    assert "candidateSecret".lower() not in rendered
    assert "zetacorp" not in rendered
    assert lifecycle["feasible"] is True
    assert lifecycle["candidate_id"]
    assert lifecycle["origin"]


def test_parse_portfolio_preserves_open_candidate_metadata_and_selection() -> None:
    raw = {
        "candidate_plans": [
            {
                "candidate_id": "gentle",
                "plan": {"setpoint": 26.0, "appliances": {"washer_start_h": 20.0}},
                "objective_estimates": {
                    "comfort": {"value": 0.9, "direction": "max", "confidence": 0.7}
                },
                "uncertainty": [{"factor": "arrival time", "effect": "may reduce flexibility"}],
                "counterfactuals": [{"if": "home remains empty", "prefer": "deeper response"}],
            },
            {
                "candidate_id": "deeper",
                "plan": {"setpoint": 28.0, "commands": [{"device": "washer", "start_h": 21.0}]},
            },
        ],
        "selected_candidate_id": "gentle",
        "selection_reason": "Better supported by the recent comfort complaint.",
    }

    parsed = parse_planning_response(json.dumps(raw))

    assert parsed["selected_candidate_id"] == "gentle"
    assert len(parsed["candidate_plans"]) == 2
    gentle = parsed["candidate_plans"][0]
    assert gentle["plan"]["setpoint"] == 26.0
    assert gentle["uncertainty"][0]["factor"] == "arrival time"
    assert gentle["counterfactuals"][0]["prefer"] == "deeper response"
    assert parsed["parse_errors"] == []
    assert parsed["legacy_single_plan"] is False


def test_decision_episode_preserves_model_alternatives_without_ranking_or_advisor() -> None:
    inputs = _inputs()
    raw = {
        "candidate_plans": [
            {
                "candidate_id": "gentle",
                "plan": {"setpoint": 26.0, "appliances": {"washer_start_h": 20.0}},
                "objective_estimates": {
                    "comfort": {"value": "small change", "confidence": 0.7}
                },
                "uncertainty": [{"factor": "arrival time", "effect": "may change fit"}],
                "counterfactuals": [{"if": "home remains empty", "prefer": "deeper"}],
                "evidence_citations": ["/observable_profile/comfort"],
            },
            {
                "candidate_id": "deeper",
                "plan": {"setpoint": 27.0, "appliances": {"washer_start_h": 21.0}},
                "objective_estimates": {
                    "cost": {"value": -0.8, "unit": "normalized", "confidence": 0.6}
                },
            },
        ],
        "selected_candidate_id": "gentle",
        "selection_reason": "The current comfort evidence is stronger than the uncertain saving.",
        "information_requests": [
            {
                "question": "Will anyone return before 20:00?",
                "decision_relevance": "It changes whether the deeper plan protects comfort.",
                "evidence_gap_citations": ["/observable_profile/active_questions/0"],
            }
        ],
    }
    resolution = evaluate_planning_response(
        raw,
        **inputs,
        advisor_candidates=[{"plan": {"setpoint": 24.0}, "objectives": {"cost": 99}}],
    )

    record = build_decision_episode_record(resolution)

    assert record["selected_candidate_id"] == "gentle"
    assert record["candidate_count"] == 2
    assert [item["candidate_id"] for item in record["candidates"]] == [
        "gentle",
        "deeper",
    ]
    assert record["candidates"][0]["chosen_in_response"] is True
    assert record["candidates"][1]["validated_plan"]["setpoint"] == 27.0
    assert record["automatic_ranking_applied"] is False
    assert "advisor_01" not in json.dumps(record)


def test_parse_legacy_single_plan_is_backwards_compatible() -> None:
    parsed = parse_planning_response(
        {"setpoint": 26.5, "appliances": {"dishwasher_start_h": 21.0}, "reason": "quiet hours"}
    )

    assert parsed["legacy_single_plan"] is True
    assert parsed["selected_candidate_id"] == "model_candidate_01"
    assert parsed["candidate_plans"][0]["plan"]["reason"] == "quiet hours"


def test_model_response_output_boundary_removes_identity_and_private_metadata() -> None:
    raw = {
        "candidate_plans": [
            {
                "candidate_id": "candidate-safe",
                "plan": {
                    "setpoint": 25.0,
                    "appliances": {"washer_start_h": 20.0},
                    "method": "MPC",
                    "provider_name": "AcmeCloud",
                    "api_base": "https://secret-provider.invalid/v1",
                    "reason": "HEMA via OpenAI GPT-9 proposed this household plan",
                    "developer_message": "DO_NOT_PERSIST_THIS",
                },
                "provider": "AnotherVendor",
            }
        ],
        "selected_candidate_id": "candidate-safe",
        "provider_name": "TopLevelProvider",
    }

    result = evaluate_planning_response(
        raw,
        observable_state={"control_limits": {"setpoint": {"min": 22, "max": 28}}},
        observable_profile={},
        memory={},
        event={},
    )

    rendered = json.dumps(result, sort_keys=True)
    lowered = rendered.lower()
    assert result["selection_status"] == "selected"
    assert result["selected_executable_plan"]["setpoint"] == 25.0
    for forbidden in ("mpc", "hema", "openai", "gpt-9"):
        assert re.search(rf"\b{re.escape(forbidden)}\b", lowered) is None
    for forbidden in (
        "acmecloud",
        "secret-provider",
        "anotherVendor",
        "topLevelProvider",
        "do_not_persist_this",
    ):
        assert forbidden.lower() not in lowered


def test_raw_response_and_selection_reason_share_the_content_privacy_boundary() -> None:
    raw = {
        "candidate_plans": [
            {
                "candidate_id": "candidate-private",
                "plan": {
                    "setpoint": 25.5,
                    "reason": (
                        "provider OutputForge used api key PlanSecret77 at "
                        "https://plan.private.invalid/v1; utility portal confirms the event"
                    ),
                },
                "uncertainty": "planner NightOwl queried 172.16.2.9:8080/status",
            }
        ],
        "selected_candidate_id": "candidate-private",
        "selection_reason": (
            "model Raven-12 preferred it; key OUTPUT99; "
            "wss://audit.private.invalid/socket; utility portal notice is current"
        ),
        "diagnostic_note": "algorithm Helix and api key RawSnapshotSecret5",
    }

    result = evaluate_planning_response(
        raw,
        observable_state={},
        observable_profile={},
        memory={},
        event={},
    )

    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
    lowered = rendered.lower()
    for forbidden in (
        "outputforge",
        "plansecret77",
        "plan.private.invalid",
        "nightowl",
        "172.16.2.9",
        "raven-12",
        "output99",
        "audit.private.invalid",
        "algorithm helix",
        "rawsnapshotsecret5",
    ):
        assert forbidden not in lowered
    selection = result["portfolio_audit"]["model_selection"]["selection_reason"]
    snapshot = result["portfolio_audit"]["raw_response_snapshot"]
    assert "utility portal notice is current" in selection
    assert "utility portal confirms the event" in json.dumps(snapshot, ensure_ascii=False)
    assert "[private endpoint]" in rendered
    assert "[sensitive value removed]" in rendered


def test_validator_records_raw_to_validated_json_patches_without_mutation() -> None:
    candidate = {
        "candidate_id": "candidate-a",
        "plan": {
            "setpoint_c": 31.0,
            "appliance_actions": {"washer_start_h": 20.0},
        },
    }
    original = deepcopy(candidate)

    lifecycle = validate_plan_candidate(
        candidate,
        observable_state={"control_limits": {"setpoint": {"min": 22.0, "max": 29.0}}},
    )

    assert candidate == original
    assert lifecycle["raw_snapshot"] == candidate["plan"]
    assert lifecycle["validated_snapshot"] == {
        "setpoint": 29.0,
        "appliances": {"washer_start_h": 20.0},
    }
    assert lifecycle["status"] == "repaired"
    assert lifecycle["feasible"] is True
    operations = [(item["op"], item["path"]) for item in lifecycle["json_patches"]]
    assert ("move", "/setpoint") in operations
    assert ("move", "/appliances") in operations
    assert ("replace", "/setpoint") in operations
    assert all(item["provenance"]["rule_id"] for item in lifecycle["json_patches"])
    assert any(item["repaired"] for item in lifecycle["violations"])


def test_explicit_half_open_event_constraint_rejects_overlap() -> None:
    constraint = {
        "constraint_id": "washer_not_in_event",
        "kind": "disjoint_interval",
        "start_path": "/appliances/washer_start_h",
        "end_path": "/appliances/washer_end_h",
        "forbidden_window": [18.0, 19.0],
        "severity": "hard",
    }
    overlapping = validate_plan_candidate(
        {"setpoint": 27.0, "appliances": {"washer_start_h": 18.5, "washer_end_h": 19.5}},
        explicit_constraints=[constraint],
    )
    adjacent = validate_plan_candidate(
        {"setpoint": 27.0, "appliances": {"washer_start_h": 19.0, "washer_end_h": 20.0}},
        explicit_constraints=[constraint],
    )

    assert overlapping["feasible"] is False
    assert overlapping["violations"][0]["message"] == "half-open intervals overlap"
    assert adjacent["feasible"] is True


def test_duration_derived_interval_rejects_hidden_cycle_overlap() -> None:
    constraint = {
        "constraint_id": "washer_outside_event",
        "kind": "disjoint_interval_duration",
        "path": "/appliances/washer_start_h",
        "duration_h": 2.0,
        "forbidden_window": [18.0, 19.0],
    }

    overlaps = validate_plan_candidate(
        {"setpoint": 26.0, "appliances": {"washer_start_h": 17.0}},
        explicit_constraints=[constraint],
    )
    boundary_safe = validate_plan_candidate(
        {"setpoint": 26.0, "appliances": {"washer_start_h": 19.0}},
        explicit_constraints=[constraint],
    )

    assert overlaps["feasible"] is False
    assert overlaps["violations"][0]["message"] == (
        "duration-derived half-open interval overlaps forbidden window"
    )
    assert boundary_safe["feasible"] is True


def test_nullable_future_check_constraint_accepts_none_but_rejects_past() -> None:
    constraint = {
        "constraint_id": "future_check",
        "kind": "range",
        "path": "/next_check_hour",
        "min": 42.166667,
        "nullable": True,
    }

    no_check = validate_plan_candidate(
        {"setpoint": 26.0, "appliances": {}, "next_check_hour": None},
        explicit_constraints=[constraint],
    )
    stale_check = validate_plan_candidate(
        {"setpoint": 26.0, "appliances": {}, "next_check_hour": 19.0},
        explicit_constraints=[constraint],
    )

    assert no_check["feasible"] is True
    assert stale_check["feasible"] is False


def test_dotted_constraint_paths_are_canonicalized_before_text_redaction() -> None:
    constraints = derive_planning_constraints(
        observable_state={
            "hard_constraints": [
                {
                    "constraint_id": "washer_window",
                    "kind": "disjoint_interval",
                    "start_path": "actions.washer.start_h",
                    "end_path": "appliances.washer_end_h",
                    "forbidden_window": [18.0, 19.0],
                    "evidence_paths": ["event.window.start_h", "/event/window/end_h"],
                },
                {
                    "constraint_id": "ev_mode",
                    "kind": "enum",
                    "path": "appliances.ev_mode",
                    "allowed": ["normal", "smart", "delay"],
                },
            ],
        },
    )

    by_id = {item["constraint_id"]: item for item in constraints}
    washer = by_id["washer_window"]
    assert washer["start_path"] == "/actions/washer/start_h"
    assert washer["end_path"] == "/appliances/washer_end_h"
    assert washer["evidence_paths"][:2] == [
        "/event/window/start_h",
        "/event/window/end_h",
    ]
    assert by_id["ev_mode"]["path"] == "/appliances/ev_mode"


def test_constraint_paths_reject_private_or_identity_segments() -> None:
    constraints = derive_planning_constraints(
        explicit_constraints=[
            {"constraint_id": "private", "kind": "equals", "path": "api_key.value"},
            {
                "constraint_id": "private_segment",
                "kind": "required",
                "path": "memory.private.academy",
                "evidence_paths": ["private.service.academy"],
            },
            {"constraint_id": "identity", "kind": "equals", "path": "controller.value"},
        ],
    )

    assert all("path" not in item for item in constraints)
    assert all("/private/" not in json.dumps(item) for item in constraints)


def test_constraint_paths_share_the_executable_alias_namespace() -> None:
    lifecycle = validate_plan_candidate(
        {
            "setpoint_c": 26.0,
            "appliance_actions": {"washer_start_h": 20.0},
        },
        explicit_constraints=[
            {"constraint_id": "setpoint_present", "kind": "required", "path": "setpoint_c"},
            {
                "constraint_id": "washer_present",
                "kind": "required",
                "path": "appliance_actions.washer_start_h",
            },
        ],
    )

    assert lifecycle["feasible"] is True
    assert lifecycle["validated_snapshot"] == {
        "setpoint": 26.0,
        "appliances": {"washer_start_h": 20.0},
    }
    assert not lifecycle["violations"]


def test_conflicting_executable_aliases_are_not_silently_resolved() -> None:
    lifecycle = validate_plan_candidate(
        {
            "setpoint": 26.0,
            "appliances": {"washer_start_h": 20.0},
            "appliance_actions": {"washer_start_h": 21.0},
        }
    )

    assert lifecycle["feasible"] is False
    assert lifecycle["status"] == "invalid"
    assert any("ambiguous" in item["message"] for item in lifecycle["violations"])


def test_empty_legacy_action_envelope_is_parsed_then_audited_as_invalid() -> None:
    result = evaluate_planning_response(
        {"setpoint": None, "appliances": {}, "reason": "not yet planned"},
        observable_state={},
        observable_profile={},
        memory={},
        event={},
    )

    assert result["portfolio_audit"]["legacy_single_plan"] is True
    assert len(result["portfolio_audit"]["candidate_lifecycles"]) == 1
    assert result["selection_status"] == "replan_required"
    assert result["selected_executable_plan"] is None


def test_unsupported_action_envelopes_require_semantic_replan() -> None:
    cases = (
        {"actions": {"washer_start_h": 20.0}},
        {"commands": [{"device": "washer", "start_h": 20.0}]},
        {
            "setpoint": 26.0,
            "appliances": {"washer_start_h": 20.0},
            "commands": [{"device": "dishwasher", "start_h": 21.0}],
        },
    )

    for plan in cases:
        lifecycle = validate_plan_candidate(plan)
        assert lifecycle["feasible"] is False
        assert lifecycle["status"] == "invalid"
        assert any(
            item["constraint_id"] == "unsupported_executable_envelope"
            for item in lifecycle["violations"]
        )

    canonical = validate_plan_candidate({
        "setpoint": 26.0,
        "appliances": {"washer_start_h": 20.0},
    })
    assert canonical["feasible"] is True


def test_information_requests_are_optional_model_owned_and_audited_without_ranking() -> None:
    response = {
        "candidate_plans": [{
            "candidate_id": "reversible",
            "plan": {"setpoint": 26.0, "appliances": {}},
        }],
        "selected_candidate_id": "reversible",
        "selection_reason": "Safe while the routine detail is unresolved.",
        "information_requests": [{
            "question_id": "routine_confirmation",
            "question": "Would shifting the washer past 19:00 disrupt tonight's routine?",
            "why_it_matters": "A yes would keep the ordinary start; a no permits the later start.",
            "evidence_gap": "/observable_profile/decision_unknowns/0",
        }],
    }
    profile = {
        "decision_unknowns": [{
            "question_id": "routine_confirmation",
            "dimension": "routine_protection",
            "question": "Which routines should not move automatically?",
            "reason": "limited_evidence",
        }]
    }

    result = evaluate_planning_response(
        response,
        observable_state={},
        observable_profile=profile,
        memory={},
        event={},
    )
    audit = result["portfolio_audit"]["information_acquisition"]

    assert result["selection_status"] == "selected"
    assert audit["requested_count"] == 1
    assert audit["requests"][0]["grounded_in_supplied_unknown"] is True
    assert audit["requests"][0]["decision_relevance_stated"] is True
    assert audit["questions_ranked_or_scored_by_harness"] is False
    assert audit["plan_selection_changed_by_harness"] is False


def test_information_request_normalizer_accepts_natural_shapes_and_cleans_private_text() -> None:
    requests = normalize_information_requests({
        "clarification_requests": [
            "Is the shower time fixed tonight?",
            {
                "ask": "Use key sk-ABCDEFGHIJKLMNOPQRSTUV at endpoint private.service.xyz?",
                "impact": "It could change hot-water timing.",
                "evidence_citations": ["/observable_profile/decision_unknowns/1", "not-a-pointer"],
            },
        ]
    })
    rendered = json.dumps(requests, ensure_ascii=False).lower()

    assert len(requests) == 2
    assert requests[0]["question"] == "Is the shower time fixed tonight?"
    assert requests[1]["evidence_gap_citations"] == [
        "/observable_profile/decision_unknowns/1"
    ]
    assert "abcdefghijkl" not in rendered
    assert "private.service.xyz" not in rendered


def test_preferences_are_not_silently_promoted_to_hard_constraints() -> None:
    constraints = derive_planning_constraints(
        observable_state={"control_limits": {"setpoint_c": {"min": 20, "max": 30}}},
        observable_profile={
            "preferred_setpoint_max_c": 25.0,
            "beliefs": {"confirmation_required": {"value": True, "confidence": 0.61}},
        },
        event={"trigger_h": 18.0, "end_h": 19.0},
    )

    assert [item["constraint_id"] for item in constraints] == ["physical_setpoint_range"]
    assert constraints[0]["max"] == 30


def _lifecycle(candidate_id: str, energy: object, comfort: object) -> dict:
    return {
        "candidate_id": candidate_id,
        "feasible": True,
        "objective_estimates": {"energy": energy, "comfort": comfort},
    }


def test_pareto_comparison_has_no_method_weights_and_audits_uncertainty() -> None:
    candidates = [
        _lifecycle(
            "balanced",
            {"value": 4.0, "lower": 3.0, "upper": 5.0, "direction": "min"},
            {"value": 0.8, "lower": 0.7, "upper": 0.9, "direction": "max"},
        ),
        _lifecycle(
            "worse-point-estimate",
            {"value": 5.0, "lower": 4.1, "upper": 6.0, "direction": "min"},
            {"value": 0.7, "lower": 0.65, "upper": 0.75, "direction": "max"},
        ),
        _lifecycle(
            "comfort-specialist",
            {"value": 6.0, "direction": "min"},
            {"value": 0.95, "direction": "max"},
        ),
    ]

    audit = analyze_pareto(candidates)

    assert audit["weights_used"] is False
    assert audit["comparison"] == "pareto_no_scalarization"
    assert set(audit["point_frontier"]) == {"balanced", "comfort-specialist"}
    assert "worse-point-estimate" not in audit["point_frontier"]
    # Its uncertainty overlaps balanced, so point dominance is not claimed as
    # robust dominance.
    assert "worse-point-estimate" in audit["robust_frontier"]


def test_model_selection_is_preserved_even_when_advisor_point_dominates() -> None:
    inputs = _inputs()
    response = {
        "candidate_plans": [
            {
                "candidate_id": "personalized",
                "plan": {"setpoint": 26.0},
                "objective_estimates": {
                    "energy": {"value": 5.0, "direction": "min"},
                    "comfort": {"value": 0.8, "direction": "max"},
                },
            }
        ],
        "selected_candidate_id": "personalized",
    }
    advisor = {
        "method": "MPC",
        "plan": {"setpoint": 27.0},
        "objective_estimates": {
            "energy": {"value": 4.0, "direction": "min"},
            "comfort": {"value": 0.9, "direction": "max"},
        },
    }

    result = evaluate_planning_response(response, **inputs, advisor_candidates=[advisor])

    assert result["selection_status"] == "selected"
    assert result["selected_candidate_id"] == "personalized"
    assert result["selected_executable_plan"]["setpoint"] == 26.0
    selection = result["portfolio_audit"]["model_selection"]
    assert selection["advisor_override_allowed"] is False
    dominance = result["portfolio_audit"]["pareto"]["point_dominance"]
    assert {"dominant": "advisor_01", "dominated": "personalized"} in dominance


def test_invalid_model_selection_requires_replanning_instead_of_advisor_fallback() -> None:
    inputs = _inputs()
    response = {
        "candidate_plans": [
            {"candidate_id": "invalid", "plan": {"reason": "No executable command"}}
        ],
        "selected_candidate_id": "invalid",
    }
    result = evaluate_planning_response(
        response,
        **inputs,
        advisor_candidates=[{"method": "MPC", "plan": {"setpoint": 27.0}}],
    )

    assert result["selection_status"] == "replan_required"
    assert result["selected_executable_plan"] is None
    assert result["selected_candidate_id"] is None
    assert result["portfolio_audit"]["candidate_lifecycles"][1]["feasible"] is True


def test_evaluator_is_deterministic_and_json_serializable() -> None:
    inputs = _inputs()
    response = {
        "candidate_plans": [
            {
                "candidate_id": "a",
                "plan": {"setpoint": 26.0},
                "uncertainty": "weather forecast may change",
                "counterfactuals": ["If occupied, restore 25 C"],
            }
        ],
        "selected_candidate_id": "a",
    }
    original_inputs = deepcopy(inputs)

    first = evaluate_planning_response(response, **inputs)
    second = evaluate_planning_response(response, **inputs)

    assert first == second
    assert inputs == original_inputs
    assert first["schema_version"] == PLANNING_SCHEMA_VERSION
    json.dumps(first, ensure_ascii=False, allow_nan=False)
    lifecycle = first["portfolio_audit"]["candidate_lifecycles"][0]
    assert lifecycle["uncertainty"] == "weather forecast may change"
    assert lifecycle["counterfactuals"] == ["If occupied, restore 25 C"]
