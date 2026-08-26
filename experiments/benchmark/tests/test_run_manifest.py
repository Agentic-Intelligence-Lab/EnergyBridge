from __future__ import annotations

import json

from energybridge.benchmark.run_manifest import (
    SCHEMA_VERSION,
    build_run_manifest,
    manifest_fingerprint,
    result_matches_manifest,
)
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
    assert paper["fingerprint"] != baseline["fingerprint"]


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
