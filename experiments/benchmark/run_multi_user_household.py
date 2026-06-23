#!/usr/bin/env python3
"""Run one fixed multi-user household with independent member role-play.

This runner intentionally keeps multi-user logic out of the single-persona
benchmark path. A household JSON fixes the member list, household relationship
prompt, and shared physical appliances. Each member keeps an independent
role-play context for pre-event strategy feedback and post-event scoring.
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_BENCH_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
DEFAULT_BENCHMARK_RESULTS_DIR = _PROJECT_ROOT / "benchmark_results"
load_dotenv(_PROJECT_ROOT / ".env")

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))

import family_runner as fr
import user_pref_scorer as _ups
from energybridge.data.vpp_events import describe_vpp_events, load_vpp_events_config, make_daily_vpp_events
from energybridge.roleplay.households import (
    list_household_ids,
    load_household_config,
    load_household_member_personas,
    merge_member_calendars,
)
from run_persona_json import (
    ENERGYBRIDGE_METHOD_ID,
    _canonical_method,
    _controller_method,
    _default_days_for_city,
    _default_start_date_for_city,
    _prepare_run_assets,
)


MEMBER_SERVICE_KEYS = ("washer", "dishwasher", "dryer", "water_heater", "ev")


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value).strip("_").lower()


def _default_output_dir(household_id: str, method: str, city: str, days: int, horizon: int) -> Path:
    method = _canonical_method(method)
    token = f"{method}_H{int(horizon)}" if method in ("mpc_dynamic", "mpc_ep") else method
    return DEFAULT_BENCHMARK_RESULTS_DIR / date.today().isoformat() / (
        f"{_slug(household_id)}_{token}_{city.lower()}_{int(days)}days"
    )


def _household_system_prompt(household: dict[str, Any]) -> str:
    base = str(((household.get("llm_prompts") or {}).get("system_prompt")) or "")
    appliance_names = ", ".join(MEMBER_SERVICE_KEYS)
    addendum = (
        "This benchmark household physically owns the full shared appliance set: "
        f"{appliance_names}. Do not infer missing appliances from any individual member JSON; "
        "individual persona JSON files are used only for role-play preference, comments, choices, "
        "and scoring. Shared appliance feasibility is defined by the household JSON."
    )
    return f"{base}\n\n{addendum}".strip()


def _build_physical_household_persona(
    household: dict[str, Any],
    member_personas: list[dict[str, Any]],
    *,
    days: int,
) -> dict[str, Any]:
    """Persona-like object used only for EP occupancy and shared appliances."""
    prompt = _household_system_prompt(household)
    return {
        "schema_version": "multi_user_physical_household_v1",
        "id": household["id"],
        "display_name": household.get("display_name", household["id"]),
        "description": household.get("description", ""),
        "tags": dict(household.get("tags") or {}),
        "schedule": {
            "occupancy_pattern": "multi_user_overlay",
            "calendar_merge_policy": "occupied_if_any_member_home",
            "member_count": len(member_personas),
        },
        "preferences": dict(household.get("preferences") or {}),
        "appliances": dict(household.get("appliances") or {}),
        "members": [
            {
                "member_id": p.get("household_member", {}).get("member_id", p.get("id")),
                "persona_id": p.get("id"),
                "display_name": p.get("display_name", p.get("id")),
                "household_role": p.get("household_member", {}).get("household_role", ""),
            }
            for p in member_personas
        ],
        "llm_prompts": {
            "system_prompt": prompt,
            "agent_context": prompt,
        },
        "calendar": merge_member_calendars(household, member_personas, days=days),
        "meta": {
            "persona_type": "multi_user_household_independent_roleplay",
            "household_source_path": household.get("_source_path", ""),
            "roleplay_policy": "each_member_independent_choice_and_score",
            "calendar_merge_policy": "union_home_occupancy_max",
            "appliance_config_policy": household.get("appliance_config_policy", "maximal_shared_device_set"),
            "scoring_policy": {
                "type": "independent_member_scores_mean",
                "member_score_aggregation": "arithmetic_mean",
                "preserve_member_comments": True,
            },
        },
    }


class IndependentMemberRoleplay:
    """Maintain independent member memories and aggregate only at the interface."""

    def __init__(
        self,
        household: dict[str, Any],
        member_personas: list[dict[str, Any]],
        *,
        max_memory_items: int = 10,
    ) -> None:
        self.household = household
        self.household_prompt = _household_system_prompt(household)
        self.member_personas = [copy.deepcopy(p) for p in member_personas]
        self.max_memory_items = max(1, int(max_memory_items))
        self.memory: dict[str, list[dict[str, str]]] = {
            self._member_key(p): [] for p in self.member_personas
        }
        self.transcripts: dict[str, Any] = {
            "strategy": {},
            "strategy_aggregate": {},
            "score": {},
            "score_aggregate": {},
        }

    @staticmethod
    def _member_key(persona: dict[str, Any]) -> str:
        return str(persona.get("household_member", {}).get("member_id") or persona.get("id"))

    def _member_name(self, persona: dict[str, Any]) -> str:
        member = persona.get("household_member", {}) or {}
        role = member.get("household_role", "")
        name = persona.get("display_name", persona.get("id", self._member_key(persona)))
        return f"{self._member_key(persona)} ({name}; {role})" if role else f"{self._member_key(persona)} ({name})"

    def _persona_with_context(self, persona: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(persona)
        prompts = dict(out.get("llm_prompts") or {})
        original = str(prompts.get("system_prompt", ""))
        member = out.get("household_member", {}) or {}
        memory_lines = []
        for item in self.memory.get(self._member_key(persona), [])[-self.max_memory_items:]:
            memory_lines.append(f"- {item.get('label', '')}: {item.get('text', '')}")
        context = [
            "[Multi-user household context]",
            self.household_prompt,
            f"Your household member role: {member.get('household_role', '')}",
            "You speak and score independently as this member. Do not average your view with others.",
            "Assume the physical household owns AC, washer, dryer, dishwasher, water heater, EV, and refrigerator.",
        ]
        if memory_lines:
            context.append("[Your own past-event memory]")
            context.extend(memory_lines)
        prompts["system_prompt"] = "\n\n".join(part for part in [original, "\n".join(context)] if part)
        out["llm_prompts"] = prompts
        return out

    def _remember(self, persona: dict[str, Any], label: str, text: str) -> None:
        key = self._member_key(persona)
        self.memory.setdefault(key, []).append({"label": label, "text": text[:500]})
        self.memory[key] = self.memory[key][-self.max_memory_items:]

    def choose_strategy(
        self,
        *,
        orig_get_pref,
        building: str,
        event_index: int,
        vpp_context: dict,
        past_events: list,
        human_mode: bool = False,
    ):
        from user_pref_scorer import StrategyPreference

        member_entries: list[dict[str, Any]] = []
        print(f"\n  {'='*62}")
        print(f"  [Multi-user independent strategy] event {event_index}: each member chooses separately")
        print(f"  {'='*62}")
        for persona in self.member_personas:
            member_persona = self._persona_with_context(persona)
            pref = orig_get_pref(
                building,
                event_index,
                vpp_context,
                past_events,
                persona=member_persona,
                human_mode=human_mode,
            )
            trace = dict(getattr(pref, "strategy_trace", {}) or {})
            selected = trace.get("selected_strategy") or {}
            entry = {
                "member_id": self._member_key(persona),
                "member_name": self._member_name(persona),
                "persona_id": persona.get("id"),
                "selected_strategy": selected,
                "preference_text": str(pref),
                "calendar_context": trace.get("calendar_context", {}),
            }
            member_entries.append(entry)
            choice = selected.get("id") or selected.get("label") or "custom"
            print(f"  [Member Strategy] {entry['member_name']} -> {choice}: {str(pref)[:100]}")

        aggregate = self._synthesize_agent_feedback(event_index, vpp_context, member_entries)
        self.transcripts["strategy"][str(event_index)] = member_entries
        self.transcripts["strategy_aggregate"][str(event_index)] = aggregate
        for entry, persona in zip(member_entries, self.member_personas):
            selected = entry.get("selected_strategy") or {}
            self._remember(
                persona,
                f"event {event_index} strategy choice",
                f"{selected.get('id', selected.get('label', 'custom'))}: {entry.get('preference_text', '')}",
            )
        return StrategyPreference(
            aggregate["agent_feedback"],
            {
                "event_index": event_index,
                "source": "multi_user_independent_member_choices",
                "member_choices": member_entries,
                "selected_strategy": {
                    "id": "multi_user_feedback",
                    "label": "Independent member feedback summary",
                    "source": "multi_user_synthesis",
                    "preference_text": aggregate["agent_feedback"],
                },
                "returned_user_pref": aggregate["agent_feedback"],
            },
        )

    def _synthesize_agent_feedback(self, event_index: int, vpp_context: dict, entries: list[dict[str, Any]]) -> dict:
        lines = [
            f"Household event {event_index}; VPP window {vpp_context.get('trigger_h')} to {vpp_context.get('end_h')}.",
            "Every listed household member has independently commented and selected a preference.",
            "Summarize these independent preferences into concise actionable feedback for an EnergyBridge control agent.",
            "The home owns all shared appliances: washer, dryer, dishwasher, water heater, EV, and refrigerator.",
            "Do not discard minority concerns; preserve conflicts and hard constraints.",
        ]
        for entry in entries:
            selected = entry.get("selected_strategy") or {}
            lines.append(
                f"- {entry['member_name']}: selected {selected.get('id', selected.get('label', 'custom'))}; "
                f"preference={entry.get('preference_text', '')}"
            )
        fallback = (
            "Multi-user feedback: "
            + " | ".join(
                f"{e['member_id']} says {e.get('preference_text', '')[:120]}" for e in entries
            )
        )
        try:
            from energybridge.llm.client import LLMClient

            sys_prompt = (
                "You summarize independent household member preferences for a home energy control agent. "
                "Return JSON only: {\"agent_feedback\": \"<=140 words\", \"conflicts\": [\"...\"], "
                "\"hard_constraints\": [\"...\"]}."
            )
            resp = LLMClient().chat_with_metrics(
                sys_prompt,
                "\n".join(lines),
                max_retries=2,
                retry_base_delay=1.0,
            )
            text = resp["text"].strip()
            if text.startswith("```"):
                text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("```")).strip()
            data = json.loads(text)
            data["agent_feedback"] = str(data.get("agent_feedback") or fallback)
            return data
        except Exception as exc:
            return {"agent_feedback": fallback, "conflicts": [], "hard_constraints": [], "fallback_error": str(exc)[:160]}

    def score_event(
        self,
        *,
        orig_score,
        building: str,
        method: str,
        mean_temp_c: float,
        pmv_ok_fraction: float,
        energy_kwh_per_day: float,
        agent_setpoint_c: float | None,
        event_index: int,
        user_preference_text: str,
        agent_reason: str,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        print(f"\n  {'='*62}")
        print(f"  [Multi-user independent scoring] event {event_index}: each member scores separately")
        print(f"  {'='*62}")
        member_scores: list[dict[str, Any]] = []
        for persona in self.member_personas:
            member_persona = self._persona_with_context(persona)
            result = orig_score(
                building=building,
                method=method,
                mean_temp_c=mean_temp_c,
                pmv_ok_fraction=pmv_ok_fraction,
                energy_kwh_per_day=energy_kwh_per_day,
                agent_setpoint_c=agent_setpoint_c,
                event_index=event_index,
                user_preference_text=user_preference_text,
                agent_reason=agent_reason,
                persona=member_persona,
                **kwargs,
            )
            entry = {
                "member_id": self._member_key(persona),
                "member_name": self._member_name(persona),
                "persona_id": persona.get("id"),
                "score": float(result.get("score", 3.0) or 3.0),
                "comfort_score": float(result.get("comfort_score", result.get("score", 3.0)) or 3.0),
                "energy_score": float(result.get("energy_score", result.get("score", 3.0)) or 3.0),
                "vpp_score": float(result.get("vpp_score", result.get("score", 3.0)) or 3.0),
                "label": result.get("label", ""),
                "comment": str(result.get("comment", "")),
                "source": result.get("source", ""),
            }
            member_scores.append(entry)
            self._remember(
                persona,
                f"event {event_index} outcome",
                f"score={entry['score']:.2f}; {entry['comment']}",
            )
            print(f"  [Member Score] {entry['member_name']} -> {entry['score']:.2f}/5 | {entry['comment'][:100]}")

        avg = _avg([m["score"] for m in member_scores])
        comfort_avg = _avg([m["comfort_score"] for m in member_scores])
        energy_avg = _avg([m["energy_score"] for m in member_scores])
        vpp_avg = _avg([m["vpp_score"] for m in member_scores])
        label = _label_for_score(avg)
        comments = "; ".join(f"{m['member_id']}={m['score']:.1f}: {m['comment'][:80]}" for m in member_scores)
        aggregate = {
            "score": round(avg, 3),
            "comfort_score": round(comfort_avg, 3),
            "energy_score": round(energy_avg, 3),
            "vpp_score": round(vpp_avg, 3),
            "label": label,
            "comment": f"mean of {len(member_scores)} independent member scores; {comments}",
            "source": "multi_user_independent_mean",
            "member_scores": member_scores,
            "member_score_min": round(min(m["score"] for m in member_scores), 3),
            "member_score_max": round(max(m["score"] for m in member_scores), 3),
            "member_score_std": round(statistics.pstdev([m["score"] for m in member_scores]), 3)
            if len(member_scores) > 1 else 0.0,
        }
        self.transcripts["score"][str(event_index)] = member_scores
        self.transcripts["score_aggregate"][str(event_index)] = aggregate
        return aggregate


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 3.0


def _label_for_score(score: float) -> str:
    rounded = max(1, min(5, int(round(score))))
    return {
        1: "very_dissatisfied",
        2: "dissatisfied",
        3: "neutral",
        4: "satisfied",
        5: "very_satisfied",
    }[rounded]


def _make_patched_functions(roleplay: IndependentMemberRoleplay, orig_get_pref, orig_score):
    def patched_get_user_preference_input(
        building,
        event_index,
        vpp_context,
        past_events,
        persona=None,
        log_path=None,
        human_mode=False,
    ):
        return roleplay.choose_strategy(
            orig_get_pref=orig_get_pref,
            building=building,
            event_index=event_index,
            vpp_context=vpp_context,
            past_events=past_events,
            human_mode=human_mode,
        )

    def patched_score_user_preference(
        building,
        method="agent",
        mean_temp_c=25.0,
        pmv_ok_fraction=0.8,
        energy_kwh_per_day=5.0,
        agent_setpoint_c=26.5,
        event_index=1,
        user_preference_text="",
        agent_reason="",
        persona=None,
        log_path=None,
        human_mode=False,
        **kwargs,
    ):
        return roleplay.score_event(
            orig_score=orig_score,
            building=building,
            method=method,
            mean_temp_c=mean_temp_c,
            pmv_ok_fraction=pmv_ok_fraction,
            energy_kwh_per_day=energy_kwh_per_day,
            agent_setpoint_c=agent_setpoint_c,
            event_index=event_index,
            user_preference_text=user_preference_text,
            agent_reason=agent_reason,
            kwargs={**kwargs, "log_path": log_path, "human_mode": human_mode},
        )

    return patched_get_user_preference_input, patched_score_user_preference


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a fixed multi-user household with independent member role-play.")
    parser.add_argument("--household", required=False, help="Household ID or JSON path.")
    parser.add_argument("--list-households", action="store_true")
    parser.add_argument("--method", default=ENERGYBRIDGE_METHOD_ID)
    parser.add_argument("--city", default="Tianjin", choices=["Tianjin", "Beijing", "Shanghai", "Germany"])
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--start-date", default="")
    parser.add_argument("--price-csv", default="")
    parser.add_argument("--vpp-start-hour", type=float, default=18.0)
    parser.add_argument("--vpp-duration-hours", type=float, default=1.0)
    parser.add_argument("--vpp-events-json", default="")
    parser.add_argument("--mpc-horizon", type=int, default=6)
    parser.add_argument("--idf", default="")
    parser.add_argument("--epw", default="")
    parser.add_argument("--weather-csv", default="")
    parser.add_argument("--regenerate-epw", action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument("--max-memory-items", type=int, default=10)
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.list_households:
        for household_id in list_household_ids():
            print(household_id)
        return
    if not args.household:
        raise SystemExit("--household is required unless --list-households is used")

    days = _default_days_for_city(args.city, args.days)
    start_date = _default_start_date_for_city(args.city, args.start_date.strip())
    method = _canonical_method(args.method)
    controller_method = _controller_method(method)
    mpc_horizon = max(1, int(args.mpc_horizon))
    vpp_start_hour = float(args.vpp_start_hour) % 24.0
    vpp_duration_hours = float(args.vpp_duration_hours)
    if vpp_duration_hours <= 0:
        raise SystemExit("--vpp-duration-hours must be > 0")
    if args.vpp_events_json:
        vpp_events = load_vpp_events_config(
            args.vpp_events_json,
            sim_days=days,
            default_start_h=vpp_start_hour,
            default_duration_h=vpp_duration_hours,
        )
        vpp_schedule_source = str(Path(args.vpp_events_json))
    else:
        if vpp_start_hour + vpp_duration_hours > 24.0:
            raise SystemExit("VPP windows crossing midnight are not supported yet; choose start+duration <= 24")
        vpp_events = make_daily_vpp_events(days, start_h=vpp_start_hour, duration_h=vpp_duration_hours)
        vpp_schedule_source = "daily_default"

    household_config = load_household_config(args.household)
    member_personas = load_household_member_personas(household_config)
    physical_persona = _build_physical_household_persona(household_config, member_personas, days=days)
    roleplay = IndependentMemberRoleplay(
        household_config,
        member_personas,
        max_memory_items=args.max_memory_items,
    )

    output_dir = Path(args.output) if args.output else _default_output_dir(
        physical_persona["id"],
        method,
        args.city,
        days,
        mpc_horizon,
    )
    idf_path, epw_path, price_profile = _prepare_run_assets(args, output_dir, days, start_date, physical_persona)

    print("=" * 78)
    print("  EnergyBridge Multi-User Household Benchmark")
    print(f"  Household : {physical_persona.get('display_name', physical_persona['id'])}")
    print(f"  ID        : {physical_persona['id']}")
    print(f"  Members   : {', '.join(m.get('member_id', m.get('persona_id', '')) for m in physical_persona['members'])}")
    print(f"  Role-play : independent member choices and independent member scoring mean")
    print(f"  City      : {args.city}")
    print(f"  Method    : {method}")
    print(f"  Days      : {days}")
    print(f"  VPP       : {describe_vpp_events(vpp_events)}")
    print(f"  IDF       : {idf_path}")
    print(f"  EPW       : {epw_path}")
    print(f"  PRICE     : {getattr(price_profile, 'source', '') or 'N/A'}")
    print(f"  OUTPUT    : {output_dir}")
    print("=" * 78)

    orig_get_pref = _ups.get_user_preference_input
    orig_score = _ups.score_user_preference
    patched_get, patched_score = _make_patched_functions(roleplay, orig_get_pref, orig_score)
    _ups.get_user_preference_input = patched_get
    _ups.score_user_preference = patched_score
    result = None
    try:
        result = fr.run_family_agent(
            idf_path=idf_path,
            epw_path=epw_path,
            user_pref=physical_persona["llm_prompts"]["agent_context"],
            appliance_config=physical_persona.get("appliances", {}),
            persona_config=physical_persona,
            output_dir=output_dir,
            weather_label=args.city.lower(),
            verbose=args.verbose,
            human_mode=False,
            method=controller_method,
            mpc_horizon_steps=mpc_horizon,
            sim_days=days,
            start_date=start_date or None,
            day_ahead_price_profile=price_profile,
            vpp_start_h=vpp_start_hour,
            vpp_duration_h=vpp_duration_hours,
            vpp_events_config=vpp_events,
            vpp_schedule_source=vpp_schedule_source,
        )
    finally:
        _ups.get_user_preference_input = orig_get_pref
        _ups.score_user_preference = orig_score

    if result is None:
        raise SystemExit("run_family_agent returned None")
    result.method = method
    result.multi_user_roleplay = {
        "aggregation": "independent_member_scores_mean",
        "household_id": physical_persona["id"],
        "members": physical_persona["members"],
        "transcripts": roleplay.transcripts,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "benchmark_result.json"
    result_path.write_text(json.dumps(result.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    meta_path = output_dir / "multi_user_roleplay.json"
    meta_path.write_text(
        json.dumps(
            {
                "household": physical_persona,
                "member_persona_ids": [p.get("id") for p in member_personas],
                "roleplay_policy": "independent_member_choice_and_score",
                "transcripts": roleplay.transcripts,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\n[Saved] benchmark_result.json -> {result_path}")
    print(f"[Saved] multi_user_roleplay.json -> {meta_path}")


if __name__ == "__main__":
    main()
