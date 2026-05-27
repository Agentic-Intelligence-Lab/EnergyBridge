"""MemGPT-style hierarchical memory manager for EnergyBridge long-term tests.

Each run gets a fully isolated memory directory:

    experiments/benchmark/memory/
        {persona}_{city}_{run_id}/
            belief_state.json   - structured preference model, updated after each VPP event
            episodic.jsonl      - append-only per-event detailed log
            semantic_rules.json - extracted persistent rules (human-readable)

Memory is initialised fresh at run start. Concurrent/sequential runs never share
memory: the run_id (timestamp) guarantees isolation.

Update pipeline (per VPP event):
  1. Append raw event to episodic.jsonl
  2. Run a reflection LLM call -> returns JSON diff of belief updates
  3. Apply EMA-based merging for numeric fields
  4. Persist belief_state.json

Reference: Shinn et al. (2023) Reflexion; Packer et al. (2023) MemGPT.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Default belief state
# ---------------------------------------------------------------------------
_BELIEF_DEFAULTS: Dict[str, Any] = {
    "n_events": 0,
    # Thermal comfort model
    "temp_preference_c": 24.5,     # learned preferred setpoint
    "temp_confidence": 0.20,       # 0-1; grows with more evidence
    # VPP cooperation model
    "vpp_fatigue": 0.0,            # 0-1; cumulative fatigue level
    "needs_benefit_explanation": False,
    "vpp_tolerance": 0.8,
    # Appliance model
    "washer_success_rate": 0.0,
    "washer_preferred_window": [9.0, 15.0],
    # Energy model
    "energy_sensitivity": 0.5,
    # Semantic (updated by LLM reflection)
    "semantic_rules": [],
    "last_reflection": "",
}

# ---------------------------------------------------------------------------
# LLM prompts
# ---------------------------------------------------------------------------
_REFLECTION_SYS = (
    "You are a preference-learning assistant for a smart home energy management system. "
    "Extract actionable beliefs from daily VPP event outcomes. "
    "Respond with ONLY a valid JSON object containing the fields that should change. "
    "No prose, no markdown, no text outside the JSON."
)

_REFLECTION_TMPL = """\
Update the belief state based on the VPP event outcome below.

EVENT OUTCOME:
  day={day}, overall={overall}/5, comfort={comfort}/5, energy={energy}/5, vpp={vpp}/5
  setpoint_during_vpp={setpoint}C
  washer_completed={washer_ok}
  user_said: "{user_input}"
  evaluator_comment: "{comment}"

CURRENT BELIEF STATE (full JSON):
{belief_json}

Return a JSON object with ONLY the fields that need to change (plus "n_events": {n_next}).

Update rules (apply conservatively):

  n_events: always = {n_next}

  vpp_fatigue (float 0-1):
    +0.25 if vpp_score <= 1; +0.15 if vpp_score == 2; -0.05 if vpp_score >= 4
    clamp to [0, 1]

  needs_benefit_explanation (bool):
    Set true if user_said contains: benefit, worth it, how long, why, cancel, explain

  temp_preference_c (float):
    comfort<=2 AND setpoint>24.0: decrease by 0.3 (min 22.5)
    comfort>=4: increase by 0.1 (max 25.5)
    otherwise: no change

  temp_confidence: increase by 0.08 per event (cap at 0.95)

  washer_success_rate:
    new = (old * ({n_next}-1) + {washer_int}) / {n_next}

  vpp_tolerance:
    vpp<=2: decrease 0.05; vpp>=4: increase 0.03 (clamp [0,1])

  semantic_rules (list of strings, max 5):
    Preserve useful existing rules. Add a new one only when today reveals a
    clear pattern not already captured. Be concise and actionable.

  last_reflection (string):
    One sentence: what changed in the preference model today and why.

Return ONLY changed fields."""


class MemoryManager:
    """Manages isolated per-run hierarchical memory for a benchmark run.

    All runs are stored under:
        experiments/benchmark/memory/{persona}_{city}_{run_id}/

    A new run_id (timestamp) is generated at startup, so concurrent or
    sequential runs for the same persona never share or overwrite each other.
    """

    MEMORY_ROOT = Path(__file__).resolve().parent / "memory"

    def __init__(
        self,
        persona: str,
        city: str,
        run_id: Optional[str] = None,
        pool=None,
    ) -> None:
        self.persona = persona
        self.city = city
        self.run_id = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self._pool = pool

        self.run_dir = self.MEMORY_ROOT / f"{persona}_{city}_{self.run_id}"
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self._belief_path = self.run_dir / "belief_state.json"
        self._episodic_path = self.run_dir / "episodic.jsonl"
        self._rules_path = self.run_dir / "semantic_rules.json"

        self.belief: Dict[str, Any] = {
            **_BELIEF_DEFAULTS,
            "persona": persona,
            "city": city,
            "run_id": self.run_id,
        }
        self._save()
        print(f"  [Memory] run_dir: {self.run_dir}")

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def update(self, event: Dict[str, Any]) -> None:
        """Update memory after one VPP event. Call once per scored event."""
        with open(self._episodic_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        if self._pool is not None:
            self._reflect_llm(event)
        else:
            self._reflect_deterministic(event)

        self._save()

    def build_prompt_ctx(self) -> str:
        """Return the memory context string to inject into the agent system prompt."""
        b = self.belief
        n = b.get("n_events", 0)
        if n == 0:
            return ""

        explanation_warning = (
            "  WARNING: User REQUIRES benefit explanation before VPP -- always state: "
            "duration + estimated kWh saved.\n"
            if b.get("needs_benefit_explanation") else ""
        )
        rules_block = ""
        if b.get("semantic_rules"):
            rules_block = "LEARNED RULES:\n" + "\n".join(
                f"  [{i+1}] {r}" for i, r in enumerate(b["semantic_rules"])
            ) + "\n"

        return (
            f"\n[PERSISTENT MEMORY -- {n} event(s)]\n"
            f"Preference model:\n"
            f"  temp_preference:    {b['temp_preference_c']:.1f}C  "
            f"(confidence={b['temp_confidence']:.0%})\n"
            f"  vpp_fatigue:        {b['vpp_fatigue']:.2f}/1.0  "
            f"({_fatigue_label(b['vpp_fatigue'])})\n"
            f"  vpp_tolerance:      {b['vpp_tolerance']:.2f}\n"
            f"  washer_success:     {b['washer_success_rate']:.0%} of days\n"
            f"{explanation_warning}"
            f"{rules_block}"
            f"Last reflection: {b.get('last_reflection', '(none yet)')}\n"
        )

    @property
    def temp_preference(self) -> float:
        return float(self.belief.get("temp_preference_c", 24.5))

    @property
    def vpp_fatigue(self) -> float:
        return float(self.belief.get("vpp_fatigue", 0.0))

    @property
    def needs_explanation(self) -> bool:
        return bool(self.belief.get("needs_benefit_explanation", False))

    # -----------------------------------------------------------------------
    # Private
    # -----------------------------------------------------------------------

    def _reflect_llm(self, event: Dict[str, Any]) -> None:
        n = self.belief.get("n_events", 0)
        washer_ok = bool(event.get("washer_completed", False))
        prompt = _REFLECTION_TMPL.format(
            day=event.get("day", n + 1),
            overall=event.get("overall", 3),
            comfort=event.get("comfort", 3),
            energy=event.get("energy", 3),
            vpp=event.get("vpp", 3),
            setpoint=event.get("setpoint", 24.0),
            washer_ok=washer_ok,
            washer_int=1 if washer_ok else 0,
            user_input=str(event.get("user_input", ""))[:150],
            comment=str(event.get("comment", ""))[:150],
            belief_json=json.dumps(self.belief, indent=2, ensure_ascii=False),
            n_next=n + 1,
        )
        try:
            resp = self._pool.chat(_REFLECTION_SYS, prompt).strip()
            if not resp.lstrip().startswith("{"):
                a, b_ = resp.find("{"), resp.rfind("}")
                if a != -1 and b_ > a:
                    resp = resp[a:b_+1]
                else:
                    raise ValueError(f"No JSON in reflection: {resp[:80]!r}")
            updates = json.loads(resp)
            _PROTECTED = {"persona", "city", "run_id"}
            for k, v in updates.items():
                if k not in _PROTECTED:
                    self.belief[k] = v
            print(
                f"  [Memory d{event.get('day', n+1)}] "
                f"fatigue={self.belief['vpp_fatigue']:.2f} "
                f"explain={self.belief['needs_benefit_explanation']} "
                f"temp={self.belief['temp_preference_c']:.1f}C | "
                f"{self.belief.get('last_reflection', '')[:70]}"
            )
        except Exception as exc:
            print(f"  [Memory] LLM reflection failed ({exc}); using deterministic fallback")
            self._reflect_deterministic(event)

    def _reflect_deterministic(self, event: Dict[str, Any]) -> None:
        n = self.belief.get("n_events", 0) + 1
        self.belief["n_events"] = n

        vpp = event.get("vpp", 3)
        comfort = event.get("comfort", 3)
        sp = float(event.get("setpoint", 24.0))
        washer_ok = 1 if event.get("washer_completed", False) else 0

        # VPP fatigue
        fat = self.belief["vpp_fatigue"]
        if vpp <= 1:   fat = min(1.0, fat + 0.25)
        elif vpp <= 2: fat = min(1.0, fat + 0.15)
        elif vpp >= 4: fat = max(0.0, fat - 0.05)
        self.belief["vpp_fatigue"] = round(fat, 3)

        # VPP tolerance
        tol = self.belief.get("vpp_tolerance", 0.8)
        if vpp <= 2:   tol = max(0.0, tol - 0.05)
        elif vpp >= 4: tol = min(1.0, tol + 0.03)
        self.belief["vpp_tolerance"] = round(tol, 3)

        # Needs explanation
        user_input = str(event.get("user_input", "")).lower()
        if any(kw in user_input for kw in [
                "benefit", "worth", "how long", "what's in it", "cancel", "explain", "why"]):
            self.belief["needs_benefit_explanation"] = True

        # Temp preference
        pref = self.belief["temp_preference_c"]
        if comfort <= 2 and sp > 24.0:
            pref = max(22.5, pref - 0.3)
        elif comfort >= 4:
            pref = min(25.5, pref + 0.1)
        self.belief["temp_preference_c"] = round(pref, 2)
        self.belief["temp_confidence"] = round(
            min(0.95, self.belief.get("temp_confidence", 0.2) + 0.08), 3)

        # Washer success rate
        old_rate = self.belief.get("washer_success_rate", 0.0)
        self.belief["washer_success_rate"] = round((old_rate * (n - 1) + washer_ok) / n, 3)

        self.belief["last_reflection"] = (
            f"Day {event.get('day', n)}: overall={event.get('overall', 3)}/5, "
            f"fatigue now {self.belief['vpp_fatigue']:.2f}"
        )

    def _save(self) -> None:
        self._belief_path.write_text(
            json.dumps(self.belief, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        rules = self.belief.get("semantic_rules", [])
        if rules:
            self._rules_path.write_text(
                json.dumps({
                    "run_id": self.run_id, "persona": self.persona,
                    "city": self.city, "rules": rules,
                }, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fatigue_label(f: float) -> str:
    if f < 0.2:  return "LOW"
    if f < 0.5:  return "MODERATE"
    if f < 0.75: return "HIGH"
    return "CRITICAL"


def list_runs(persona: Optional[str] = None, city: Optional[str] = None) -> List[Path]:
    """List all memory run directories, optionally filtered."""
    root = MemoryManager.MEMORY_ROOT
    if not root.exists():
        return []
    runs = sorted(root.iterdir())
    if persona:
        runs = [r for r in runs if r.name.startswith(persona)]
    if city:
        runs = [r for r in runs if f"_{city}_" in r.name]
    return runs
