"""LLM-based persona seed generator.

Usage:
    python -m energybridge.roleplay.generator --n 20 [--output-dir /path] [--dry-run]

Generates N JSON persona files in the personas/ directory (or custom output-dir).
All generated personas start with meta.approved=false. Review and set to true manually.
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from energybridge.llm.client import LLMClient
from energybridge.roleplay.schema import VALID_TAGS, validate_persona

_DEFAULT_OUT = Path(__file__).parent / "personas"

# 20 seed combinations covering all 6 taxonomy dimensions
_SEED_COMBINATIONS: list[dict] = [
    {"schedule":"regular_commuter","comfort":"temp_tolerant","task":"flexible","price":"price_sensitive","control":"suggestion_first","grid_value":"evening_peak"},
    {"schedule":"regular_commuter","comfort":"normal_comfort","task":"semi_rigid","price":"needs_explanation","control":"confirm_required","grid_value":"stable_flex"},
    {"schedule":"stay_at_home","comfort":"temp_sensitive","task":"rigid","price":"low_incentive","control":"confirm_required","grid_value":"short_peak_cut"},
    {"schedule":"stay_at_home","comfort":"normal_comfort","task":"flexible","price":"price_sensitive","control":"high_trust_auto","grid_value":"stable_flex"},
    {"schedule":"stay_at_home","comfort":"temp_tolerant","task":"flexible","price":"event_fatigue","control":"suggestion_first","grid_value":"uncertain_flex"},
    {"schedule":"night_owl","comfort":"normal_comfort","task":"flexible","price":"event_fatigue","control":"suggestion_first","grid_value":"uncertain_flex"},
    {"schedule":"night_owl","comfort":"temp_tolerant","task":"flexible","price":"price_sensitive","control":"high_trust_auto","grid_value":"stable_flex"},
    {"schedule":"night_owl","comfort":"temp_sensitive","task":"semi_rigid","price":"low_incentive","control":"privacy_sensitive","grid_value":"short_peak_cut"},
    {"schedule":"irregular","comfort":"normal_comfort","task":"rigid","price":"needs_explanation","control":"confirm_required","grid_value":"uncertain_flex"},
    {"schedule":"irregular","comfort":"temp_tolerant","task":"flexible","price":"price_sensitive","control":"high_trust_auto","grid_value":"stable_flex"},
    {"schedule":"irregular","comfort":"temp_sensitive","task":"semi_rigid","price":"event_fatigue","control":"suggestion_first","grid_value":"short_peak_cut"},
    {"schedule":"caregiver","comfort":"temp_sensitive","task":"rigid","price":"low_incentive","control":"low_auto_accept","grid_value":"low_value"},
    {"schedule":"caregiver","comfort":"normal_comfort","task":"semi_rigid","price":"needs_explanation","control":"confirm_required","grid_value":"short_peak_cut"},
    {"schedule":"regular_commuter","comfort":"normal_comfort","task":"ev_constrained","price":"price_sensitive","control":"high_trust_auto","grid_value":"stable_flex"},
    {"schedule":"regular_commuter","comfort":"temp_tolerant","task":"ev_constrained","price":"price_sensitive","control":"high_trust_auto","grid_value":"evening_peak"},
    {"schedule":"stay_at_home","comfort":"low_control_tolerance","task":"semi_rigid","price":"needs_explanation","control":"privacy_sensitive","grid_value":"uncertain_flex"},
    {"schedule":"irregular","comfort":"temp_tolerant","task":"ev_constrained","price":"needs_explanation","control":"suggestion_first","grid_value":"uncertain_flex"},
    {"schedule":"night_owl","comfort":"normal_comfort","task":"flexible","price":"price_sensitive","control":"confirm_required","grid_value":"evening_peak"},
    {"schedule":"regular_commuter","comfort":"temp_sensitive","task":"rigid","price":"event_fatigue","control":"low_auto_accept","grid_value":"low_value"},
    {"schedule":"stay_at_home","comfort":"temp_tolerant","task":"flexible","price":"price_sensitive","control":"high_trust_auto","grid_value":"stable_flex"},
]

_TAG_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "schedule": {
        "regular_commuter": "leaves home ~8:00, returns ~18:00 on weekdays",
        "stay_at_home": "occupies the home all day",
        "night_owl": "active in the evenings and late nights, sleeps past midnight",
        "irregular": "unpredictable schedule, sometimes home early, sometimes very late",
        "caregiver": "stays home to care for elderly parents or young children",
    },
    "comfort": {
        "temp_tolerant": "comfortable in a wide range (20-28 C)",
        "normal_comfort": "prefers 23-26 C",
        "temp_sensitive": "only comfortable in a narrow band (e.g., 23.5-25.5 C)",
        "low_control_tolerance": "dislikes any automated control, prefers to manage manually",
    },
    "task": {
        "flexible": "appliance schedules are flexible and can be shifted freely",
        "semi_rigid": "some tasks have time windows but can be moved within them",
        "rigid": "strict appliance schedules, cannot be changed",
        "ev_constrained": "also has an electric vehicle that needs charging overnight",
    },
    "price": {
        "price_sensitive": "actively motivated by electricity cost savings",
        "needs_explanation": "interested in savings but needs clear benefit explained",
        "low_incentive": "little financial motivation, comfort comes first",
        "event_fatigue": "tired of repeated DR events, needs strong justification",
    },
    "control": {
        "high_trust_auto": "fully trusts the agent to act autonomously",
        "suggestion_first": "prefers the agent to suggest before acting",
        "confirm_required": "must confirm each action explicitly before it happens",
        "privacy_sensitive": "uncomfortable sharing detailed energy data",
        "low_auto_accept": "very reluctant to accept automated control",
    },
    "grid_value": {
        "evening_peak": "provides flexibility mainly during evening peak hours",
        "stable_flex": "reliable and consistent demand-response flexibility",
        "uncertain_flex": "uncertain availability due to unpredictable schedule",
        "short_peak_cut": "can only do very short demand-response events (< 30 min)",
        "low_value": "very low grid flexibility value, usually unavailable for DR",
    },
}

_SYSTEM_PROMPT = (
    "You are an expert in residential energy demand-response and user behavioural modelling. "
    "Generate realistic home user persona profiles for simulation. "
    "Return ONLY valid JSON, with no extra text or markdown."
)

_JSON_SCHEMA = """{
  "schema_version": "1.0",
  "id": "<snake_case unique id>",
  "display_name": "<Short human-readable name>",
  "description": "<2-3 sentence description of this user type>",
  "tags": {
    "schedule": "<one of the valid schedule tags>",
    "comfort": "<one of the valid comfort tags>",
    "task": "<one of the valid task tags>",
    "price": "<one of the valid price tags>",
    "control": "<one of the valid control tags>",
    "grid_value": "<one of the valid grid_value tags>"
  },
  "preferences": {
    "temp_preferred_min": <float, min comfortable temp in C>,
    "temp_preferred_max": <float, max comfortable temp in C>,
    "temp_tolerance_c": <float, tolerance beyond min/max>,
    "scoring_weights": {"comfort": <0-1>, "energy": <0-1>, "vpp": <0-1>},
    "vpp_override_prob": <0.0-1.0, probability of refusing VPP>
  },
  "schedule": {
    "leaves_home_h": <float or null>,
    "returns_home_h": <float or null>,
    "sleep_h": <float>,
    "occupancy_pattern": "<string description>"
  },
  "appliances": {
    "washer": {
      "earliest_h": <float, earliest start hour>,
      "latest_h": <float, latest end hour>,
      "preferred_h": <float, preferred start hour>,
      "duration_h": 2.0,
      "power_kw": 1.5
    }
  },
  "llm_prompts": {
    "system_prompt": "<3-5 sentence role-playing instruction for this persona>",
    "agent_context": "<2-3 sentence summary for the home energy agent>",
    "example_responses": [
      "<example response 1>",
      "<example response 2>",
      "<example response 3>",
      "<example response 4>"
    ]
  },
  "meta": {
    "source": "llm_generated",
    "paper_role": null,
    "approved": false,
    "created_at": "<YYYY-MM-DD>"
  }
}"""


def _build_user_prompt(tags: dict) -> str:
    lines = ["Generate a home user persona with EXACTLY these behavioral tags:\n"]
    for dim, val in tags.items():
        desc = _TAG_DESCRIPTIONS.get(dim, {}).get(val, val)
        lines.append(f"  {dim}: {val}  ({desc})")
    lines.append(
        "\nReturn ONLY a JSON object matching this schema exactly:\n"
        + _JSON_SCHEMA
        + "\n\nIMPORTANT:"
        "\n- All text must be in English."
        "\n- scoring_weights must sum to 1.0 (+/- 0.01)."
        "\n- Tags in the output must exactly match the tags listed above."
        "\n- system_prompt and agent_context must be in English."
        "\n- Do not include any text before or after the JSON."
    )
    return "\n".join(lines)


def _extract_json(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = "\n".join(l for l in s.splitlines() if not l.strip().startswith("```")).strip()
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        return s[a:b+1]
    raise ValueError("No JSON object found in LLM response")


def generate_persona_seeds(
    n: int = 20,
    output_dir: Path | None = None,
    *,
    dry_run: bool = False,
    verbose: bool = True,
) -> list[Path]:
    """Generate n persona seed files via LLM. Returns list of paths written."""
    out = Path(output_dir) if output_dir else _DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    client = LLMClient(config_prefix="ROLEPLAY_LLM", use_key="ROLEPLAY_USE_LLM", fallback_prefix="LLM")
    seeds = _SEED_COMBINATIONS[:n]
    written: list[Path] = []
    import datetime
    today = datetime.date.today().isoformat()

    for i, tags in enumerate(seeds, 1):
        label = "_".join(v[:6] for v in tags.values())
        if verbose:
            print(f"[{i}/{len(seeds)}] Generating: {label} ...")
        user_prompt = _build_user_prompt(tags)
        try:
            result = client.chat_with_metrics(_SYSTEM_PROMPT, user_prompt)
            raw_json = _extract_json(result["text"])
            data = json.loads(raw_json)
            data["meta"]["source"] = "llm_generated"
            data["meta"]["approved"] = False
            data["meta"]["created_at"] = today
            for dim, val in tags.items():
                data["tags"][dim] = val  # enforce correct tags
            validate_persona(data)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue

        pid = data.get("id", label)
        path = out / f"{pid}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("meta", {}).get("approved", False):
                print(f"  SKIP: {path.name} already approved, not overwriting")
                continue

        if not dry_run:
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            written.append(path)
            if verbose:
                print(f"  Wrote: {path.name}")
        else:
            print(f"  [dry-run] Would write: {pid}.json")

    return written


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate persona seed files via LLM")
    parser.add_argument("--n", type=int, default=20, help="Number of seeds to generate (max 20)")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory for JSON files")
    parser.add_argument("--dry-run", action="store_true", help="Validate prompts without writing files")
    parser.add_argument("--verbose", action="store_true", default=True)
    args = parser.parse_args()
    paths = generate_persona_seeds(n=args.n, output_dir=args.output_dir,
                                   dry_run=args.dry_run, verbose=args.verbose)
    print(f"\nGenerated {len(paths)} persona files.")
