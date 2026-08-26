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
from datetime import date, datetime
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
from energybridge.benchmark.run_manifest import build_run_manifest
from energybridge.data.vpp_events import describe_vpp_events, load_vpp_events_config, make_daily_vpp_events
from energybridge.roleplay.calendar import calendar_context_for_event
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
FULL_SHARED_APPLIANCES = ("ac", "washer", "dryer", "dishwasher", "water_heater", "ev", "refrigerator")


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value).strip("_").lower()


def _default_output_dir(household_id: str, method: str, city: str, days: int, horizon: int) -> Path:
    method = _canonical_method(method)
    token = f"{method}_H{int(horizon)}" if method == "mpc_dynamic" else method
    return DEFAULT_BENCHMARK_RESULTS_DIR / date.today().isoformat() / (
        f"{_slug(household_id)}_{token}_{city.lower()}_{int(days)}days"
    )


def _household_system_prompt(household: dict[str, Any]) -> str:
    base = str(((household.get("llm_prompts") or {}).get("system_prompt")) or "")
    appliance_names = ", ".join(FULL_SHARED_APPLIANCES)
    addendum = (
        "This benchmark household physically owns the full shared appliance set: "
        f"{appliance_names}. Do not infer missing appliances from any individual member JSON; "
        "individual persona JSON files are used only for role-play preference, comments, choices, "
        "and scoring. Shared appliance feasibility is defined by the household JSON.\n"
        "Each shared appliance service has exactly one household device, not one device per member. "
        "Coordinate all member preferences into one feasible schedule per device.\n"
        "Controller service contract: washer, dryer, dishwasher, water heater, and EV are shared "
        "household services. If a service is present, give it an explicit feasible policy. "
        "Do not use washer_skip/dryer_skip/dishwasher_skip to avoid VPP or because scheduling is hard; "
        "skip=true is allowed only when the member feedback or appliance status explicitly says the task "
        "is unnecessary today. Otherwise schedule the task outside the VPP window and set skip=false. "
        "At every replan, all emitted start/preheat/charge times must be executable from the current clock time: "
        "never output a past time, and never output a window that overlaps an elapsed or active VPP window. "
        "After a VPP window begins, only move services that were originally inside that VPP window to after it; "
        "do not reschedule already-safe services backward into the event window."
    )
    return f"{base}\n\n{addendum}".strip()


def _member_label(persona: dict[str, Any]) -> str:
    member = persona.get("household_member", {}) or {}
    member_id = str(member.get("member_id") or persona.get("id"))
    role = str(member.get("household_role") or "")
    display = str(persona.get("display_name") or persona.get("id") or member_id)
    persona_id = str(persona.get("id") or "")
    role_text = f"; role={role}" if role else ""
    return f"{member_id} ({display}; persona={persona_id}{role_text})"


def _fmt_h(value: Any) -> str:
    try:
        h = float(value) % 24.0
    except (TypeError, ValueError):
        return "?"
    hh = int(h)
    mm = int(round((h - hh) * 60.0))
    if mm >= 60:
        hh = (hh + 1) % 24
        mm = 0
    return f"{hh:02d}:{mm:02d}"


def _calendar_day_summary(persona: dict[str, Any], day_idx: int) -> str:
    ctx = calendar_context_for_event(
        persona,
        day_idx,
        {"day": day_idx, "trigger_h": 18.0, "duration_h": 1.0},
    )
    if not ctx.get("available"):
        return f"Day {day_idx}: calendar unavailable"
    events = ctx.get("events") or []
    event_text = "; ".join(
        f"{_fmt_h(e.get('start_h'))}-{_fmt_h(e.get('end_h'))} {e.get('title', '')} @{e.get('location', '')}"
        for e in events
    ) or "no scheduled events"
    conflicts = ctx.get("vpp_conflicts") or []
    conflict_text = "; ".join(
        f"{_fmt_h(e.get('start_h'))}-{_fmt_h(e.get('end_h'))} {e.get('title', '')}"
        for e in conflicts
    ) or "none"
    deadlines = ctx.get("appliance_deadlines") or {}
    return (
        f"Day {day_idx} {ctx.get('weekday', '')}: {event_text}; "
        f"VPP-window conflicts={conflict_text}; appliance_deadlines={json.dumps(deadlines, ensure_ascii=False)}"
    )


def _member_calendar_context_text(member_personas: list[dict[str, Any]], *, days: int) -> str:
    horizon = max(1, min(int(days), 7))
    lines = [
        "[All household member calendars visible to the controller]",
        "Use these member calendars as first-class constraints. They are the source of household occupancy, "
        "return-home comfort needs, chore deadlines, bath/hot-water needs, and EV departure readiness.",
    ]
    for persona in member_personas:
        lines.append(f"- {_member_label(persona)}")
        for day_idx in range(1, horizon + 1):
            lines.append(f"  * {_calendar_day_summary(persona, day_idx)}")
    return "\n".join(lines)


def _calendar_context_brief(ctx: dict[str, Any]) -> str:
    if not ctx or not ctx.get("available", False):
        return "calendar unavailable"
    conflicts = ctx.get("vpp_conflicts") or []
    conflict_text = "; ".join(
        f"{_fmt_h(e.get('start_h'))}-{_fmt_h(e.get('end_h'))} {e.get('title', '')}"
        for e in conflicts
    ) or "none"
    deadlines = ctx.get("appliance_deadlines") or {}
    events = ctx.get("events") or []
    events_text = "; ".join(
        f"{_fmt_h(e.get('start_h'))}-{_fmt_h(e.get('end_h'))} {e.get('title', '')}"
        for e in events[:8]
    ) or "none"
    return (
        f"day={ctx.get('day')}, events={events_text}, "
        f"vpp_conflicts={conflict_text}, deadlines={json.dumps(deadlines, ensure_ascii=False)}"
    )


def _controller_feedback_from_member_scores(event_index: int, member_scores: list[dict[str, Any]]) -> str:
    parts = []
    low_score_members = []
    skip_mentions = []
    for item in member_scores:
        member_id = str(item.get("member_id", "member"))
        score = float(item.get("score", 3.0) or 3.0)
        comment = str(item.get("comment", "")).strip()
        parts.append(f"{member_id}={score:.1f}/5: {comment[:240]}")
        if score <= 2.0:
            low_score_members.append(member_id)
        if "skip" in comment.lower() or "skipped" in comment.lower():
            skip_mentions.append(member_id)
    guidance = (
        "Next event controller guidance: preserve each member's comfort and routine constraints; "
        "schedule every present shared appliance explicitly; do not use skip=true for required "
        "washer/dryer/dishwasher tasks unless a member explicitly says the task is unnecessary today. "
        "Use price/cost-aware schedules when they do not disrupt service, and explain the comfort, "
        "appliance-service, VPP-window, and cost/price reasoning clearly. If you correct a named "
        "member complaint next event, that member should reward the improvement; repeated unresolved "
        "complaints should be penalized."
    )
    if low_score_members:
        guidance += " Low-scoring members need priority correction: " + ", ".join(low_score_members) + "."
    if skip_mentions:
        guidance += " The skipped-task complaint must be treated as a hard service violation."
    return f"Multi-user post-event {event_index} feedback: " + " | ".join(parts) + " | " + guidance


def _household_agent_context(
    household: dict[str, Any],
    member_personas: list[dict[str, Any]],
    *,
    days: int,
) -> str:
    prompt = _household_system_prompt(household)
    members = "\n".join(f"- {_member_label(persona)}" for persona in member_personas)
    present = [
        name for name, cfg in (household.get("appliances") or {}).items()
        if isinstance(cfg, dict) and bool(cfg.get("present"))
    ]
    service_contract = (
        "[Shared appliance service contract]\n"
        f"Present shared appliances: {', '.join(present) if present else 'none'}.\n"
        "There is one shared unit per service: one washer, one dryer, one dishwasher, one water heater, and one EV charger. "
        "Do not create per-member duplicate appliance schedules.\n"
        "Every VPP-day controller prompt must treat the household appliance set, not any individual "
        "persona appliance list, as the physical truth. Washer, dryer, dishwasher, water heater, and EV "
        "need explicit commands when present. For washer/dryer/dishwasher, the normal valid command is "
        "start_h plus skip=false. Do not output skip=true unless member feedback or runtime appliance "
        "status explicitly says the task is unnecessary today. VPP shifting means moving the task outside "
        "the VPP window, not cancelling it. Replans must use future feasible times only; after VPP starts, "
        "do not output appliance times earlier than the current clock or inside the VPP window that just started."
    )
    return "\n\n".join([
        prompt,
        "[Household members]",
        members,
        service_contract,
        _member_calendar_context_text(member_personas, days=days),
    ]).strip()


def _build_physical_household_persona(
    household: dict[str, Any],
    member_personas: list[dict[str, Any]],
    *,
    days: int,
) -> dict[str, Any]:
    """Persona-like object used only for EP occupancy and shared appliances."""
    prompt = _household_system_prompt(household)
    agent_context = _household_agent_context(household, member_personas, days=days)
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
        "acceptance_profiles": [
            {
                "member_id": p.get("household_member", {}).get("member_id", p.get("id")),
                "persona_id": p.get("id"),
                "display_name": p.get("display_name", p.get("id")),
                "household_role": p.get("household_member", {}).get("household_role", ""),
                "decision_weight": float(p.get("household_member", {}).get("decision_weight", 1.0) or 1.0),
                "tags": copy.deepcopy(p.get("tags") or {}),
                "preferences": copy.deepcopy(p.get("preferences") or {}),
                "schedule": copy.deepcopy(p.get("schedule") or {}),
                "calendar": copy.deepcopy(p.get("calendar") or {}),
                "appliances": {"ac": copy.deepcopy((p.get("appliances") or {}).get("ac", {}))},
            }
            for p in member_personas
        ],
        "llm_prompts": {
            "system_prompt": prompt,
            "agent_context": agent_context,
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
        days: int = 7,
        max_memory_items: int = 10,
    ) -> None:
        self.household = household
        self.household_prompt = _household_system_prompt(household)
        self.household_agent_context = _household_agent_context(household, member_personas, days=days)
        self.shared_appliances = copy.deepcopy(household.get("appliances") or {})
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
        self.member_preferences_by_event: dict[str, dict[str, str]] = {}

    @staticmethod
    def _member_key(persona: dict[str, Any]) -> str:
        return str(persona.get("household_member", {}).get("member_id") or persona.get("id"))

    def _member_name(self, persona: dict[str, Any]) -> str:
        member = persona.get("household_member", {}) or {}
        role = member.get("household_role", "")
        name = persona.get("display_name", persona.get("id", self._member_key(persona)))
        return f"{self._member_key(persona)} ({name}; {role})" if role else f"{self._member_key(persona)} ({name})"

    def _persona_appliances_for_roleplay(self, persona: dict[str, Any]) -> dict[str, Any]:
        """Use household non-AC appliances while preserving member comfort AC preferences."""
        merged = copy.deepcopy(persona.get("appliances") or {})
        for name, cfg in self.shared_appliances.items():
            if name == "ac" and name in merged:
                continue
            merged[name] = copy.deepcopy(cfg)
        return merged

    def _persona_with_context(self, persona: dict[str, Any]) -> dict[str, Any]:
        out = copy.deepcopy(persona)
        out["appliances"] = self._persona_appliances_for_roleplay(out)
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
            "Use your personal comfort/consent/routine preferences, but use the household shared appliance set as physical truth.",
            "Assume the physical household owns AC, washer, dryer, dishwasher, water heater, EV, and refrigerator.",
            "There is one shared unit per appliance service, coordinated across all members.",
            "A required shared appliance task being skipped is a serious service failure unless you explicitly said it is unnecessary today.",
            "When scoring, consider electricity price/cost as a real household factor after comfort and service feasibility. A no-disruption cheaper schedule deserves credit.",
            "Give modest extra credit for a truthful explanation that connects comfort, appliance completion, EV/hot-water readiness, VPP-window avoidance, and cost/price benefit.",
            "Do not invent explanation quality. If the controller explanation is empty, code-like, or only an objective/solver trace such as 'mpc_pdf_v15 total=...', do not praise clarity, reassurance, consent handling, or price explanation.",
            "Write detailed feedback. If the controller fixes your specific previous complaint in the next event, score more favorably; if it repeats the issue, score more harshly.",
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
        self.member_preferences_by_event[str(event_index)] = {
            str(entry["member_id"]): str(entry.get("preference_text", ""))
            for entry in member_entries
        }
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
            "There is one shared unit per appliance service; produce one coordinated schedule per service.",
            "Hard appliance rule: do not cancel required washer/dryer/dishwasher service; tell the controller to schedule it outside the VPP window with skip=false unless a member explicitly says the task is unnecessary today.",
            "Hard timing rule: all controller times must be future-feasible from the current replan time; never schedule a preheat, charge, washer, dryer, or dishwasher command into the past or into an elapsed/active VPP window.",
            "Cost-awareness rule: when comfort and service constraints are preserved, tell the controller to prefer lower price/cost schedules and explain that benefit plainly.",
            "Explanation rule: ask the controller to state why the plan preserves comfort, required appliance service, EV/hot-water readiness, VPP-window avoidance, and price/cost benefit.",
            "Do not discard minority concerns; preserve conflicts and hard constraints.",
        ]
        for entry in entries:
            selected = entry.get("selected_strategy") or {}
            calendar_text = _calendar_context_brief(entry.get("calendar_context") or {})
            lines.append(
                f"- {entry['member_name']}: selected {selected.get('id', selected.get('label', 'custom'))}; "
                f"preference={entry.get('preference_text', '')}; calendar={calendar_text}"
            )
        fallback = (
            "Multi-user feedback: "
            + " | ".join(
                f"{e['member_id']} says {e.get('preference_text', '')[:180]}" for e in entries
            )
            + " | Required household appliance tasks must be scheduled, not skipped. Use one shared device schedule per service and future-feasible times only."
        )
        try:
            from energybridge.llm.client import LLMClient

            sys_prompt = (
                "You summarize independent household member preferences for a home energy control agent. "
                "Return JSON only: {\"agent_feedback\": \"<=260 words\", \"conflicts\": [\"...\"], "
                "\"hard_constraints\": [\"...\"]}. Preserve every member's hard constraint. "
                "Be explicit that required shared appliance tasks should be scheduled outside the VPP window, "
                "not skipped, unless the task is explicitly unnecessary today. Also be explicit that replans must "
                "use future feasible times only, never past times or elapsed/active VPP-window times. Include "
                "cost-aware scheduling and a clear explanation requirement when these do not conflict with service."
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
        event_member_preferences = self.member_preferences_by_event.get(str(event_index), {})
        for persona in self.member_personas:
            member_persona = self._persona_with_context(persona)
            member_id = self._member_key(persona)
            member_preference_text = event_member_preferences.get(member_id, user_preference_text)
            result = orig_score(
                building=building,
                method=method,
                mean_temp_c=mean_temp_c,
                pmv_ok_fraction=pmv_ok_fraction,
                energy_kwh_per_day=energy_kwh_per_day,
                agent_setpoint_c=agent_setpoint_c,
                event_index=event_index,
                user_preference_text=member_preference_text,
                agent_reason=agent_reason,
                persona=member_persona,
                **kwargs,
            )
            entry = {
                "member_id": member_id,
                "member_name": self._member_name(persona),
                "persona_id": persona.get("id"),
                "scored_against_preference": member_preference_text,
                "agent_feedback_seen_by_controller": user_preference_text,
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
        controller_feedback = _controller_feedback_from_member_scores(event_index, member_scores)
        aggregate = {
            "score": round(avg, 3),
            "comfort_score": round(comfort_avg, 3),
            "energy_score": round(energy_avg, 3),
            "vpp_score": round(vpp_avg, 3),
            "label": label,
            "comment": f"mean of {len(member_scores)} independent member scores; {comments}",
            "controller_feedback": controller_feedback,
            "member_feedback_summary": controller_feedback,
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


def _method_label(method: str) -> str:
    labels = {
        ENERGYBRIDGE_METHOD_ID: "EnergyBridge",
        "mpc_dynamic": "MPC Dynamic",
        "rule_milp": "Rule+MILP",
        "eb_rule_milp": "EnergyBridge",
    }
    return labels.get(str(method), str(method))


def _short_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _first_action_dict(event: dict[str, Any]) -> dict[str, Any]:
    for decision in event.get("day_decisions") or []:
        if not isinstance(decision, dict):
            continue
        actions = decision.get("actions") or decision.get("raw_appliance_actions") or {}
        if isinstance(actions, dict) and actions:
            return actions
    actions = event.get("vpp_trigger_actions") or {}
    return actions if isinstance(actions, dict) else {}


def _action_summary(actions: dict[str, Any]) -> str:
    if not actions:
        return "no explicit appliance actions"
    parts: list[str] = []
    for service in ("washer", "dishwasher", "dryer"):
        start = actions.get(f"{service}_start_h")
        skip = actions.get(f"{service}_skip")
        if skip is True:
            parts.append(f"{service}:skip")
        elif start is not None:
            parts.append(f"{service}:{_fmt_h(start)}")
    if actions.get("water_heater_preheat"):
        wh_start = actions.get("water_heater_preheat_start_h")
        wh_end = actions.get("water_heater_preheat_end_h")
        parts.append(f"water_heater:{_fmt_h(wh_start)}-{_fmt_h(wh_end)}")
    ev_start = actions.get("ev_charge_start_h")
    ev_end = actions.get("ev_charge_end_h")
    if ev_start is not None and ev_end is not None:
        parts.append(f"ev:{_fmt_h(ev_start)}-{_fmt_h(ev_end)}")
    return "; ".join(parts) if parts else "no appliance start/charge/preheat commands"


def _service_outcome_summary(event: dict[str, Any]) -> str:
    parts: list[str] = []
    for name, info in (event.get("appliance_summary") or {}).items():
        if not isinstance(info, dict) or not info.get("present"):
            continue
        if info.get("ran_during_vpp"):
            status = "ran_in_vpp"
        elif info.get("skipped"):
            status = "skipped"
        elif info.get("completed") is False:
            status = "not_completed_yet"
        else:
            status = "avoided_vpp"
        parts.append(f"{name}:{status}")
    return "; ".join(parts) if parts else "no controllable services"


def _write_household_run_summary(
    *,
    result,
    household: dict[str, Any],
    member_personas: list[dict[str, Any]],
    city: str,
    output_dir: Path,
    roleplay: IndependentMemberRoleplay,
) -> Path:
    """Write a compact human-readable summary for multi-user household runs."""
    data = result.as_dict()
    events = list(data.get("vpp_event_log") or [])
    scores = data.get("user_pref_scores") or []
    avg_score = data.get("user_pref_score")
    members = [
        (
            persona.get("household_member", {}).get("member_id", persona.get("id")),
            persona.get("id"),
            persona.get("display_name", persona.get("id")),
        )
        for persona in member_personas
    ]
    present_appliances = [
        name for name, cfg in (household.get("appliances") or {}).items()
        if isinstance(cfg, dict) and cfg.get("present")
    ]
    lines = [
        "=" * 72,
        "  EnergyBridge Multi-User Household Run Summary  (run_summary.txt)",
        "=" * 72,
        f"  Household : {household.get('display_name') or household.get('id')}",
        f"  ID        : {household.get('id')}",
        f"  Method    : {_method_label(str(data.get('method') or ''))} ({data.get('method') or 'unknown'})",
        f"  City      : {city}",
        f"  Days      : {data.get('sim_days')}",
        f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  Output dir: {output_dir}",
        "",
        "Members:",
    ]
    for member_id, persona_id, display_name in members:
        lines.append(f"  - {member_id}: {display_name} ({persona_id})")
    lines.extend([
        "",
        "Shared physical appliances:",
        f"  {', '.join(present_appliances) if present_appliances else 'none'}",
        "",
        "Key metrics:",
        f"  - Average member score       : {avg_score:.3g}/5" if avg_score is not None else "  - Average member score       : N/A",
        f"  - Per-event mean scores      : {', '.join(str(x) for x in scores) if scores else 'N/A'}",
        f"  - Total energy               : {float(data.get('energy_kwh_total') or 0.0):.3f} kWh",
        f"  - Daily energy               : {float(data.get('energy_kwh_per_day') or 0.0):.3f} kWh/day",
        f"  - VPP-window energy          : {float(data.get('vpp_window_energy_kwh') or 0.0):.3f} kWh",
        f"  - VPP-window avg per hour    : {float(data.get('vpp_window_energy_avg_per_hour_kwh') or 0.0):.3f} kWh/h",
        f"  - Policy appliance output    : {float(data.get('appliance_task_completion_rate') or 0.0) * 100:.0f}%",
        f"  - Physical service completion: {float(data.get('physical_appliance_task_completion_rate') or 0.0) * 100:.0f}%",
        f"  - VPP appliance avoidance    : {float(data.get('appliance_vpp_avoidance_rate') or 0.0) * 100:.0f}%",
        f"  - LLM calls/failures         : {data.get('llm_call_count', 0)} / {data.get('llm_call_failures', 0)}",
        "",
        "-" * 72,
        "Per-event independent role-play",
        "-" * 72,
    ])
    for idx, event in enumerate(events, start=1):
        event_id = event.get("id") or f"vpp{idx}"
        trigger = event.get("trigger_h")
        end = event.get("end_h")
        lines.extend([
            f"[Event {idx}] {event_id} Day{event.get('day', idx)} {_fmt_h(trigger)}-{_fmt_h(end)}",
            f"  Household mean score : {event.get('score', 'N/A')}/5 ({event.get('label', 'N/A')})",
            f"  Controller actions   : {_action_summary(_first_action_dict(event))}",
            f"  Service outcomes     : {_service_outcome_summary(event)}",
        ])
        trace = event.get("strategy_trace") or {}
        member_choices = trace.get("member_choices") or roleplay.transcripts.get("strategy", {}).get(str(idx), [])
        if member_choices:
            lines.append("  Member choices:")
            for choice in member_choices:
                selected = choice.get("selected_strategy") or {}
                selected_id = selected.get("id") or selected.get("label") or "custom"
                pref = _short_text(choice.get("preference_text"), 180)
                lines.append(f"    - {choice.get('member_id')}: {selected_id}; {pref}")
        member_scores = event.get("member_scores") or roleplay.transcripts.get("score", {}).get(str(idx), [])
        if member_scores:
            lines.append("  Member scores:")
            for item in member_scores:
                comment = _short_text(item.get("comment"), 160)
                lines.append(
                    f"    - {item.get('member_id')}: {float(item.get('score', 0.0)):.2f}/5 "
                    f"(comfort={float(item.get('comfort_score', 0.0)):.2f}, "
                    f"energy={float(item.get('energy_score', 0.0)):.2f}, "
                    f"vpp={float(item.get('vpp_score', 0.0)):.2f}) {comment}"
                )
        feedback = event.get("controller_feedback") or event.get("member_feedback_summary")
        if feedback:
            lines.append(f"  Feedback to next event: {_short_text(feedback, 320)}")
        lines.append("")
    lines.extend([
        "-" * 72,
        "Role-play storage",
        "-" * 72,
        "  - multi_user_roleplay.json keeps full independent member strategy and scoring transcripts.",
        "  - benchmark_result.json keeps aggregate metrics plus per-event member_scores/controller_feedback.",
        "=" * 72,
    ])
    path = output_dir / "run_summary.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _make_roleplay_callbacks(roleplay: IndependentMemberRoleplay, orig_get_pref, orig_score):
    def pre_event_preference_callback(
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

    def post_event_score_callback(
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

    return pre_event_preference_callback, post_event_score_callback


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
    parser.add_argument(
        "--mpc-horizon",
        type=int,
        default=6,
        help="MPC prediction horizon used by EnergyBridge advisory and mpc_dynamic (default: 6).",
    )
    parser.add_argument("--idf", default="")
    parser.add_argument("--epw", default="")
    parser.add_argument("--weather-csv", default="")
    parser.add_argument("--regenerate-epw", action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument("--max-memory-items", type=int, default=10)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--dr-memory-library", type=str, default="", help="Path to historical DR memory library JSON (for capacity reporting).")
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
        days=days,
        max_memory_items=args.max_memory_items,
    )

    output_dir = Path(args.output) if args.output else _default_output_dir(
        physical_persona["id"],
        method,
        args.city,
        days,
        mpc_horizon,
    )
    run_manifest = build_run_manifest(
        runner="run_multi_user_household",
        subject_kind="household",
        subject_id=physical_persona["id"],
        subject_reference=args.household,
        method=method,
        city=args.city,
        days=days,
        start_date=start_date,
        price_csv=args.price_csv,
        vpp_start_hour=vpp_start_hour,
        vpp_duration_hours=vpp_duration_hours,
        vpp_events_json=args.vpp_events_json,
        mpc_horizon=mpc_horizon,
        idf=args.idf,
        epw=args.epw,
        weather_csv=args.weather_csv,
        regenerate_epw=args.regenerate_epw,
        max_memory_items=args.max_memory_items,
        dr_memory_library=args.dr_memory_library,
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

    pre_event_callback, post_event_callback = _make_roleplay_callbacks(
        roleplay,
        _ups.get_user_preference_input,
        _ups.score_user_preference,
    )
    controller_user_pref = physical_persona["llm_prompts"]["agent_context"]
    if controller_method == "agent":
        controller_user_pref = (
            "No hidden household persona prompt is preloaded. Infer preferences from the initial "
            "questionnaire memory, observable calendar context, member role-play outputs, "
            "and scored feedback over the run."
        )

    dr_memory_library_path = args.dr_memory_library or None

    result = fr.run_family_agent(
        idf_path=idf_path,
        epw_path=epw_path,
        user_pref=controller_user_pref,
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
        pre_event_preference_callback=pre_event_callback,
        post_event_score_callback=post_event_callback,
        dr_memory_library_path=dr_memory_library_path,
    )

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
    result_dict = result.as_dict()
    result_dict["run_manifest"] = run_manifest
    result_path.write_text(json.dumps(result_dict, indent=2, ensure_ascii=False), encoding="utf-8")
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
    summary_path = _write_household_run_summary(
        result=result,
        household=household_config,
        member_personas=member_personas,
        city=args.city,
        output_dir=output_dir,
        roleplay=roleplay,
    )
    print(f"\n[Saved] benchmark_result.json -> {result_path}")
    print(f"[Saved] multi_user_roleplay.json -> {meta_path}")
    print(f"[Saved] run_summary.txt -> {summary_path}")


if __name__ == "__main__":
    main()
