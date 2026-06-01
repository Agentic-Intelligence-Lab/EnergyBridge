"""Run a role-play evaluation using a specific persona JSON file."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"


def load_persona(persona_arg: str) -> dict:
    p = Path(persona_arg)
    if p.exists() and p.suffix == ".json":
        raw = json.loads(p.read_text(encoding="utf-8"))
    else:
        candidate = PERSONA_DIR / f"{persona_arg}.json"
        if not candidate.exists():
            raise FileNotFoundError(f"Persona '{persona_arg}' not found.")
        raw = json.loads(candidate.read_text(encoding="utf-8"))
    if "persona_id" not in raw:
        raw["persona_id"] = raw.get("id", persona_arg)
    return raw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", default="basic_role_a_commuter_price_cooperative")
    parser.add_argument("--turns", type=int, default=3)
    parser.add_argument("--output-root", default="logs/evaluations")
    args = parser.parse_args()

    persona_data = load_persona(args.persona)
    print(f"Loaded persona: {persona_data.get('display_name', persona_data['persona_id'])}")
    print(f"Running {args.turns} turns ...\n")

    from energybridge.simulation import user as user_mod
    _orig_init = user_mod.SimulatedUser.__init__

    def _patched_init(self, roleplay=None):
        _orig_init(self, roleplay)
        self.persona = persona_data
        self.persona_trace = {"data": persona_data}

    user_mod.SimulatedUser.__init__ = _patched_init

    from energybridge.simulation.simulation import run_roleplay_simulation
    result = run_roleplay_simulation(turns=args.turns, output_root=args.output_root)
    user_mod.SimulatedUser.__init__ = _orig_init

    summary = result.get("summary", {})
    print("\n=== Role-Play Complete ===")
    print(f"Artifacts: {result.get('user_dir')}")
    print("\n=== Learning Summary ===")
    print(json.dumps(summary.get("learning_summary", {}), ensure_ascii=False, indent=2))
    print("\n=== Turn Overview ===")
    for item in summary.get("turn_overview", []):
        print(json.dumps(item, ensure_ascii=False))


if __name__ == "__main__":
    main()
