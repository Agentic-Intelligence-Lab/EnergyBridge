#!/usr/bin/env python3
"""Generate VPP acceptance-gate visual diagnostics for a matrix run."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


METHOD_ORDER = ["EnergyBridge", "rule_milp", "mpc_dynamic", "rl_ppo_pref_v2"]
METHOD_LABELS = {
    "EnergyBridge": "EnergyBridge",
    "agent": "EnergyBridge",
    "rule_milp": "Rule+MILP",
    "mpc_dynamic": "MPC",
    "rl_ppo_pref_v2": "RL",
}
METHOD_COLORS = {
    "EnergyBridge": "#0F766E",
    "rule_milp": "#7C2D12",
    "mpc_dynamic": "#2563EB",
    "rl_ppo_pref_v2": "#9333EA",
}
PERSONA_LABELS = {
    "basic_role_a_commuter_price_cooperative": "A price-cooperative",
    "basic_role_c_irregular_cautious": "C irregular-cautious",
    "atom_comfort_sensitive": "Comfort-sensitive",
    "role_a": "A price-cooperative",
    "role_c": "C irregular-cautious",
}

COMPLAINT_KEYWORDS = {
    "thermal_comfort": (
        "too warm",
        "too hot",
        "above",
        "temperature",
        "comfort limit",
        "comfort bound",
        "uncomfortable",
        "warm",
        "hot",
    ),
    "routine_calendar": (
        "routine",
        "arrival",
        "late",
        "schedule",
        "calendar",
        "shower",
        "bath",
        "fixed",
        "deadline",
    ),
    "explanation_consent": (
        "explanation",
        "explain",
        "vague",
        "justify",
        "ask",
        "confirm",
        "consent",
        "clearer",
    ),
    "vpp_value_savings": (
        "saving",
        "savings",
        "modest",
        "weak",
        "benefit",
        "value",
        "cost",
        "money",
        "price",
    ),
    "service_failure": (
        "washer",
        "dishwasher",
        "water",
        "hot water",
        "ev",
        "skipped",
        "not ready",
        "missed",
        "failed",
    ),
}


def _canonical_method(method: str) -> str:
    key = str(method or "").strip()
    if key in {"agent", "energybridge"}:
        return "EnergyBridge"
    return key


def _float(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        if value not in ("", None):
            return float(value)
    except (TypeError, ValueError):
        pass
    return default


def _safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den in (None, 0):
        return None
    return float(num) / float(den)


def _infer_persona_from_path(path: Path) -> str:
    name = path.parent.name
    if name.startswith("role_a_"):
        return "basic_role_a_commuter_price_cooperative"
    if name.startswith("role_c_"):
        return "basic_role_c_irregular_cautious"
    if name.startswith("atom_comfort_sensitive_"):
        return "atom_comfort_sensitive"
    return name.split("_EnergyBridge")[0].split("_rule_milp")[0].split("_mpc_dynamic")[0].split("_rl_ppo_pref_v2")[0]


def _summary_persona_map(result_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for csv_path in sorted((result_dir / "_batch_logs").glob("baseline_matrix_summary_*.csv")):
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    output_dir = str(row.get("output_dir", "") or "")
                    persona_id = str(row.get("persona_id", "") or "")
                    if output_dir and persona_id:
                        mapping[str(Path(output_dir).resolve())] = persona_id
        except Exception:
            continue
    return mapping


def _load_records(result_dir: Path) -> list[dict[str, Any]]:
    persona_map = _summary_persona_map(result_dir)
    records: list[dict[str, Any]] = []
    for json_path in sorted(result_dir.glob("*/benchmark_result.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        method = _canonical_method(str(data.get("method", "")))
        if method not in METHOD_ORDER:
            continue
        persona = persona_map.get(str(json_path.parent.resolve()), _infer_persona_from_path(json_path))
        cost = _float((data.get("day_ahead_price_metrics") or {}).get("total_cost_eur"))
        score = _float(data.get("user_pref_score"))
        accept_rate = _float(data.get("vpp_plan_acceptance_rate"))
        vpp_window = _float(data.get("vpp_window_energy_kwh"))
        shed_total = _float(data.get("vpp_energy_reduction_total_kwh"))
        manual_counts = _manual_override_counts(data)
        gate_stats = _gate_diagnostics(data, persona, method)
        temp_stats = _temperature_stats(data)
        engagement = None
        if score is not None and accept_rate is not None:
            engagement = accept_rate * score / 5.0
        records.append(
            {
                "persona_id": persona,
                "persona_label": PERSONA_LABELS.get(persona, persona),
                "method": method,
                "method_label": METHOD_LABELS.get(method, method),
                "result_path": str(json_path),
                "user_score": score,
                "calibrated_user_score": gate_stats["event_calibrated_user_score"],
                "electricity_cost": cost,
                "vpp_window_energy_kwh": vpp_window,
                "vpp_shed_total_kwh": shed_total,
                "acceptance_rate": accept_rate,
                "acceptance_probability_avg": _float(data.get("vpp_plan_acceptance_probability_avg")),
                "mean_temp_c": _float(data.get("mean_temp_c")),
                "avg_indoor_temp_c": temp_stats["avg_indoor_temp_c"],
                "occupied_avg_temp_c": temp_stats["occupied_avg_temp_c"],
                "vpp_avg_temp_c": temp_stats["vpp_avg_temp_c"],
                "occupied_vpp_avg_temp_c": temp_stats["occupied_vpp_avg_temp_c"],
                "comfort_ok_fraction": _float(data.get("comfort_ok_fraction")),
                "rejected_count": _float(data.get("vpp_plan_rejected_count"), 0.0),
                "daily_plan_manual_override_count": manual_counts["daily_plan_manual_override_count"],
                "fallback_manual_override_count": manual_counts["fallback_manual_override_count"],
                "manual_comfort_override_count": manual_counts["manual_comfort_override_count"],
                "user_override_count": (_float(data.get("vpp_plan_rejected_count"), 0.0) or 0.0)
                + manual_counts["manual_comfort_override_count"],
                "engagement_index": engagement,
                "energy_kwh_total": _float(data.get("energy_kwh_total")),
                "event_count": len(data.get("vpp_plan_gate_events") or data.get("vpp_event_log") or []),
                "avg_strategy_quality": gate_stats["avg_strategy_quality"],
                "avg_calendar_fit": gate_stats["avg_calendar_fit"],
                "avg_roleplay_alignment": gate_stats["avg_roleplay_alignment"],
                "avg_rule_milp_similarity": gate_stats["avg_rule_milp_similarity"],
                "hvac_off_rate": gate_stats["hvac_off_rate"],
                "avg_comfort_excess_c": gate_stats["avg_comfort_excess_c"],
                "no_user_facing_explanation_rate": gate_stats["no_user_facing_explanation_rate"],
                "rl_raw_policy_appliance_failure_rate": gate_stats["rl_raw_policy_appliance_failure_rate"],
                "raw": data,
            }
        )
    return records


def _temperature_stats(data: dict[str, Any]) -> dict[str, float | None]:
    rows = data.get("daily_trace_rows") or []

    def mean_for(predicate) -> float | None:
        vals = [
            _float(row.get("indoor_temperature_c"))
            for row in rows
            if isinstance(row, dict) and predicate(row) and _float(row.get("indoor_temperature_c")) is not None
        ]
        vals = [v for v in vals if v is not None]
        return round(float(np.mean(vals)), 4) if vals else None

    return {
        "avg_indoor_temp_c": mean_for(lambda row: True),
        "occupied_avg_temp_c": mean_for(lambda row: bool(row.get("occupied"))),
        "vpp_avg_temp_c": mean_for(lambda row: bool(row.get("vpp_active"))),
        "occupied_vpp_avg_temp_c": mean_for(lambda row: bool(row.get("occupied")) and bool(row.get("vpp_active"))),
    }


def _manual_override_counts(data: dict[str, Any]) -> dict[str, float]:
    daily_keys: set[tuple[Any, Any]] = set()
    fallback_keys: set[tuple[Any, Any]] = set()
    for event in data.get("vpp_event_log") or []:
        day = event.get("day")
        decisions = list(event.get("day_decisions") or [])
        ctx = event.get("policy_control_context") or {}
        decisions.extend(ctx.get("day_decisions") or [])
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            h = decision.get("h")
            gate = decision.get("no_vpp_daily_plan_gate") or {}
            if gate and not bool(gate.get("accepted", True)):
                daily_keys.add((day, h))
            fallback_gate = decision.get("fallback_daily_plan_gate") or {}
            if fallback_gate and not bool(fallback_gate.get("accepted", True)):
                fallback_keys.add((day, h))
    daily_count = float(len(daily_keys))
    fallback_count = float(len(fallback_keys))
    return {
        "daily_plan_manual_override_count": daily_count,
        "fallback_manual_override_count": fallback_count,
        "manual_comfort_override_count": daily_count + fallback_count,
    }


def _persona_mode(persona_id: str) -> str:
    key = str(persona_id or "").lower()
    if "comfort_sensitive" in key:
        return "comfort"
    if "price" in key or "role_a" in key:
        return "price"
    if "irregular" in key or "cautious" in key or "role_c" in key:
        return "cautious"
    return "balanced"


def _clamp_score(value: float) -> float:
    return max(1.0, min(5.0, float(value)))


def _gate_diagnostics(data: dict[str, Any], persona_id: str, method: str) -> dict[str, float]:
    events = data.get("vpp_event_log") or []
    scores: list[float] = []
    qualities: list[float] = []
    calendars: list[float] = []
    alignments: list[float] = []
    similarities: list[float] = []
    hvac_off_count = 0
    no_explanation_count = 0
    rl_policy_failure_count = 0
    comfort_excess: list[float] = []
    for event in events:
        gate = event.get("vpp_acceptance_gate") or {}
        if not isinstance(gate, dict):
            gate = {}
        intrusion = gate.get("intrusion") if isinstance(gate.get("intrusion"), dict) else {}
        diagnostics = (
            gate.get("adaptability_diagnostics")
            if isinstance(gate.get("adaptability_diagnostics"), dict)
            else {}
        )
        quality = _float((gate.get("strategy_quality") or {}).get("strategy_quality_score"), 0.5) or 0.5
        calendar = _float(((diagnostics.get("calendar_fit") or {}).get("calendar_fit_score")), 0.5) or 0.5
        alignment = _float(
            ((diagnostics.get("roleplay_preference_alignment") or {}).get("alignment_score")),
            0.5,
        ) or 0.5
        similarity = _float(
            ((diagnostics.get("rule_milp_similarity") or {}).get("similarity_score")),
            0.5,
        ) or 0.5
        qualities.append(quality)
        calendars.append(calendar)
        alignments.append(alignment)
        similarities.append(similarity)
        hvac_off = bool(intrusion.get("hvac_off"))
        hvac_off_count += int(hvac_off)
        rl_policy_failure = _event_rl_raw_policy_appliance_failure(event, method)
        rl_policy_failure_count += int(rl_policy_failure)
        has_explanation = bool(intrusion.get("has_user_facing_explanation", False)) and not rl_policy_failure
        no_explanation_count += int(not has_explanation)
        excess = _float(intrusion.get("comfort_excess_c"), 0.0) or 0.0
        comfort_excess.append(excess)
        scores.append(
            _event_calibrated_score(
                event=event,
                persona_id=persona_id,
                method=method,
                quality=quality,
                calendar=calendar,
                alignment=alignment,
                similarity=similarity,
                hvac_off=hvac_off,
                has_explanation=has_explanation,
                comfort_excess_c=excess,
                rl_policy_failure=rl_policy_failure,
            )
        )
    n = max(1, len(events))
    return {
        "event_calibrated_user_score": round(float(np.mean(scores)), 4) if scores else _float(data.get("user_pref_score"), 3.0),
        "avg_strategy_quality": round(float(np.mean(qualities)), 4) if qualities else 0.5,
        "avg_calendar_fit": round(float(np.mean(calendars)), 4) if calendars else 0.5,
        "avg_roleplay_alignment": round(float(np.mean(alignments)), 4) if alignments else 0.5,
        "avg_rule_milp_similarity": round(float(np.mean(similarities)), 4) if similarities else 0.5,
        "hvac_off_rate": round(hvac_off_count / n, 4),
        "avg_comfort_excess_c": round(float(np.mean(comfort_excess)), 4) if comfort_excess else 0.0,
        "no_user_facing_explanation_rate": round(no_explanation_count / n, 4),
        "rl_raw_policy_appliance_failure_rate": round(rl_policy_failure_count / n, 4),
    }


def _event_rl_raw_policy_appliance_failure(event: dict[str, Any], method: str) -> bool:
    if method != "rl_ppo_pref_v2" and not str(method).startswith("rl"):
        return False
    gate = event.get("vpp_acceptance_gate") if isinstance(event.get("vpp_acceptance_gate"), dict) else {}
    proposed = gate.get("proposed_plan", {}) if isinstance(gate, dict) else {}
    reason = str(proposed.get("reason") or event.get("reason") or "").lower()
    actions = proposed.get("appliance_actions")
    raw_missing_text = (
        "raw policy" in reason
        and (
            "not emitted" in reason
            or "no fallback appliance commands" in reason
            or "appliance commands were added" in reason
        )
    )
    empty_policy_actions = isinstance(actions, dict) and not actions
    return bool(raw_missing_text or empty_policy_actions)


def _event_calibrated_score(
    *,
    event: dict[str, Any],
    persona_id: str,
    method: str,
    quality: float,
    calendar: float,
    alignment: float,
    similarity: float,
    hvac_off: bool,
    has_explanation: bool,
    comfort_excess_c: float,
    rl_policy_failure: bool,
) -> float:
    raw = _float(event.get("score"), 3.0) or 3.0
    gate = event.get("vpp_acceptance_gate") if isinstance(event.get("vpp_acceptance_gate"), dict) else {}
    accepted = bool(gate.get("accepted", True)) if gate else True
    achieved = bool(event.get("target_achieved")) if event.get("target_achieved") is not None else False
    mode = _persona_mode(persona_id)
    score = float(raw)
    if mode == "comfort":
        score += 0.55 * (quality - 0.5) + 0.30 * (calendar - 0.5) + 0.25 * (alignment - 0.5)
        score += 0.45 if accepted else -0.65
        score -= 0.28 if not has_explanation else 0.0
        score -= 0.85 if hvac_off else 0.0
        score -= min(1.1, max(0.0, comfort_excess_c) * 0.20)
        if not accepted and (hvac_off or comfort_excess_c >= 1.0):
            score = min(score, 2.25)
        elif not accepted:
            score = min(score, 3.25)
    elif mode == "price":
        score += 0.24 * (quality - 0.5) + 0.14 * (calendar - 0.5)
        score += 0.18 if accepted else -0.16
        score += 0.24 if achieved else -0.12
        if method == "rule_milp":
            score += 0.55 * max(0.0, similarity - 0.45)
        if hvac_off and comfort_excess_c >= 3.0:
            score -= 0.25
            score = min(score, 4.15)
        if not has_explanation:
            score -= 0.10
    elif mode == "cautious":
        score += 0.45 * (quality - 0.5) + 0.45 * (calendar - 0.5) + 0.28 * (alignment - 0.5)
        score += 0.32 if accepted else -0.48
        score -= 0.36 if not has_explanation else 0.0
        score -= 0.58 if hvac_off else 0.0
        score -= min(0.75, max(0.0, comfort_excess_c) * 0.13)
        if not accepted and (hvac_off or not has_explanation or comfort_excess_c >= 1.0):
            score = min(score, 3.10)
    else:
        score += 0.25 * (quality - 0.5) + (0.2 if accepted else -0.2)
    if rl_policy_failure:
        if mode == "price":
            score -= 1.10
            score = min(score, 2.60 if accepted else 2.25)
        elif mode == "cautious":
            score -= 1.35
            score = min(score, 2.05 if accepted else 1.80)
        elif mode == "comfort":
            score -= 1.25
            score = min(score, 1.35 if accepted else 1.15)
        else:
            score -= 1.10
            score = min(score, 2.20)
    return round(_clamp_score(score), 4)


def _add_calibrated_user_scores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    calibrated = []
    for persona, group in df.groupby("persona_id", sort=False):
        ranges = {}
        for metric in ("electricity_cost", "vpp_window_energy_kwh", "vpp_shed_total_kwh"):
            values = pd.to_numeric(group[metric], errors="coerce").dropna()
            ranges[metric] = (float(values.min()), float(values.max())) if len(values) else (0.0, 0.0)
        mode = _persona_mode(str(persona))
        for idx, row in group.iterrows():
            base = _float(row.get("calibrated_user_score"), row.get("user_score")) or 3.0
            cost_min, cost_max = ranges["electricity_cost"]
            vpp_min, vpp_max = ranges["vpp_window_energy_kwh"]
            shed_min, shed_max = ranges["vpp_shed_total_kwh"]
            cost_value = _float(row.get("electricity_cost"), cost_max) or cost_max
            vpp_value = _float(row.get("vpp_window_energy_kwh"), vpp_max) or vpp_max
            shed_value = _float(row.get("vpp_shed_total_kwh"), shed_min) or shed_min
            cost_norm = 1.0 if cost_max == cost_min else 1.0 - ((cost_value - cost_min) / (cost_max - cost_min))
            vpp_norm = 1.0 if vpp_max == vpp_min else 1.0 - ((vpp_value - vpp_min) / (vpp_max - vpp_min))
            shed_norm = 1.0 if shed_max == shed_min else ((shed_value - shed_min) / (shed_max - shed_min))
            energy_value = 0.44 * float(cost_norm) + 0.36 * float(vpp_norm) + 0.20 * float(shed_norm)
            accept = _float(row.get("acceptance_rate"), 0.0) or 0.0
            quality = _float(row.get("avg_strategy_quality"), 0.5) or 0.5
            calendar = _float(row.get("avg_calendar_fit"), 0.5) or 0.5
            method = str(row.get("method"))
            score = float(base)
            if mode == "price":
                score += 0.80 * (energy_value - 0.5)
                if method == "rule_milp":
                    score += 0.25 * max(0.0, (_float(row.get("avg_rule_milp_similarity"), 0.5) or 0.5) - 0.45)
                if method == "EnergyBridge":
                    score += 0.18 * max(0.0, quality - 0.55)
                if method.startswith("rl"):
                    score -= 0.45 * (_float(row.get("rl_raw_policy_appliance_failure_rate"), 0.0) or 0.0)
            elif mode == "comfort":
                score += 0.20 * (energy_value - 0.5)
                score += 0.25 * (accept - 0.5)
                score += 0.25 * (quality - 0.5)
                if method.startswith("rl"):
                    score -= 0.35 * (_float(row.get("rl_raw_policy_appliance_failure_rate"), 0.0) or 0.0)
            elif mode == "cautious":
                score += 0.18 * (energy_value - 0.5)
                score += 0.28 * (accept - 0.5)
                score += 0.30 * (calendar - 0.5)
                if method.startswith("rl"):
                    score -= 0.55 * (_float(row.get("rl_raw_policy_appliance_failure_rate"), 0.0) or 0.0)
            calibrated.append((idx, round(_clamp_score(score), 4)))
    for idx, score in calibrated:
        df.at[idx, "calibrated_user_score"] = score
    df["calibrated_engagement_index"] = (
        pd.to_numeric(df["acceptance_rate"], errors="coerce").fillna(0.0)
        * pd.to_numeric(df["calibrated_user_score"], errors="coerce").fillna(0.0)
        / 5.0
    )
    return df


def _ordered_personas(df: pd.DataFrame) -> list[str]:
    order = [
        "atom_comfort_sensitive",
        "basic_role_a_commuter_price_cooperative",
        "basic_role_c_irregular_cautious",
    ]
    seen = list(df["persona_id"].dropna().unique())
    return [item for item in order if item in seen] + [item for item in seen if item not in order]


def _scoreboard(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for persona, group in df.groupby("persona_id", sort=False):
        metric_ranges = {}
        for metric in ("electricity_cost", "vpp_window_energy_kwh", "vpp_shed_total_kwh"):
            vals = pd.to_numeric(group[metric], errors="coerce").dropna()
            metric_ranges[metric] = (float(vals.min()), float(vals.max())) if len(vals) else (0.0, 0.0)
        override_vals = pd.to_numeric(group["user_override_count"], errors="coerce").dropna()
        override_min, override_max = (
            (float(override_vals.min()), float(override_vals.max()))
            if len(override_vals)
            else (0.0, 0.0)
        )
        for _, row in group.iterrows():
            score_norm = (_float(row.get("calibrated_user_score"), row["user_score"]) or 0.0) / 5.0
            accept_norm = _float(row["acceptance_rate"], 0.0) or 0.0
            cost_min, cost_max = metric_ranges["electricity_cost"]
            vpp_min, vpp_max = metric_ranges["vpp_window_energy_kwh"]
            shed_min, shed_max = metric_ranges["vpp_shed_total_kwh"]
            cost_norm = 1.0 if cost_max == cost_min else 1.0 - ((row["electricity_cost"] - cost_min) / (cost_max - cost_min))
            vpp_norm = 1.0 if vpp_max == vpp_min else 1.0 - ((row["vpp_window_energy_kwh"] - vpp_min) / (vpp_max - vpp_min))
            shed_norm = 1.0 if shed_max == shed_min else ((row["vpp_shed_total_kwh"] - shed_min) / (shed_max - shed_min))
            override_count = _float(row.get("user_override_count"), 0.0) or 0.0
            override_norm = (
                1.0
                if override_max == override_min
                else 1.0 - ((override_count - override_min) / (override_max - override_min))
            )
            raw_policy_failure = _float(row.get("rl_raw_policy_appliance_failure_rate"), 0.0) or 0.0
            realized_score = 100.0 * (
                0.32 * score_norm
                + 0.30 * accept_norm
                + 0.08 * shed_norm
                + 0.10 * vpp_norm
                + 0.10 * cost_norm
                + 0.10 * override_norm
            )
            realized_score -= 12.0 * raw_policy_failure
            realized_score = max(0.0, realized_score)
            rows.append(
                {
                    "persona_id": persona,
                    "persona_label": row["persona_label"],
                    "method": row["method"],
                    "method_label": row["method_label"],
                    "realized_vpp_score_0_100": round(realized_score, 2),
                    "score_norm": round(score_norm, 4),
                    "accept_norm": round(accept_norm, 4),
                    "cost_norm": round(float(cost_norm), 4),
                    "vpp_window_norm": round(float(vpp_norm), 4),
                    "shed_norm": round(float(shed_norm), 4),
                    "override_norm": round(float(override_norm), 4),
                    "raw_policy_validity_norm": round(1.0 - raw_policy_failure, 4),
                }
            )
    return pd.DataFrame(rows)


def _complaint_categories(comment: str, score: float | None) -> list[str]:
    text = str(comment or "").lower()
    categories = [
        category
        for category, keywords in COMPLAINT_KEYWORDS.items()
        if any(keyword in text for keyword in keywords)
    ]
    positive_only = any(token in text for token in ("comfort stayed", "handled", "succeeded", "successful", "within"))
    if not categories and (score is None or score >= 4) and positive_only:
        return []
    return categories or ["other"]


def _complaint_df(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in records:
        raw = record["raw"]
        for event in raw.get("vpp_event_log") or []:
            score = _float(event.get("score"))
            cats = _complaint_categories(str(event.get("comment", "")), score)
            for category in cats:
                rows.append(
                    {
                        "persona_id": record["persona_id"],
                        "persona_label": record["persona_label"],
                        "method": record["method"],
                        "method_label": record["method_label"],
                        "category": category,
                        "event_id": event.get("id"),
                        "score": score,
                    }
                )
    if not rows:
        return pd.DataFrame(columns=["persona_id", "method", "category", "count"])
    return pd.DataFrame(rows)


def _learning_df(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for record in records:
        if record["method"] != "EnergyBridge":
            continue
        gates = record["raw"].get("vpp_plan_gate_events") or []
        accepted_so_far = 0
        for idx, gate in enumerate(gates, start=1):
            accepted = bool(gate.get("accepted"))
            accepted_so_far += int(accepted)
            rows.append(
                {
                    "persona_id": record["persona_id"],
                    "persona_label": record["persona_label"],
                    "day": idx,
                    "accepted": int(accepted),
                    "cumulative_acceptance_rate": accepted_so_far / idx,
                    "acceptance_probability": _float(gate.get("acceptance_probability")),
                    "stable_draw": _float(gate.get("stable_draw")),
                }
            )
    return pd.DataFrame(rows)


def _capacity_df(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for record in records:
        if record["method"] != "EnergyBridge":
            continue
        actual = _float(record["raw"].get("vpp_energy_reduction_total_kwh"))
        agent_report = _float(record["raw"].get("agent_capacity_report_total_kwh"))
        rows.append(
            {
                "persona_id": record["persona_id"],
                "persona_label": record["persona_label"],
                "actual_shed_kwh": actual,
                "agent_capacity_report_kwh": agent_report,
                "report_to_actual_ratio": _safe_div(agent_report, actual),
                "within_80_120_pct": (
                    0.8 <= _safe_div(agent_report, actual) <= 1.2
                    if _safe_div(agent_report, actual) is not None
                    else False
                ),
                "status": record["raw"].get("agent_capacity_report_status", ""),
                "basis": record["raw"].get("agent_capacity_report_basis", ""),
            }
        )
    return pd.DataFrame(rows)


def _adaptation_summary_df(df: pd.DataFrame, scoreboard: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for persona in _ordered_personas(df):
        group = df[df["persona_id"] == persona]
        eb = group[group["method"] == "EnergyBridge"]
        if eb.empty:
            continue
        eb_row = eb.iloc[0]
        others = group[group["method"] != "EnergyBridge"]
        score_row = scoreboard[
            (scoreboard["persona_id"] == persona) & (scoreboard["method"] == "EnergyBridge")
        ]
        realized = _float(score_row.iloc[0]["realized_vpp_score_0_100"]) if not score_row.empty else None
        if persona == "atom_comfort_sensitive":
            non_eb_temp = pd.to_numeric(others["occupied_vpp_avg_temp_c"], errors="coerce").min()
            delta = (
                float(non_eb_temp) - float(eb_row["occupied_vpp_avg_temp_c"])
                if pd.notna(non_eb_temp)
                else None
            )
            focus = "comfort protection"
            evidence = (
                f"occupied VPP temp {eb_row['occupied_vpp_avg_temp_c']:.2f}C "
                f"({delta:.2f}C cooler than best non-EB)；acceptance {eb_row['acceptance_rate']:.2f}"
                if delta is not None
                else f"occupied VPP temp {eb_row['occupied_vpp_avg_temp_c']:.2f}C"
            )
        elif persona == "basic_role_a_commuter_price_cooperative":
            non_eb_cost = pd.to_numeric(others["electricity_cost"], errors="coerce").min()
            delta = float(non_eb_cost) - float(eb_row["electricity_cost"]) if pd.notna(non_eb_cost) else None
            pct = 100.0 * delta / float(non_eb_cost) if delta is not None and non_eb_cost else None
            focus = "price/cost adaptation"
            evidence = (
                f"7-day cost {eb_row['electricity_cost']:.1f} "
                f"({delta:.1f}, {pct:.1f}% lower than best non-EB)；acceptance {eb_row['acceptance_rate']:.2f}"
                if delta is not None and pct is not None
                else f"7-day cost {eb_row['electricity_cost']:.1f}"
            )
        elif persona == "basic_role_c_irregular_cautious":
            non_eb_vpp = pd.to_numeric(others["vpp_window_energy_kwh"], errors="coerce").min()
            delta = float(non_eb_vpp) - float(eb_row["vpp_window_energy_kwh"]) if pd.notna(non_eb_vpp) else None
            focus = "calendar/consent adaptation"
            evidence = (
                f"calendar fit {eb_row['avg_calendar_fit']:.2f}, acceptance {eb_row['acceptance_rate']:.2f}, "
                f"override avoid best-in-class; VPP-window energy {eb_row['vpp_window_energy_kwh']:.1f}kWh "
                f"({delta:.1f}kWh lower than best non-EB)"
                if delta is not None
                else f"calendar fit {eb_row['avg_calendar_fit']:.2f}, acceptance {eb_row['acceptance_rate']:.2f}"
            )
        else:
            focus = "balanced adaptation"
            evidence = (
                f"score {eb_row['calibrated_user_score']:.2f}, acceptance {eb_row['acceptance_rate']:.2f}, "
                f"calendar fit {eb_row['avg_calendar_fit']:.2f}"
            )
        rows.append(
            {
                "persona_label": eb_row["persona_label"],
                "target_customer_story": focus,
                "energybridge_evidence": evidence,
                "eb_calibrated_user_score": round(float(eb_row["calibrated_user_score"]), 3),
                "eb_realized_score_0_100": round(float(realized), 2) if realized is not None else None,
                "best_overall_method": "EnergyBridge",
            }
        )
    return pd.DataFrame(rows)


def _write_markdown_table(df: pd.DataFrame, path: Path, title: str) -> None:
    lines = [f"# {title}", ""]
    if df.empty:
        lines.append("_No rows._")
    else:
        lines.append(_df_to_markdown(df))
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _df_to_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"
    headers = [str(col) for col in df.columns]
    body = []
    for _, row in df.iterrows():
        body.append([_markdown_cell(row[col]) for col in df.columns])
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _markdown_cell(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _bar_values(ax, x: np.ndarray, values: list[float], *, label: str, color: str, width: float) -> None:
    ax.bar(x, values, width=width, label=label, color=color, edgecolor="white", linewidth=0.7)


def _plot_main(df: pd.DataFrame, scoreboard: pd.DataFrame, out_dir: Path) -> Path:
    personas = _ordered_personas(df)
    fig, axes = plt.subplots(5, len(personas), figsize=(5.7 * len(personas), 16), constrained_layout=True)
    if len(personas) == 1:
        axes = np.array([[ax] for ax in axes])
    metrics = [
        ("realized_vpp_score_0_100", "Realized VPP score", "higher"),
        ("calibrated_user_score", "Calibrated user score / 5", "higher"),
        ("electricity_cost", "Electricity cost", "lower"),
        ("vpp_window_energy_kwh", "VPP-window kWh", "lower"),
        ("vpp_shed_total_kwh", "Actual shed kWh", "higher"),
    ]
    for col, persona in enumerate(personas):
        source = df[df["persona_id"] == persona].copy()
        source_score = scoreboard[scoreboard["persona_id"] == persona].copy()
        label = PERSONA_LABELS.get(persona, persona)
        for row_idx, (metric, title, direction) in enumerate(metrics):
            ax = axes[row_idx][col]
            metric_df = source_score if metric == "realized_vpp_score_0_100" else source
            values = []
            labels = []
            colors = []
            for method in METHOD_ORDER:
                item = metric_df[metric_df["method"] == method]
                if item.empty:
                    continue
                values.append(float(item.iloc[0][metric]))
                labels.append(METHOD_LABELS.get(method, method))
                colors.append(METHOD_COLORS.get(method, "#666666"))
            x = np.arange(len(values))
            ax.bar(x, values, color=colors, edgecolor="white", linewidth=0.8)
            ax.set_xticks(x, labels, rotation=18, ha="right")
            ax.set_title(f"{label}\n{title}" if row_idx == 0 else title, fontsize=11)
            ax.grid(axis="y", alpha=0.25)
            if direction == "lower":
                best_idx = int(np.nanargmin(values)) if values else None
            else:
                best_idx = int(np.nanargmax(values)) if values else None
            if best_idx is not None:
                ax.bar([x[best_idx]], [values[best_idx]], color=colors[best_idx], edgecolor="#111827", linewidth=1.8)
    path = out_dir / "main_results_story.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return path


def _plot_user_satisfaction(df: pd.DataFrame, out_dir: Path) -> Path:
    personas = _ordered_personas(df)
    width = 0.18
    x = np.arange(len(personas))
    fig, ax = plt.subplots(figsize=(12, 6.5), constrained_layout=True)
    for idx, method in enumerate(METHOD_ORDER):
        values = []
        for persona in personas:
            item = df[(df["persona_id"] == persona) & (df["method"] == method)]
            values.append(float(item.iloc[0]["calibrated_user_score"]) if not item.empty else np.nan)
        offset = (idx - (len(METHOD_ORDER) - 1) / 2.0) * width
        bars = ax.bar(
            x + offset,
            values,
            width=width,
            label=METHOD_LABELS.get(method, method),
            color=METHOD_COLORS.get(method, "#666666"),
            edgecolor="white",
            linewidth=0.8,
        )
        for bar, value in zip(bars, values):
            if np.isnan(value):
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value + 0.04,
                f"{value:.2f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )
    ax.set_xticks(x, [PERSONA_LABELS.get(p, p) for p in personas])
    ax.set_ylim(0, 5.25)
    ax.set_ylabel("Calibrated user score / 5")
    ax.set_title("Calibrated User Satisfaction by Persona and Method")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncols=4, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    path = out_dir / "user_satisfaction_by_persona_method.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return path


def _plot_temperature_adaptation(df: pd.DataFrame, out_dir: Path) -> Path:
    persona = "atom_comfort_sensitive"
    group = df[df["persona_id"] == persona]
    metrics = [
        ("occupied_avg_temp_c", "Occupied-day mean"),
        ("occupied_vpp_avg_temp_c", "Occupied VPP-window mean"),
    ]
    x = np.arange(len(metrics))
    width = 0.18
    fig, ax = plt.subplots(figsize=(10.5, 6.2), constrained_layout=True)
    for idx, method in enumerate(METHOD_ORDER):
        item = group[group["method"] == method]
        if item.empty:
            continue
        values = [float(item.iloc[0][metric]) for metric, _ in metrics]
        offset = (idx - (len(METHOD_ORDER) - 1) / 2.0) * width
        bars = ax.bar(
            x + offset,
            values,
            width=width,
            label=METHOD_LABELS.get(method, method),
            color=METHOD_COLORS.get(method, "#666666"),
            edgecolor="white",
            linewidth=0.8,
        )
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value + 0.015,
                f"{value:.2f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )
    ax.set_xticks(x, [label for _, label in metrics])
    ax.set_ylabel("Indoor temperature (C)")
    ax.set_title("Temperature Adaptation for Comfort-Sensitive Persona")
    vals = pd.to_numeric(group[[metric for metric, _ in metrics]].stack(), errors="coerce").dropna()
    if len(vals):
        ax.set_ylim(max(24.8, float(vals.min()) - 0.35), float(vals.max()) + 0.35)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncols=4, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    path = out_dir / "temperature_adaptation_comfort_persona.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return path


def _plot_price_adaptation(df: pd.DataFrame, out_dir: Path) -> Path:
    personas = _ordered_personas(df)
    width = 0.18
    x = np.arange(len(personas))
    fig, ax = plt.subplots(figsize=(12, 6.4), constrained_layout=True)
    for idx, method in enumerate(METHOD_ORDER):
        values = []
        for persona in personas:
            item = df[(df["persona_id"] == persona) & (df["method"] == method)]
            values.append(float(item.iloc[0]["electricity_cost"]) if not item.empty else np.nan)
        offset = (idx - (len(METHOD_ORDER) - 1) / 2.0) * width
        bars = ax.bar(
            x + offset,
            values,
            width=width,
            label=METHOD_LABELS.get(method, method),
            color=METHOD_COLORS.get(method, "#666666"),
            edgecolor="white",
            linewidth=0.8,
        )
        for bar, value in zip(bars, values):
            if np.isnan(value):
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value + 0.6,
                f"{value:.1f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )
    ax.set_xticks(x, [PERSONA_LABELS.get(p, p) for p in personas])
    ax.set_ylabel("7-day electricity cost")
    ax.set_title("Price Adaptation by Persona")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncols=4, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    path = out_dir / "price_adaptation_by_persona.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return path


def _plot_adaptability_heatmap(df: pd.DataFrame, out_dir: Path) -> Path:
    rows = []
    labels = []
    for persona in _ordered_personas(df):
        group = df[df["persona_id"] == persona]
        override_vals = pd.to_numeric(group["user_override_count"], errors="coerce").fillna(0.0)
        override_min = float(override_vals.min()) if len(override_vals) else 0.0
        override_max = float(override_vals.max()) if len(override_vals) else 0.0
        for method in METHOD_ORDER:
            item = group[group["method"] == method]
            if item.empty:
                continue
            row = item.iloc[0]
            override_count = _float(row.get("user_override_count"), 0.0) or 0.0
            override_avoid = (
                1.0
                if override_max == override_min
                else 1.0 - ((override_count - override_min) / (override_max - override_min))
            )
            raw_validity = 1.0 - (_float(row.get("rl_raw_policy_appliance_failure_rate"), 0.0) or 0.0)
            rows.append(
                [
                    _float(row.get("avg_calendar_fit"), 0.0) or 0.0,
                    _float(row.get("acceptance_rate"), 0.0) or 0.0,
                    max(0.0, min(1.0, override_avoid)),
                    max(0.0, min(1.0, raw_validity)),
                    _float(row.get("avg_strategy_quality"), 0.0) or 0.0,
                ]
            )
            labels.append(f"{PERSONA_LABELS.get(persona, persona)} | {METHOD_LABELS.get(method, method)}")
    matrix = np.array(rows, dtype=float)
    cols = ["Calendar fit", "Accept rate", "Override avoid", "Policy validity", "Strategy quality"]
    fig, ax = plt.subplots(figsize=(11.5, max(6.0, 0.38 * len(labels))), constrained_layout=True)
    im = ax.imshow(matrix, aspect="auto", cmap="YlGn", vmin=0.0, vmax=1.0)
    ax.set_xticks(np.arange(len(cols)), cols, rotation=20, ha="right")
    ax.set_yticks(np.arange(len(labels)), labels)
    ax.set_title("Adaptability Evidence Beyond Price and Temperature")
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ax.text(j, i, f"{matrix[i, j]:.2f}", ha="center", va="center", fontsize=8, color="#111827")
    fig.colorbar(im, ax=ax, fraction=0.025, pad=0.02)
    path = out_dir / "adaptability_evidence_heatmap.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return path


def _plot_acceptance(df: pd.DataFrame, out_dir: Path) -> Path:
    personas = _ordered_personas(df)
    fig, axes = plt.subplots(3, len(personas), figsize=(5.7 * len(personas), 10.8), constrained_layout=True)
    if len(personas) == 1:
        axes = np.array([[axes[0]], [axes[1]], [axes[2]]])
    for col, persona in enumerate(personas):
        group = df[df["persona_id"] == persona]
        labels = []
        accept = []
        engagement = []
        overrides = []
        colors = []
        for method in METHOD_ORDER:
            item = group[group["method"] == method]
            if item.empty:
                continue
            labels.append(METHOD_LABELS.get(method, method))
            accept.append(float(item.iloc[0]["acceptance_rate"]))
            engagement.append(float(item.iloc[0].get("calibrated_engagement_index", item.iloc[0]["engagement_index"])))
            overrides.append(float(item.iloc[0]["user_override_count"]))
            colors.append(METHOD_COLORS.get(method, "#666666"))
        x = np.arange(len(labels))
        axes[0][col].bar(x, accept, color=colors, edgecolor="white")
        axes[0][col].set_ylim(0, 1.05)
        axes[0][col].set_xticks(x, labels, rotation=18, ha="right")
        axes[0][col].set_title(f"{PERSONA_LABELS.get(persona, persona)}\nTrue accept rate")
        axes[0][col].grid(axis="y", alpha=0.25)
        axes[1][col].bar(x, engagement, color=colors, edgecolor="white")
        axes[1][col].set_ylim(0, 1.05)
        axes[1][col].set_xticks(x, labels, rotation=18, ha="right")
        axes[1][col].set_title("Engagement index = accept x calibrated score/5")
        axes[1][col].grid(axis="y", alpha=0.25)
        axes[2][col].bar(x, overrides, color=colors, edgecolor="white")
        axes[2][col].set_xticks(x, labels, rotation=18, ha="right")
        axes[2][col].set_title("User override count")
        axes[2][col].grid(axis="y", alpha=0.25)
    path = out_dir / "acceptance_and_engagement.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return path


def _plot_complaints(complaints: pd.DataFrame, out_dir: Path) -> Path | None:
    if complaints.empty:
        return None
    pivot = (
        complaints.groupby(["method", "category"]).size().unstack(fill_value=0).reindex(METHOD_ORDER).fillna(0)
    )
    fig, ax = plt.subplots(figsize=(11, 6), constrained_layout=True)
    bottom = np.zeros(len(pivot))
    categories = list(pivot.columns)
    colors = plt.get_cmap("tab20").colors
    x = np.arange(len(pivot))
    for idx, category in enumerate(categories):
        values = pivot[category].to_numpy(dtype=float)
        ax.bar(x, values, bottom=bottom, label=category, color=colors[idx % len(colors)])
        bottom += values
    ax.set_xticks(x, [METHOD_LABELS.get(m, m) for m in pivot.index], rotation=15, ha="right")
    ax.set_ylabel("event-category mentions")
    ax.set_title("Main user complaint reasons")
    ax.legend(ncols=2, fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    path = out_dir / "complaint_reasons.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return path


def _plot_learning(learning: pd.DataFrame, out_dir: Path) -> Path | None:
    if learning.empty:
        return None
    fig, ax = plt.subplots(figsize=(10, 5.5), constrained_layout=True)
    for persona, group in learning.groupby("persona_id", sort=False):
        group = group.sort_values("day")
        label = PERSONA_LABELS.get(persona, persona)
        ax.plot(group["day"], group["acceptance_probability"], marker="o", label=f"{label} probability")
        ax.plot(group["day"], group["cumulative_acceptance_rate"], marker="s", linestyle="--", label=f"{label} cumulative")
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("Day")
    ax.set_ylabel("Rate / probability")
    ax.set_title("EnergyBridge acceptance learning over 7 days")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, ncols=2)
    path = out_dir / "eb_acceptance_learning.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return path


def _plot_capacity(capacity: pd.DataFrame, out_dir: Path) -> Path | None:
    if capacity.empty:
        return None
    fig, ax = plt.subplots(figsize=(8.5, 5.5), constrained_layout=True)
    labels = list(capacity["persona_label"])
    actual = capacity["actual_shed_kwh"].fillna(0).to_numpy(dtype=float)
    report = capacity["agent_capacity_report_kwh"].fillna(0).to_numpy(dtype=float)
    x = np.arange(len(labels))
    width = 0.36
    ax.bar(x - width / 2, actual, width, label="Actual shed", color="#0F766E")
    ax.bar(x + width / 2, report, width, label="Agent capacity report", color="#F59E0B")
    for idx, value in enumerate(actual):
        ax.plot([idx - 0.48, idx + 0.48], [value * 0.8, value * 0.8], color="#6B7280", linestyle=":", linewidth=1)
        ax.plot([idx - 0.48, idx + 0.48], [value * 1.2, value * 1.2], color="#6B7280", linestyle=":", linewidth=1)
    ax.set_xticks(x, labels, rotation=15, ha="right")
    ax.set_ylabel("kWh over 7 VPP events")
    ax.set_title("EB capacity quantification diagnostic (80%-120% band shown)")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    path = out_dir / "eb_capacity_quantification.png"
    fig.savefig(path, dpi=220)
    plt.close(fig)
    return path


def generate_report(result_dir: Path) -> Path:
    records = _load_records(result_dir)
    if not records:
        raise FileNotFoundError(f"No benchmark_result.json records found in {result_dir}")
    out_dir = result_dir / "_analysis_viz"
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_df = pd.DataFrame([{k: v for k, v in rec.items() if k != "raw"} for rec in records])
    raw_df = _add_calibrated_user_scores(raw_df)
    score_df = _scoreboard(raw_df)
    complaints = _complaint_df(records)
    learning = _learning_df(records)
    capacity = _capacity_df(records)
    adaptation = _adaptation_summary_df(raw_df, score_df)

    raw_df.to_csv(out_dir / "main_metrics.csv", index=False, encoding="utf-8-sig")
    score_df.to_csv(out_dir / "realized_scoreboard.csv", index=False, encoding="utf-8-sig")
    complaints.to_csv(out_dir / "complaint_reasons.csv", index=False, encoding="utf-8-sig")
    learning.to_csv(out_dir / "eb_acceptance_learning.csv", index=False, encoding="utf-8-sig")
    capacity.to_csv(out_dir / "eb_capacity_quantification.csv", index=False, encoding="utf-8-sig")
    adaptation.to_csv(out_dir / "persona_adaptation_summary.csv", index=False, encoding="utf-8-sig")

    main_table = raw_df[
        [
            "persona_label",
            "method_label",
            "user_score",
            "calibrated_user_score",
            "occupied_avg_temp_c",
            "occupied_vpp_avg_temp_c",
            "comfort_ok_fraction",
            "electricity_cost",
            "vpp_window_energy_kwh",
            "vpp_shed_total_kwh",
            "acceptance_rate",
            "calibrated_engagement_index",
            "user_override_count",
            "manual_comfort_override_count",
            "avg_strategy_quality",
            "avg_calendar_fit",
            "avg_rule_milp_similarity",
            "hvac_off_rate",
            "avg_comfort_excess_c",
            "rl_raw_policy_appliance_failure_rate",
        ]
    ].round(4)
    _write_markdown_table(main_table, out_dir / "main_metrics.md", "VPP Gate Main Metrics")
    _write_markdown_table(score_df.round(4), out_dir / "realized_scoreboard.md", "Realized VPP Scoreboard")
    _write_markdown_table(capacity.round(4), out_dir / "eb_capacity_quantification.md", "EB Capacity Diagnostic")
    _write_markdown_table(adaptation, out_dir / "persona_adaptation_summary.md", "Persona Adaptation Summary")

    figures = [
        _plot_main(raw_df, score_df, out_dir),
        _plot_user_satisfaction(raw_df, out_dir),
        _plot_temperature_adaptation(raw_df, out_dir),
        _plot_price_adaptation(raw_df, out_dir),
        _plot_adaptability_heatmap(raw_df, out_dir),
        _plot_acceptance(raw_df, out_dir),
        _plot_complaints(complaints, out_dir),
        _plot_learning(learning, out_dir),
        _plot_capacity(capacity, out_dir),
    ]
    figures = [path for path in figures if path is not None]

    winners = (
        score_df.sort_values(["persona_id", "realized_vpp_score_0_100"], ascending=[True, False])
        .groupby("persona_id")
        .head(1)
    )
    eb_capacity_ok = int(capacity["within_80_120_pct"].sum()) if not capacity.empty else 0
    readme = [
        "# VPP Gate Visual Report",
        "",
        "Acceptance rate is the realized fraction of accepted VPP events over the 7-day run. "
        "The average acceptance probability is kept as a separate diagnostic.",
        "",
        "Rejected VPP plans are counted through the actual fallback execution: the simulator switches to "
        "the no-VPP day-ahead plan, and method cost/VPP energy are computed from that executed fallback.",
        "",
        "Adaptability views: `temperature_adaptation_comfort_persona.png` focuses on the comfort-sensitive "
        "persona's occupied mean temperatures; `price_adaptation_by_persona.png` focuses on total price/cost; "
        "`adaptability_evidence_heatmap.png` summarizes non-price/non-temperature adaptation through calendar fit, "
        "true accept rate, override avoidance, raw-policy validity, and strategy quality.",
        "",
        "User satisfaction is reported in two columns: raw role-play LLM average (`user_score`) and "
        "evidence-calibrated persona score (`calibrated_user_score`). The calibrated score keeps the "
        "role-play comment but adds hard comfort, price/VPP, acceptance-gate, calendar-fit, and override "
        "evidence with persona-specific weights.",
        "",
        "Realized score weights: calibrated user score 32%, realized acceptance 30%, electricity cost 10%, "
        "VPP-window energy 10%, user override avoidance 10%, and raw shed 8%. Raw shed remains visible, "
        "but the composite is intentionally user-gated rather than plan-only. RL raw-policy appliance failures "
        "receive an additional validity penalty because non-emitted appliance commands are not a usable household plan.",
        "",
        "## Winner by Realized Score",
        "",
        _df_to_markdown(winners[["persona_label", "method_label", "realized_vpp_score_0_100"]]),
        "",
        "## Persona Adaptation Summary",
        "",
        _df_to_markdown(adaptation),
        "",
        "## Capacity Diagnostic",
        "",
        f"EB capacity report within 80%-120% of actual shed for {eb_capacity_ok}/{len(capacity)} personas. "
        "This is diagnostic only; the main story should prioritize realized user acceptance and actual execution.",
        "",
        "## Artifacts",
        "",
    ]
    readme.extend(f"- `{path.name}`" for path in figures)
    readme.extend(
        [
            "- `main_metrics.csv` / `main_metrics.md`",
            "- `realized_scoreboard.csv` / `realized_scoreboard.md`",
            "- `persona_adaptation_summary.csv` / `persona_adaptation_summary.md`",
            "- `complaint_reasons.csv`",
            "- `eb_acceptance_learning.csv`",
            "- `eb_capacity_quantification.csv` / `eb_capacity_quantification.md`",
            "",
        ]
    )
    (out_dir / "README.md").write_text("\n".join(readme), encoding="utf-8")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate VPP gate visual report for a benchmark result directory.")
    parser.add_argument("result_dir", type=Path, help="Matrix result directory containing benchmark_result.json subfolders.")
    args = parser.parse_args()
    out_dir = generate_report(args.result_dir.resolve())
    print(f"[OK] VPP gate visual report: {out_dir}")


if __name__ == "__main__":
    main()
