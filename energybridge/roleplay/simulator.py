"""Persona-aware role-play user simulator for EnergyBridge evaluation."""
from __future__ import annotations
import json, random, datetime
from pathlib import Path
from energybridge.llm.client import LLMClient
from energybridge.roleplay.schema import to_legacy_dict


def _extract_json(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = "\n".join(l for l in s.splitlines() if not l.strip().startswith("```")).strip()
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        return s[a:b+1]
    raise ValueError("No JSON in LLM response")


def _log(path: Path | None, entry: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    entry["_ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


class RoleplayUserSimulator:
    """Simulates a home user using a loaded persona dict."""

    def __init__(self, persona: dict) -> None:
        self._flat = to_legacy_dict(persona) if "preferences" in persona else persona
        self._client = LLMClient(
            config_prefix="ROLEPLAY_LLM",
            use_key="ROLEPLAY_USE_LLM",
            fallback_prefix="LLM",
        )

    def get_user_input(
        self,
        event_index: int,
        vpp_context: dict,
        past_events: list[dict],
        building: str = "family",
        log_path: Path | None = None,
    ) -> str:
        """Simulate user preference expression before a VPP event."""
        override_prob = self._flat.get("vpp_override_prob", 0.0)
        if override_prob > 0.0 and random.random() < override_prob:
            msg = ("I really do not want the temperature to go above 26\u00b0C. "
                   "Please keep me comfortable \u2014 this is my top priority.")
            _log(log_path, {"type": "user_input", "event_index": event_index,
                            "persona_id": self._flat.get("id"), "source": "override", "text": msg})
            return msg

        sys_p = self._flat.get("roleplay_user_prompt", "You are a residential home user.")
        hist  = "\n".join(
            f"  Event {e.get('event_index','?')}: overall={e.get('overall_score','?')}/5 — {e.get('comment','')}"
            for e in past_events[-3:]
        )
        usr_p = (
            f"Building: {building}\n"
            f"Upcoming VPP event #{event_index}: {vpp_context.get('vpp_id','?')} "
            f"window {vpp_context.get('trigger_h',0):.0f}:00-{vpp_context.get('end_h',0):.0f}:00\n"
        )
        if hist:
            usr_p += f"Past outcomes:\n{hist}\n"
        usr_p += "Express your preference in 1-3 sentences. Plain text only."

        try:
            result = self._client.chat_with_metrics(sys_p, usr_p)
            text = result["text"].strip().strip('"').strip("'")
        except Exception as exc:
            text = f"[LLM error: {exc}]"

        _log(log_path, {"type": "user_input", "event_index": event_index,
                        "persona_id": self._flat.get("id"), "source": "llm", "text": text})
        return text

    def score_result(
        self,
        event_index: int,
        mean_temp_c: float,
        pmv_ok_fraction: float,
        energy_kwh_per_day: float,
        agent_setpoint_c: float | None = None,
        agent_reason: str = "",
        user_preference_text: str = "",
        zone_group_temps: dict | None = None,
        washer_completed: bool = True,
        washer_during_vpp: bool = False,
        appliance_summary: dict | None = None,
        log_path: Path | None = None,
    ) -> dict:
        """Score an event outcome from this persona's perspective."""
        weights = self._flat.get("scoring_weights", {"comfort": 0.5, "energy": 0.3, "vpp": 0.2})
        try:
            r = self._llm_score(event_index=event_index, mean_temp_c=mean_temp_c,
                                pmv_ok_fraction=pmv_ok_fraction, energy_kwh_per_day=energy_kwh_per_day,
                                agent_setpoint_c=agent_setpoint_c, agent_reason=agent_reason,
                                user_preference_text=user_preference_text,
                                washer_completed=washer_completed, washer_during_vpp=washer_during_vpp,
                                appliance_summary=appliance_summary or {},
                                weights=weights)
            r["source"] = "llm"
        except Exception:
            r = self._rule_score(mean_temp_c=mean_temp_c, pmv_ok_fraction=pmv_ok_fraction,
                                 energy_kwh_per_day=energy_kwh_per_day,
                                 washer_completed=washer_completed, washer_during_vpp=washer_during_vpp,
                                 appliance_summary=appliance_summary or {},
                                 weights=weights)
            r["source"] = "rule"
        _log(log_path, {"type": "feedback", "event_index": event_index,
                        "persona_id": self._flat.get("id"), "scores": r})
        return r

    def _llm_score(self, *, event_index, mean_temp_c, pmv_ok_fraction, energy_kwh_per_day,
                   agent_setpoint_c, agent_reason, user_preference_text,
                   washer_completed, washer_during_vpp, appliance_summary=None, weights):
        sys_p = (self._flat.get("roleplay_user_prompt", "You are a home user.")
                 + "\nScore how satisfied you are with the agent's last event.")
        w_c, w_e, w_v = weights["comfort"], weights["energy"], weights["vpp"]
        sp = f"{agent_setpoint_c:.1f}\u00b0C" if agent_setpoint_c else "unknown"
        washer_line = (f"  Washer completed: {'yes' if washer_completed else 'NO'}. "
                       f"Ran during VPP: {'YES' if washer_during_vpp else 'no'}.\n"
                       if washer_completed is not None else "")
        # Build per-appliance summary line (independent results per device)
        appl_lines = ""
        if appliance_summary:
            for _aname, _ainfo in appliance_summary.items():
                if not isinstance(_ainfo, dict) or not _ainfo.get("present", False):
                    continue
                _vpp = _ainfo.get("ran_during_vpp", False)
                _ok = _ainfo.get("completed", _ainfo.get("ready_at_bath",
                      _ainfo.get("target_reached", True)))
                appl_lines += f"  {_aname}: completed={_ok} ran_during_vpp={_vpp}\n"
        usr_p = (
            f"Event #{event_index}:\n"
            f"  Mean indoor temp: {mean_temp_c:.1f}\u00b0C\n"
            f"  PMV in comfort band: {pmv_ok_fraction*100:.0f}%\n"
            f"  Daily energy: {energy_kwh_per_day:.1f} kWh\n"
            f"  Agent setpoint: {sp}, reason: {agent_reason}\n"
            f"{washer_line}"
            f"{appl_lines}"
            f"Your stated preference: \"{user_preference_text}\"\n\n"
            f"Score 1-5 each (comfort={w_c:.0%}, energy={w_e:.0%}, vpp={w_v:.0%}). "
            'Return ONLY JSON: {"comfort_score":X,"energy_score":X,"vpp_score":X,"comment":"..."}' 
        )
        raw = self._client.chat_with_metrics(sys_p, usr_p)
        d = json.loads(_extract_json(raw["text"]))
        c, e, v = int(d["comfort_score"]), int(d["energy_score"]), int(d["vpp_score"])
        return {"comfort_score": c, "energy_score": e, "vpp_score": v,
                "overall_score": round(w_c*c + w_e*e + w_v*v), "comment": str(d.get("comment",""))}

    def _rule_score(self, *, mean_temp_c, pmv_ok_fraction, energy_kwh_per_day,
                    washer_completed, washer_during_vpp, appliance_summary=None, weights):
        tmax = self._flat.get("preferred_temp_max", 26.0)
        tol  = self._flat.get("temp_tolerance", 1.0)
        if pmv_ok_fraction >= 0.90 and mean_temp_c <= tmax + tol:       c = 5
        elif pmv_ok_fraction >= 0.75 and mean_temp_c <= tmax + tol*2:   c = 4
        elif pmv_ok_fraction >= 0.60 and mean_temp_c <= tmax + tol*3:   c = 3
        elif mean_temp_c <= tmax + tol*4:                                c = 2
        else:                                                             c = 1
        if energy_kwh_per_day <= 18:   e = 5
        elif energy_kwh_per_day <= 22: e = 4
        elif energy_kwh_per_day <= 26: e = 3
        elif energy_kwh_per_day <= 30: e = 2
        else:                          e = 1
        v = 4
        if washer_during_vpp:   v = max(1, v - 2)
        if not washer_completed: v = max(1, v - 1)
        # Independent per-appliance penalties
        if appliance_summary:
            for _aname, _ainfo in appliance_summary.items():
                if not isinstance(_ainfo, dict) or not _ainfo.get("present", False):
                    continue
                if _ainfo.get("ran_during_vpp", False):
                    v = max(1, v - 1)   # each device in VPP window costs 1pt
                # task not completed: extra penalty
                _done = _ainfo.get("completed", _ainfo.get("ready_at_bath",
                        _ainfo.get("target_reached", True)))
                if not _done:
                    v = max(1, v - 1)
        w_c, w_e, w_v = weights["comfort"], weights["energy"], weights["vpp"]
        return {"comfort_score": c, "energy_score": e, "vpp_score": v,
                "overall_score": round(w_c*c + w_e*e + w_v*v), "comment": "[rule-based]"}
