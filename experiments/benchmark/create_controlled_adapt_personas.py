#!/usr/bin/env python3
"""Create controlled personas for the paper adaptability experiment.

The generated personas keep the original schedule, scoring weights, tags, and
role-play prompts, but replace non-AC appliances with the same appliance suite.
That lets the experiment attribute differences to user preference and calendar
rather than different device availability.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"

SOURCE_TO_CONTROLLED = {
    "basic_role_a_commuter_price_cooperative": "paper_adapt_a_price_cooperative",
    "basic_role_b_home_comfort_gated": "paper_adapt_b_comfort_gated",
    "basic_role_c_irregular_cautious": "paper_adapt_c_irregular_cautious",
    "basic_role_d_commuter_ideal_dr": "paper_adapt_d_ideal_dr",
    "basic_role_e_caregiver_low_dr": "paper_adapt_e_caregiver_low_dr",
}

COMMON_NON_AC_APPLIANCES: dict[str, dict[str, Any]] = {
    "washer": {
        "present": True,
        "earliest_h": 8.0,
        "latest_h": 22.0,
        "preferred_h": 19.0,
        "duration_h": 2.0,
        "power_kw": 1.5,
        "shiftable": True,
        "dr_adjustable": True,
    },
    "dishwasher": {
        "present": True,
        "earliest_h": 9.0,
        "latest_h": 23.0,
        "preferred_h": 21.0,
        "duration_h": 1.5,
        "power_kw": 1.2,
        "shiftable": True,
        "dr_adjustable": True,
    },
    "dryer": {"present": False},
    "water_heater": {
        "present": True,
        "rated_kw": 2.0,
        "bath_required_h": 21.0,
        "dr_adjustable": True,
        "pre_heat_window_start_h": 14.0,
        "pre_heat_window_end_h": 18.0,
    },
    "ev": {"present": False},
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _controlled_persona(source: dict[str, Any], controlled_id: str) -> dict[str, Any]:
    out = json.loads(json.dumps(source, ensure_ascii=False))
    out["id"] = controlled_id
    out["display_name"] = f"Paper Controlled - {source.get('display_name', controlled_id)}"
    out["description"] = (
        str(source.get("description") or "").rstrip()
        + " Controlled paper variant: non-AC appliances are held constant across personas."
    )
    appliances = dict(COMMON_NON_AC_APPLIANCES)
    appliances["ac"] = dict((source.get("appliances") or {}).get("ac") or {"present": True})
    out["appliances"] = {"ac": appliances.pop("ac"), **appliances}
    prompts = dict(out.get("llm_prompts") or {})
    prompts["agent_context"] = (
        str(prompts.get("agent_context") or "")
        + " [paper_controlled_adapt] Same non-AC appliance suite as other controlled personas; adapt only to this user's calendar, consent, comfort, and price preferences."
    ).strip()
    out["llm_prompts"] = prompts
    meta = dict(out.get("meta") or {})
    meta.update(
        {
            "approved": False,
            "persona_type": "paper_controlled_adaptability",
            "source_persona_id": source.get("id"),
            "controlled_non_ac_appliance_suite": True,
        }
    )
    out["meta"] = meta
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate controlled adaptability personas.")
    parser.add_argument("--persona-dir", default=str(DEFAULT_PERSONA_DIR))
    args = parser.parse_args()
    persona_dir = Path(args.persona_dir)
    for source_id, controlled_id in SOURCE_TO_CONTROLLED.items():
        source = _load_json(persona_dir / f"{source_id}.json")
        controlled = _controlled_persona(source, controlled_id)
        out_path = persona_dir / f"{controlled_id}.json"
        out_path.write_text(json.dumps(controlled, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] {source_id} -> {out_path}")


if __name__ == "__main__":
    main()
