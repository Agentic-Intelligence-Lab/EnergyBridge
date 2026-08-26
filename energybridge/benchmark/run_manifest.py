"""Reproducible, secret-free manifests for benchmark harness runs.

The manifest schema is V2 even when the selected harness behavior is a frozen
V1 profile.  This lets old experiment semantics keep working while giving new
runs enough provenance for safe ``--resume`` decisions.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from energybridge.harness.memory_v3 import MEMORY_V3_VERSION
from energybridge.harness.planning import PLANNING_SCHEMA_VERSION
from energybridge.harness.profile_v3 import HOUSEHOLD_MODEL_VERSION
from energybridge.utils.config import load_llm_config


SCHEMA_VERSION = "energybridge_harness_manifest_v2"
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_FORBIDDEN_FIELD_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
)
_HARNESS_PROFILE_ALIASES = {
    "v2": "adaptive_v2",
    "adaptive": "adaptive_v2",
    "adaptive_v2": "adaptive_v2",
    "energybridge_v2": "adaptive_v2",
    "paper": "paper_v1",
    "paper_v1": "paper_v1",
    "frozen_v1": "paper_v1",
    "legacy": "legacy_v1",
    "legacy_v1": "legacy_v1",
}


def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def _harness_profile() -> str:
    raw = str(os.getenv("ENERGYBRIDGE_HARNESS_PROFILE", "legacy_v1")).strip().lower()
    return _HARNESS_PROFILE_ALIASES.get(raw, raw or "legacy_v1")


def _acceptance_gate(profile: str) -> str:
    configured = str(os.getenv("ENERGYBRIDGE_VPP_ACCEPTANCE_GATE", "")).strip().lower()
    if configured:
        return configured
    return "adaptive_roleplay_v2" if profile == "adaptive_v2" else "roleplay_prompt_v1"


def _method_uses_mpc_horizon(method: str) -> bool:
    return str(method).strip().lower() in {"energybridge", "agent", "mpc_dynamic", "mpc"}


def _uses_adaptive_agent_components(profile: str, method: str) -> bool:
    return profile == "adaptive_v2" and str(method).strip().lower() in {
        "energybridge",
        "agent",
    }


def _agent_component_schemas(profile: str, method: str) -> dict[str, str | None]:
    """Describe the agent stack without exposing its state or storage location."""
    if not _uses_adaptive_agent_components(profile, method):
        return {"profile": None, "memory": None, "planning": None}
    return {
        "profile": HOUSEHOLD_MODEL_VERSION,
        "memory": MEMORY_V3_VERSION,
        "planning": PLANNING_SCHEMA_VERSION,
    }


def _agent_memory_warm_start_enabled(profile: str, method: str) -> bool:
    """Return configured warm-start state without inspecting or naming the store."""
    return bool(
        _uses_adaptive_agent_components(profile, method)
        and str(os.getenv("ENERGYBRIDGE_AGENT_MEMORY_STORE", "")).strip()
        and _env_flag("ENERGYBRIDGE_LOAD_AGENT_MEMORY")
    )


def _safe_endpoint(base_url: str) -> str:
    """Keep endpoint provenance while dropping credentials, query, and fragment."""
    try:
        parsed = urlsplit(str(base_url or ""))
        if not parsed.scheme or not parsed.hostname:
            return "custom_endpoint" if base_url else ""
        host = parsed.hostname.lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = f":{parsed.port}" if parsed.port is not None else ""
        except ValueError:
            port = ""
        safe_segments: list[str] = []
        for segment in parsed.path.split("/"):
            if not segment:
                continue
            lowered = segment.lower()
            if lowered.startswith("sk-") or len(segment) > 48:
                safe_segments.append("redacted")
            else:
                safe_segments.append(segment)
        path = f"/{'/'.join(safe_segments)}" if safe_segments else ""
        return f"{parsed.scheme.lower()}://{host}{port}{path}".rstrip("/")
    except Exception:
        return "custom_endpoint" if base_url else ""


def _safe_llm_settings(
    *,
    prefix: str,
    use_key: str,
    fallback_prefix: str | None = None,
) -> dict[str, Any]:
    config = load_llm_config(prefix=prefix, use_key=use_key, fallback_prefix=fallback_prefix)
    # Deliberately do not expose config.api_key, config.api_key_pool, or any
    # derivative of them. Changing credentials must not change a run identity.
    return {
        "enabled": bool(config.use_llm),
        "provider": str(config.provider),
        "endpoint": _safe_endpoint(config.base_url),
        "model": str(config.model),
        "temperature": float(config.temperature),
        "max_tokens": int(config.max_tokens),
        "timeout_seconds": float(config.timeout_seconds),
    }


def _display_path(path: Path, project_root: Path) -> str:
    resolved = path.expanduser().resolve(strict=False)
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _file_descriptor(path_value: str | Path | None, project_root: Path) -> dict[str, Any] | None:
    if path_value is None or not str(path_value).strip():
        return None
    path = Path(path_value).expanduser()
    descriptor: dict[str, Any] = {
        "path": _display_path(path, project_root),
        "exists": path.is_file(),
    }
    if path.is_file():
        descriptor["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return descriptor


def _calendar_path(persona_id: str, project_root: Path) -> Path | None:
    base = project_root / "energybridge" / "roleplay" / "personas" / "calendars" / persona_id
    return next(
        (candidate for candidate in (base / "calendar_7day.json", base / "calendar_3day.json") if candidate.is_file()),
        None,
    )


def _persona_path(reference: str, persona_id: str, project_root: Path) -> Path:
    referenced = Path(reference).expanduser() if reference else Path()
    if reference and referenced.is_file():
        return referenced
    return project_root / "energybridge" / "roleplay" / "personas" / f"{persona_id}.json"


def _household_path(reference: str, household_id: str, project_root: Path) -> Path:
    referenced = Path(reference).expanduser() if reference else Path()
    if reference and referenced.is_file():
        return referenced
    base = project_root / "energybridge" / "roleplay" / "households"
    flat = base / f"{household_id}.json"
    nested = base / household_id / "household.json"
    return flat if flat.is_file() or not nested.is_file() else nested


def _subject_inputs(
    *,
    subject_kind: str,
    subject_id: str,
    subject_reference: str,
    project_root: Path,
) -> list[dict[str, Any]]:
    paths: list[Path] = []
    if subject_kind == "persona":
        persona_path = _persona_path(subject_reference, subject_id, project_root)
        paths.append(persona_path)
        calendar = _calendar_path(subject_id, project_root)
        if calendar is not None:
            paths.append(calendar)
    elif subject_kind == "household":
        household_path = _household_path(subject_reference, subject_id, project_root)
        paths.append(household_path)
        try:
            household = json.loads(household_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            household = {}
        for member in household.get("members") or []:
            persona_id = str((member or {}).get("persona_id", "")).strip()
            if not persona_id:
                continue
            paths.append(project_root / "energybridge" / "roleplay" / "personas" / f"{persona_id}.json")
            calendar = _calendar_path(persona_id, project_root)
            if calendar is not None:
                paths.append(calendar)
    else:
        raise ValueError(f"Unsupported subject_kind: {subject_kind!r}")

    unique: dict[str, Path] = {_display_path(path, project_root): path for path in paths}
    return [
        descriptor
        for _, path in sorted(unique.items())
        if (descriptor := _file_descriptor(path, project_root)) is not None
    ]


def _effective_asset_inputs(
    *,
    city: str,
    days: int,
    price_csv: str,
    idf: str,
    epw: str,
    weather_csv: str,
    project_root: Path,
) -> dict[str, Any]:
    city_key = city.lower()
    family_model_dir = project_root / "experiments" / "models" / "family_home"
    if idf:
        idf_path = Path(idf)
    elif city_key == "germany":
        idf_path = family_model_dir / "berlin_family_geg_final.idf"
    else:
        templates = {
            3: family_model_dir / "family_simple_3day.idf",
            7: family_model_dir / "family_simple_7day.idf",
            14: family_model_dir / "family_simple_14day.idf",
        }
        idf_path = templates.get(days, templates[7] if days > 3 else templates[3])

    default_epw = {
        "tianjin": project_root / "experiments" / "weather" / "epw" / "CHN_TJ_Tianjin.545270_CSWD.epw",
        "beijing": project_root / "experiments" / "weather" / "epw" / "CHN_BJ_Beijing.545110_CSWD.epw",
        "shanghai": project_root / "experiments" / "weather" / "epw" / "CHN_SH_Shanghai.583620_CSWD.epw",
        "germany": project_root / "experiments" / "weather" / "epw" / "DEU_Germany_2025_real.epw",
    }
    epw_path = Path(epw) if epw else default_epw.get(city_key)

    if price_csv:
        price_path: Path | None = Path(price_csv)
        price_mode = "explicit"
    else:
        auto_price = project_root / "experiments" / "real_data" / "tianjin_tou_price_normalized.csv"
        price_path = auto_price if city_key == "tianjin" and auto_price.is_file() else None
        price_mode = "auto_tianjin_tou" if price_path is not None else "disabled"

    if city_key == "germany":
        weather_path: Path | None = (
            Path(weather_csv)
            if weather_csv
            else project_root / "experiments" / "real_data" / "germany_2025_weather.csv"
        )
    else:
        weather_path = None

    return {
        "idf_template": _file_descriptor(idf_path, project_root),
        "epw": _file_descriptor(epw_path, project_root),
        "weather_csv": _file_descriptor(weather_path, project_root),
        "price": {
            "mode": price_mode,
            "file": _file_descriptor(price_path, project_root),
        },
    }


@lru_cache(maxsize=4)
def _implementation_digest(runner: str, project_root_text: str) -> dict[str, Any]:
    project_root = Path(project_root_text)
    paths = list((project_root / "energybridge").rglob("*.py"))
    paths.extend(
        [
            project_root / "experiments" / "benchmark" / "family_runner.py",
            project_root / "experiments" / "benchmark" / "user_pref_scorer.py",
            project_root / "experiments" / "benchmark" / f"{runner}.py",
        ]
    )
    existing = sorted({path.resolve() for path in paths if path.is_file()}, key=lambda item: str(item))
    digest = hashlib.sha256()
    for path in existing:
        digest.update(_display_path(path, project_root).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {"sha256": digest.hexdigest(), "file_count": len(existing)}


@lru_cache(maxsize=1)
def _runtime_versions() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for distribution in ("openai", "numpy", "pandas", "scipy"):
        try:
            packages[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return {
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "packages": packages,
    }


def _runner_options(
    *,
    runner: str,
    project_root: Path,
    user_mode: str,
    human_name: str,
    max_memory_items: int,
    dr_memory_library: str,
    capacity_report_enabled: bool,
    capacity_memory_json: str,
    capacity_memory_top_k: int,
    capacity_report_dry_run: bool,
) -> dict[str, Any]:
    if runner == "run_persona_json":
        memory_path = capacity_memory_json or str(
            project_root
            / "dr_capacity_memory_toolkit"
            / "june_2025_daily_eb_rule_milp"
            / "data"
            / "eb_rule_milp_daily_dr_memory.json"
        )
        return {
            "user_mode": str(user_mode),
            "human_name": str(human_name).strip() if str(user_mode) == "human" else "",
            "capacity_report_enabled": bool(capacity_report_enabled),
            "capacity_memory": (
                _file_descriptor(memory_path, project_root) if capacity_report_enabled else None
            ),
            "capacity_memory_top_k": max(1, int(capacity_memory_top_k)),
            "capacity_report_dry_run": bool(capacity_report_dry_run),
        }
    if runner == "run_multi_user_household":
        return {
            "max_memory_items": max(1, int(max_memory_items)),
            "dr_memory_library": _file_descriptor(dr_memory_library, project_root),
        }
    raise ValueError(f"Unsupported runner: {runner!r}")


def _validate_secret_free(value: Any, path: str = "manifest") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_FIELD_PARTS):
                raise ValueError(f"Secret-bearing field is forbidden in run manifest: {path}.{key}")
            _validate_secret_free(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_secret_free(child, f"{path}[{index}]")


def _payload_without_fingerprint(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in manifest.items() if key != "fingerprint"}


def manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Return a deterministic SHA-256 over a manifest's non-secret payload."""
    payload = _payload_without_fingerprint(manifest)
    _validate_secret_free(payload)
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_run_manifest(
    *,
    runner: str,
    subject_kind: str,
    subject_id: str,
    subject_reference: str = "",
    method: str,
    city: str,
    days: int,
    start_date: str = "",
    price_csv: str = "",
    vpp_start_hour: float = 18.0,
    vpp_duration_hours: float = 1.0,
    vpp_events_json: str = "",
    mpc_horizon: int = 6,
    idf: str = "",
    epw: str = "",
    weather_csv: str = "",
    regenerate_epw: bool = False,
    user_mode: str = "roleplay",
    human_name: str = "",
    max_memory_items: int = 10,
    dr_memory_library: str = "",
    capacity_report_enabled: bool = True,
    capacity_memory_json: str = "",
    capacity_memory_top_k: int = 5,
    capacity_report_dry_run: bool = False,
    project_root: str | Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Build a V2 benchmark manifest without reading or hashing API keys."""
    root = Path(project_root).resolve()
    profile = _harness_profile()
    force_primary = _env_flag("ENERGYBRIDGE_FORCE_MPC_PRIMARY_NO_LLM") or _env_flag(
        "ENERGYBRIDGE_FORCE_RULE_MILP_PRIMARY_NO_LLM"
    )
    vpp_events = (
        {
            "mode": "file",
            "file": _file_descriptor(vpp_events_json, root),
            "default_start_hour": float(vpp_start_hour) % 24.0,
            "default_duration_hours": float(vpp_duration_hours),
        }
        if vpp_events_json
        else {
            "mode": "daily_default",
            "start_hour": float(vpp_start_hour) % 24.0,
            "duration_hours": float(vpp_duration_hours),
        }
    )
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "runner": runner,
        "harness_profile": profile,
        "subject": {
            "kind": subject_kind,
            "id": str(subject_id),
            "inputs": _subject_inputs(
                subject_kind=subject_kind,
                subject_id=str(subject_id),
                subject_reference=str(subject_reference or subject_id),
                project_root=root,
            ),
        },
        "scenario": {
            "city": str(city),
            "days": int(days),
            "start_date": str(start_date or ""),
            "vpp_events": vpp_events,
            "assets": _effective_asset_inputs(
                city=str(city),
                days=int(days),
                price_csv=str(price_csv),
                idf=str(idf),
                epw=str(epw),
                weather_csv=str(weather_csv),
                project_root=root,
            ),
            "regenerate_epw": bool(regenerate_epw) if str(city).lower() == "germany" else False,
        },
        "controller": {
            "method": str(method),
            "mpc_horizon_steps": int(mpc_horizon) if _method_uses_mpc_horizon(method) else None,
        },
        "llm": {
            "controller": _safe_llm_settings(prefix="LLM", use_key="USE_LLM"),
            "roleplay": _safe_llm_settings(
                prefix="ROLEPLAY_LLM",
                use_key="ROLEPLAY_USE_LLM",
                fallback_prefix="LLM",
            ),
        },
        "harness": {
            "acceptance_gate": _acceptance_gate(profile),
            "acceptance_gate_uses_llm": _env_flag(
                "ENERGYBRIDGE_ROLEPLAY_ACCEPTANCE_GATE_USE_LLM", "1"
            ),
            "manual_override_uses_llm": _env_flag("ENERGYBRIDGE_ROLEPLAY_MANUAL_OVERRIDE", "1"),
            "acceptance_fallback_disabled": _env_flag("ENERGYBRIDGE_DISABLE_ACCEPTANCE_FALLBACK"),
            "persist_agent_memory": _env_flag("ENERGYBRIDGE_PERSIST_AGENT_MEMORY"),
            "agent_component_schemas": _agent_component_schemas(profile, method),
            "agent_memory_warm_start_enabled": _agent_memory_warm_start_enabled(
                profile,
                method,
            ),
            "force_mpc_primary_without_llm": force_primary,
        },
        "runner_options": _runner_options(
            runner=runner,
            project_root=root,
            user_mode=user_mode,
            human_name=human_name,
            max_memory_items=max_memory_items,
            dr_memory_library=dr_memory_library,
            capacity_report_enabled=capacity_report_enabled,
            capacity_memory_json=capacity_memory_json,
            capacity_memory_top_k=capacity_memory_top_k,
            capacity_report_dry_run=capacity_report_dry_run,
        ),
        "implementation": _implementation_digest(runner, str(root)),
        "runtime": _runtime_versions(),
    }
    manifest["fingerprint"] = manifest_fingerprint(manifest)
    return manifest


def result_matches_manifest(
    result: Mapping[str, Any], expected_manifest_or_fingerprint: Mapping[str, Any] | str
) -> bool:
    """Return True only for a successful result with an intact exact manifest."""
    if result.get("exit_code") != 0:
        return False
    actual = result.get("run_manifest")
    if not isinstance(actual, Mapping):
        return False
    # A warm-start manifest intentionally omits the private memory path and
    # contents.  Consequently its fingerprint cannot prove that the result
    # used the same household state.  Never reuse such a run through --resume;
    # warm-start studies must execute against the explicitly selected store.
    actual_harness = actual.get("harness")
    if (
        isinstance(actual_harness, Mapping)
        and bool(actual_harness.get("agent_memory_warm_start_enabled"))
    ):
        return False
    if isinstance(expected_manifest_or_fingerprint, Mapping):
        expected_harness = expected_manifest_or_fingerprint.get("harness")
        if (
            isinstance(expected_harness, Mapping)
            and bool(expected_harness.get("agent_memory_warm_start_enabled"))
        ):
            return False
    stored = actual.get("fingerprint")
    if not isinstance(stored, str) or not stored:
        return False
    try:
        if stored != manifest_fingerprint(actual):
            return False
        expected = (
            manifest_fingerprint(expected_manifest_or_fingerprint)
            if isinstance(expected_manifest_or_fingerprint, Mapping)
            else str(expected_manifest_or_fingerprint)
        )
    except (TypeError, ValueError):
        return False
    return stored == expected


def result_manifest_fingerprint(result: Mapping[str, Any]) -> str:
    manifest = result.get("run_manifest")
    if not isinstance(manifest, Mapping):
        return ""
    fingerprint = manifest.get("fingerprint")
    return str(fingerprint) if isinstance(fingerprint, str) else ""
