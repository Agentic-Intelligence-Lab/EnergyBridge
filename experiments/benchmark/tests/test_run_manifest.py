from __future__ import annotations

import json

from energybridge.benchmark.run_manifest import (
    SCHEMA_VERSION,
    build_run_manifest,
    manifest_fingerprint,
    result_matches_manifest,
)
from energybridge.harness.energy_tools_v3 import (
    ENERGY_IMPACT_SCHEMA_VERSION,
    FLEXIBLE_LOAD_OPPORTUNITY_VERSION,
)
from energybridge.harness.decision_evidence_v3 import DECISION_EVIDENCE_LEDGER_VERSION
from energybridge.harness.calibration_v3 import OUTCOME_CALIBRATION_VERSION
from energybridge.harness.memory_v3 import MEMORY_V3_VERSION
from energybridge.harness.planning import PLANNING_SCHEMA_VERSION
from energybridge.harness.profile_v3 import HOUSEHOLD_MODEL_VERSION
from energybridge.llm.client import STRUCTURED_OUTPUT_TRANSPORT_VERSION
from experiments.benchmark.run_baseline_matrix import (
    Job,
    _command_for,
    _manifest_for_job,
    _result_success,
)
from experiments.benchmark.run_household_matrix import (
    HouseholdJob,
    _command_for as _household_command_for,
    _manifest_for_job as _household_manifest_for_job,
)
from experiments.benchmark.run_persona_json import DEFAULT_CAPACITY_MEMORY_JSON


PERSONA_ID = "basic_role_a_commuter_price_cooperative"
HOUSEHOLD_ID = "household_s1_dual_commuter_standard"


def _stable_environment(monkeypatch, *, model: str = "controller-model-a") -> None:
    settings = {
        "ENERGYBRIDGE_HARNESS_PROFILE": "adaptive_v2",
        "ENERGYBRIDGE_VPP_ACCEPTANCE_GATE": "adaptive_roleplay_v2",
        "ENERGYBRIDGE_ROLEPLAY_ACCEPTANCE_GATE_USE_LLM": "1",
        "ENERGYBRIDGE_ROLEPLAY_MANUAL_OVERRIDE": "1",
        "ENERGYBRIDGE_DISABLE_ACCEPTANCE_FALLBACK": "0",
        "ENERGYBRIDGE_PERSIST_AGENT_MEMORY": "0",
        "ENERGYBRIDGE_AGENT_MEMORY_STORE": "",
        "ENERGYBRIDGE_LOAD_AGENT_MEMORY": "0",
        "ENERGYBRIDGE_FORCE_MPC_PRIMARY_NO_LLM": "0",
        "ENERGYBRIDGE_FORCE_RULE_MILP_PRIMARY_NO_LLM": "0",
        "USE_LLM": "1",
        "LLM_PROVIDER": "openai_compatible",
        "LLM_BASE_URL": "https://example.invalid/v1",
        "LLM_MODEL": model,
        "LLM_TEMPERATURE": "0.25",
        "LLM_MAX_TOKENS": "2048",
        "LLM_TIMEOUT_SECONDS": "45",
        "ROLEPLAY_USE_LLM": "1",
        "ROLEPLAY_LLM_PROVIDER": "openai_compatible",
        "ROLEPLAY_LLM_BASE_URL": "https://roleplay.example.invalid/v1",
        "ROLEPLAY_LLM_MODEL": "roleplay-model-a",
        "ROLEPLAY_LLM_TEMPERATURE": "0.7",
        "ROLEPLAY_LLM_MAX_TOKENS": "1024",
        "ROLEPLAY_LLM_TIMEOUT_SECONDS": "45",
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)


def _persona_manifest(**overrides):
    values = {
        "runner": "run_persona_json",
        "subject_kind": "persona",
        "subject_id": PERSONA_ID,
        "subject_reference": PERSONA_ID,
        "method": "EnergyBridge",
        "city": "Tianjin",
        "days": 3,
        "start_date": "",
        "vpp_start_hour": 18.0,
        "vpp_duration_hours": 1.0,
    }
    values.update(overrides)
    return build_run_manifest(**values)


def test_current_harness_is_default_and_named_aliases_are_canonical(monkeypatch) -> None:
    monkeypatch.delenv("ENERGYBRIDGE_HARNESS_PROFILE", raising=False)
    default = _persona_manifest()
    assert default["harness_profile"] == "adaptive_v2"
    assert default["harness"]["agent_component_schemas"]["planning"] == PLANNING_SCHEMA_VERSION

    for alias in ("latest", "current", "agentic_v3"):
        monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", alias)
        assert _persona_manifest()["harness_profile"] == "adaptive_v2"

    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "paper_v1")
    compatibility = _persona_manifest()
    assert compatibility["harness_profile"] == "paper_v1"
    assert all(
        value is None
        for value in compatibility["harness"]["agent_component_schemas"].values()
    )


def test_manifest_is_stable_across_api_key_rotation(monkeypatch) -> None:
    _stable_environment(monkeypatch)
    first_keys = ("sk-test-first-primary", "sk-test-first-pool")
    monkeypatch.setenv("LLM_API_KEY", first_keys[0])
    monkeypatch.setenv("LLM_API_KEY_POOL", ",".join(first_keys))
    monkeypatch.setenv("ROLEPLAY_LLM_API_KEY", "sk-test-first-roleplay")
    first = _persona_manifest()

    second_keys = ("sk-test-second-primary", "sk-test-second-pool")
    monkeypatch.setenv("LLM_API_KEY", second_keys[0])
    monkeypatch.setenv("LLM_API_KEY_POOL", ",".join(second_keys))
    monkeypatch.setenv("ROLEPLAY_LLM_API_KEY", "sk-test-second-roleplay")
    second = _persona_manifest()

    assert first == second
    serialized = json.dumps(first, sort_keys=True)
    for value in (*first_keys, *second_keys, "sk-test-first-roleplay", "sk-test-second-roleplay"):
        assert value not in serialized
    assert "api_key" not in serialized.lower()


def test_manifest_changes_with_model_profile_and_roleplay_gate(monkeypatch) -> None:
    _stable_environment(monkeypatch)
    baseline = _persona_manifest()
    assert baseline["schema_version"] == SCHEMA_VERSION
    assert baseline["harness_profile"] == "adaptive_v2"
    assert baseline["harness"]["acceptance_gate_uses_llm"] is True
    assert baseline["harness"]["agent_component_schemas"] == {
        "profile": HOUSEHOLD_MODEL_VERSION,
        "memory": MEMORY_V3_VERSION,
        "planning": PLANNING_SCHEMA_VERSION,
        "impact_evidence": ENERGY_IMPACT_SCHEMA_VERSION,
        "flexible_load_opportunities": FLEXIBLE_LOAD_OPPORTUNITY_VERSION,
        "decision_evidence_ledger": DECISION_EVIDENCE_LEDGER_VERSION,
        "outcome_calibration": OUTCOME_CALIBRATION_VERSION,
        "structured_output_transport": STRUCTURED_OUTPUT_TRANSPORT_VERSION,
    }
    assert baseline["harness"]["agent_memory_warm_start_enabled"] is False
    assert baseline["llm"]["controller"]["model"] == "controller-model-a"
    assert baseline["llm"]["roleplay"]["model"] == "roleplay-model-a"

    monkeypatch.setenv("ROLEPLAY_LLM_MODEL", "roleplay-model-b")
    different_model = _persona_manifest()
    assert different_model["fingerprint"] != baseline["fingerprint"]

    monkeypatch.setenv("ROLEPLAY_LLM_MODEL", "roleplay-model-a")
    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "paper_v1")
    monkeypatch.setenv("ENERGYBRIDGE_VPP_ACCEPTANCE_GATE", "method_neutral_v1")
    monkeypatch.setenv("ENERGYBRIDGE_ROLEPLAY_ACCEPTANCE_GATE_USE_LLM", "0")
    paper = _persona_manifest()
    assert paper["harness_profile"] == "paper_v1"
    assert paper["harness"]["acceptance_gate_uses_llm"] is False
    assert paper["harness"]["agent_component_schemas"] == {
        "profile": None,
        "memory": None,
        "planning": None,
        "impact_evidence": None,
        "flexible_load_opportunities": None,
        "decision_evidence_ledger": None,
        "outcome_calibration": None,
        "structured_output_transport": None,
    }
    assert paper["harness"]["agent_memory_warm_start_enabled"] is False
    assert paper["fingerprint"] != baseline["fingerprint"]

    monkeypatch.setenv("ENERGYBRIDGE_HARNESS_PROFILE", "legacy_v1")
    legacy = _persona_manifest()
    assert legacy["harness_profile"] == "legacy_v1"
    assert legacy["harness"]["agent_component_schemas"] == {
        "profile": None,
        "memory": None,
        "planning": None,
        "impact_evidence": None,
        "flexible_load_opportunities": None,
        "decision_evidence_ledger": None,
        "outcome_calibration": None,
        "structured_output_transport": None,
    }
    assert legacy["harness"]["agent_memory_warm_start_enabled"] is False
    assert legacy["fingerprint"] != baseline["fingerprint"]


def test_manifest_fingerprints_optional_thinking_transport(monkeypatch) -> None:
    _stable_environment(monkeypatch)
    monkeypatch.delenv("LLM_ENABLE_THINKING", raising=False)
    provider_default = _persona_manifest()

    monkeypatch.setenv("LLM_ENABLE_THINKING", "0")
    explicit_non_thinking = _persona_manifest()

    assert provider_default["llm"]["controller"]["enable_thinking"] is None
    assert explicit_non_thinking["llm"]["controller"]["enable_thinking"] is False
    assert provider_default["fingerprint"] != explicit_non_thinking["fingerprint"]


def test_manifest_fingerprints_safe_fallback_profile(monkeypatch) -> None:
    _stable_environment(monkeypatch)
    monkeypatch.setenv(
        "ENERGYBRIDGE_SAFE_FALLBACK_PROFILE",
        "evening_peak_service_first_v1",
    )
    service_first = _persona_manifest()

    monkeypatch.setenv("ENERGYBRIDGE_SAFE_FALLBACK_PROFILE", "ordinary_v1")
    historical = _persona_manifest()

    assert service_first["harness"]["safe_fallback_profile"] == "evening_peak_service_first_v1"
    assert historical["harness"]["safe_fallback_profile"] == "ordinary_v1"
    assert service_first["fingerprint"] != historical["fingerprint"]


def test_manifest_records_warm_start_without_memory_store_provenance(monkeypatch) -> None:
    _stable_environment(monkeypatch)
    cold = _persona_manifest()

    # Loading is not enabled until the operator also names an explicit store.
    monkeypatch.setenv("ENERGYBRIDGE_LOAD_AGENT_MEMORY", "1")
    missing_store = _persona_manifest()
    assert missing_store == cold

    private_store_a = "/private/household-memory-a.json"
    monkeypatch.setenv("ENERGYBRIDGE_AGENT_MEMORY_STORE", private_store_a)
    warm_a = _persona_manifest()
    assert warm_a["harness"]["agent_memory_warm_start_enabled"] is True
    assert warm_a["fingerprint"] != cold["fingerprint"]
    assert private_store_a not in json.dumps(warm_a, sort_keys=True)

    # A store location is private runtime state, not run identity.
    private_store_b = "/another/private/household-memory-b.json"
    monkeypatch.setenv("ENERGYBRIDGE_AGENT_MEMORY_STORE", private_store_b)
    warm_b = _persona_manifest()
    assert warm_b == warm_a
    serialized = json.dumps(warm_b, sort_keys=True)
    assert private_store_b not in serialized
    assert "agent_memory_store" not in serialized.lower()

    # Store contents are deliberately private and absent from the fingerprint,
    # so a warm-start result can never be proven safe for --resume reuse.
    warm_result = {"exit_code": 0, "run_manifest": warm_b}
    assert result_matches_manifest(warm_result, warm_b) is False
    assert result_matches_manifest(warm_result, warm_b["fingerprint"]) is False


def test_non_agent_methods_do_not_claim_adaptive_agent_components(monkeypatch) -> None:
    _stable_environment(monkeypatch)
    monkeypatch.setenv("ENERGYBRIDGE_AGENT_MEMORY_STORE", "/private/memory.json")
    monkeypatch.setenv("ENERGYBRIDGE_LOAD_AGENT_MEMORY", "1")

    manifest = _persona_manifest(method="HEMA")

    assert manifest["harness"]["agent_component_schemas"] == {
        "profile": None,
        "memory": None,
        "planning": None,
        "impact_evidence": None,
        "flexible_load_opportunities": None,
        "decision_evidence_ledger": None,
        "outcome_calibration": None,
        "structured_output_transport": None,
    }
    assert manifest["harness"]["agent_memory_warm_start_enabled"] is False


def test_endpoint_provenance_strips_embedded_credentials_and_query(monkeypatch) -> None:
    _stable_environment(monkeypatch)
    monkeypatch.setenv(
        "LLM_BASE_URL",
        "https://api-user:api-password@example.invalid/v1?api_key=sk-query-secret",
    )
    manifest = _persona_manifest()
    serialized = json.dumps(manifest, sort_keys=True)

    assert manifest["llm"]["controller"]["endpoint"] == "https://example.invalid/v1"
    assert "api-password" not in serialized
    assert "sk-query-secret" not in serialized
    assert "api_key" not in serialized.lower()


def test_vpp_file_fallback_parameters_are_part_of_fingerprint(monkeypatch, tmp_path) -> None:
    _stable_environment(monkeypatch)
    events_path = tmp_path / "events.json"
    events_path.write_text('[{"day": 1}]', encoding="utf-8")

    at_eighteen = _persona_manifest(
        vpp_events_json=str(events_path),
        vpp_start_hour=18.0,
        vpp_duration_hours=1.0,
    )
    at_nineteen = _persona_manifest(
        vpp_events_json=str(events_path),
        vpp_start_hour=19.0,
        vpp_duration_hours=1.0,
    )

    assert at_eighteen["scenario"]["vpp_events"]["default_start_hour"] == 18.0
    assert at_eighteen["fingerprint"] != at_nineteen["fingerprint"]


def test_resume_requires_an_intact_exact_manifest_but_keeps_v1_helper(tmp_path, monkeypatch) -> None:
    _stable_environment(monkeypatch)
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    result_path = output_dir / "benchmark_result.json"
    result_path.write_text(json.dumps({"exit_code": 0}), encoding="utf-8")
    manifest = _persona_manifest()

    assert _result_success(output_dir) is True
    assert _result_success(output_dir, manifest) is False

    result = {"exit_code": 0, "run_manifest": manifest}
    result_path.write_text(json.dumps(result), encoding="utf-8")
    assert _result_success(output_dir, manifest) is True
    assert result_matches_manifest(result, manifest["fingerprint"]) is True

    tampered = json.loads(json.dumps(result))
    tampered["run_manifest"]["llm"]["controller"]["model"] = "tampered-model"
    result_path.write_text(json.dumps(tampered), encoding="utf-8")
    assert _result_success(output_dir, manifest) is False
    assert manifest_fingerprint(tampered["run_manifest"]) != manifest["fingerprint"]


def test_matrix_and_persona_runner_defaults_produce_same_fingerprint(monkeypatch, tmp_path) -> None:
    _stable_environment(monkeypatch)
    job = Job(
        persona_id=PERSONA_ID,
        method="EnergyBridge",
        city="Tianjin",
        mpc_horizon=12,
        days=3,
        start_date="",
        price_csv="",
        vpp_start_hour=18.0,
        vpp_duration_hours=1.0,
        vpp_events_json="",
        output_dir=tmp_path / "persona",
        log_file=tmp_path / "persona.log",
    )
    expected = _manifest_for_job(job)
    runner_side = _persona_manifest(
        mpc_horizon=12,
        capacity_report_enabled=True,
        capacity_memory_json=str(DEFAULT_CAPACITY_MEMORY_JSON),
        capacity_memory_top_k=5,
        capacity_report_dry_run=False,
        user_mode="roleplay",
    )

    assert expected["fingerprint"] == runner_side["fingerprint"]
    assert expected["controller"]["mpc_horizon_steps"] == 12
    assert _command_for(job)[-2:] == ["--mpc-horizon", "12"]
    assert _persona_manifest(mpc_horizon=6)["fingerprint"] != expected["fingerprint"]


def test_matrix_and_household_runner_defaults_produce_same_fingerprint(monkeypatch, tmp_path) -> None:
    _stable_environment(monkeypatch)
    job = HouseholdJob(
        household_id=HOUSEHOLD_ID,
        method="EnergyBridge",
        city="Germany",
        mpc_horizon=9,
        days=7,
        start_date="2025-06-01",
        price_csv="",
        vpp_start_hour=17.5,
        vpp_duration_hours=1.5,
        vpp_events_json="",
        output_dir=tmp_path / "household",
        log_file=tmp_path / "household.log",
        dr_memory_library="",
    )
    expected = _household_manifest_for_job(job)
    runner_side = build_run_manifest(
        runner="run_multi_user_household",
        subject_kind="household",
        subject_id=HOUSEHOLD_ID,
        subject_reference=HOUSEHOLD_ID,
        method="EnergyBridge",
        city="Germany",
        days=7,
        start_date="2025-06-01",
        vpp_start_hour=17.5,
        vpp_duration_hours=1.5,
        mpc_horizon=9,
        max_memory_items=10,
        dr_memory_library="",
    )

    assert expected["fingerprint"] == runner_side["fingerprint"]
    assert expected["controller"]["mpc_horizon_steps"] == 9
    assert _household_command_for(job)[-2:] == ["--mpc-horizon", "9"]
