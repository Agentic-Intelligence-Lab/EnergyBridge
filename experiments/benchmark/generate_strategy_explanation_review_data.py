#!/usr/bin/env python3
"""Generate review samples for VPP strategy explanations without running EnergyPlus."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


BENCHMARK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCHMARK_DIR.parent.parent
PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "benchmark_results" / "reports" / "strategy_explanation_review"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.benchmark.strategy_explanations import (  # noqa: E402
    build_vpp_strategy_explanation,
    write_strategy_explanation_artifacts,
)


DEFAULT_PERSONAS = [
    "basic_role_a_commuter_price_cooperative",
    "basic_role_b_home_comfort_gated",
    "basic_role_c_irregular_cautious",
    "basic_role_d_commuter_ideal_dr",
    "basic_role_e_caregiver_low_dr",
    "basic_role_f_commuter_ev_optimizer",
]


def _load_persona(persona_arg: str) -> dict:
    path = Path(persona_arg)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    candidate = PERSONA_DIR / f"{persona_arg}.json"
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"Persona not found: {persona_arg}")


def _sample_setpoint(persona: dict) -> float:
    ac = (persona.get("appliances") or {}).get("ac") or {}
    tags = persona.get("tags") or {}
    ac_max = float(ac.get("setpoint_preferred_max_c", 26.0) or 26.0)
    ac_min = float(ac.get("setpoint_preferred_min_c", 24.0) or 24.0)
    if tags.get("comfort") == "temp_tolerant":
        return round(ac_max, 1)
    if tags.get("comfort") == "temp_sensitive" or tags.get("control") in {"low_auto_accept", "confirm_required"}:
        return round(min(ac_max, max(ac_min, ac_max)), 1)
    return round(min(ac_max, 26.0), 1)


def _overlaps_window(start_h: float, duration_h: float, event_start_h: float, event_end_h: float) -> bool:
    return start_h < event_end_h and (start_h + duration_h) > event_start_h


def _sample_actions(persona: dict, event: dict) -> dict:
    cfg = persona.get("appliances") or {}
    event_start_h = float(event.get("trigger_h", 18.0)) % 24.0
    event_end_h = float(event.get("end_h", 19.0)) % 24.0
    actions: dict = {}
    for name in ("washer", "dishwasher", "dryer"):
        dev = cfg.get(name) or {}
        if not dev.get("present"):
            continue
        duration = float(dev.get("duration_h", 1.0) or 1.0)
        preferred = float(dev.get("preferred_h", max(event_end_h, 19.0)) or max(event_end_h, 19.0))
        if dev.get("dr_adjustable", dev.get("shiftable", True)) is not False:
            start_h = preferred
            if _overlaps_window(start_h, duration, event_start_h, event_end_h):
                start_h = event_end_h
            latest_start = float(dev.get("latest_h", 23.0) or 23.0) - duration
            if start_h > latest_start:
                start_h = max(0.0, event_start_h - duration - 0.5)
            actions[f"{name}_start_h"] = round(start_h, 1)
        else:
            actions[f"{name}_start_h"] = round(preferred, 1)
        actions[f"{name}_skip"] = False

    wh = cfg.get("water_heater") or {}
    if wh.get("present"):
        if wh.get("dr_adjustable", True) is not False:
            preheat_end = max(0.0, event_start_h - 1.0)
            preheat_start = max(0.0, preheat_end - 2.0)
            actions["water_heater_preheat_start_h"] = round(preheat_start, 1)
            actions["water_heater_preheat_end_h"] = round(preheat_end, 1)
            actions["water_heater_preheat_temp_c"] = 55.0
            actions["water_heater_preheat"] = True
        else:
            actions["water_heater_preheat_start_h"] = wh.get("pre_heat_window_start_h")
            actions["water_heater_preheat_end_h"] = wh.get("pre_heat_window_end_h")
            actions["water_heater_preheat_temp_c"] = 50.0
            actions["water_heater_preheat"] = True

    ev = cfg.get("ev") or {}
    if ev.get("present"):
        arrival = float(ev.get("arrival_h", event_end_h) or event_end_h)
        charge_start = max(arrival, event_end_h)
        actions["ev_mode"] = "smart"
        actions["ev_charge_start_h"] = round(charge_start, 1)
        actions["ev_charge_end_h"] = 23.9

    return actions


def _sample_events(start_h: float, duration_h: float) -> list[dict]:
    target_kw_by_day = [0.5, 1.0, 1.5]
    events = []
    for day, target_kw in enumerate(target_kw_by_day, start=1):
        trigger = (day - 1) * 24.0 + start_h
        events.append(
            {
                "id": f"review_vpp{day}",
                "trigger_h": trigger,
                "end_h": trigger + duration_h,
                "day": day,
                "target_shed_kw": target_kw,
            }
        )
    return events


def _record_for(persona: dict, event: dict, event_index: int, city: str, method: str) -> dict:
    demand_context = {
        "target_shed_kw": event.get("target_shed_kw"),
        "target_shed_kwh": float(event.get("target_shed_kw", 0.0) or 0.0)
        * max(0.0, float(event.get("end_h", 0.0)) - float(event.get("trigger_h", 0.0))),
    }
    explanation = build_vpp_strategy_explanation(
        persona_config=persona,
        appliance_config=persona.get("appliances", {}),
        event=event,
        setpoint_c=_sample_setpoint(persona),
        reason="review sample",
        appliance_actions=_sample_actions(persona, event),
        demand_context=demand_context,
        capacity_context={},
        method=method,
        city=city,
        source="review_sample_generator",
    )
    return {
        "persona_id": persona.get("id", ""),
        "persona_display_name": persona.get("display_name") or persona.get("name", ""),
        "city": city,
        "method": method,
        "event_index": event_index,
        "event_id": event.get("id", ""),
        "day": event.get("day", event_index),
        "score": "",
        "comfort_score": "",
        "energy_score": "",
        "vpp_score": "",
        "reason": "review sample",
        "natural_language": explanation.get("natural_language", ""),
        "why_request": explanation.get("why_request", ""),
        "review_dimensions": explanation.get("review_dimensions", {}),
        "strategy_explanation": explanation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate VPP strategy explanation review data.")
    parser.add_argument(
        "--personas",
        nargs="*",
        default=DEFAULT_PERSONAS,
        help="Persona IDs or JSON paths. Defaults to the six approved basic roles.",
    )
    parser.add_argument("--city", default="Germany", help="City label to write into review records.")
    parser.add_argument("--method", default="EnergyBridge", help="Method label to write into review records.")
    parser.add_argument("--vpp-start-hour", type=float, default=18.0, help="Review sample VPP start hour.")
    parser.add_argument("--vpp-duration-hours", type=float, default=1.0, help="Review sample VPP duration.")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for strategy_explanations.jsonl/csv/md.",
    )
    parser.add_argument(
        "--prefix",
        default="strategy_explanations",
        help="Output filename prefix.",
    )
    args = parser.parse_args()

    personas = [_load_persona(item) for item in args.personas]
    events = _sample_events(args.vpp_start_hour, args.vpp_duration_hours)
    records = []
    for persona in personas:
        for idx, event in enumerate(events, start=1):
            records.append(_record_for(persona, event, idx, args.city, args.method))

    output_dir = Path(args.output_dir)
    paths = write_strategy_explanation_artifacts(records, output_dir, prefix=args.prefix)
    manifest = {
        "generated_on": date.today().isoformat(),
        "records": len(records),
        "personas": [persona.get("id", "") for persona in personas],
        "city": args.city,
        "method": args.method,
        "artifacts": paths,
    }
    manifest_path = output_dir / f"{args.prefix}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Generated {len(records)} strategy explanation records.")
    for label, path in paths.items():
        print(f"{label}: {path}")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
