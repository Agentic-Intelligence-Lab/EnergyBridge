from __future__ import annotations

import json
from pathlib import Path

from experiments.benchmark.strategy_explanations import (
    build_vpp_strategy_explanation,
    collect_strategy_explanation_records,
    write_strategy_explanation_artifacts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"


def _persona(persona_id: str) -> dict:
    return json.loads((PERSONA_DIR / f"{persona_id}.json").read_text(encoding="utf-8"))


def test_vpp_strategy_explanation_contains_review_required_fields_for_ev_user() -> None:
    persona = _persona("basic_role_f_commuter_ev_optimizer")
    event = {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1}
    actions = {
        "washer_start_h": 20.0,
        "washer_skip": False,
        "water_heater_preheat_start_h": 15.0,
        "water_heater_preheat_end_h": 17.0,
        "water_heater_preheat_temp_c": 55.0,
        "water_heater_preheat": True,
        "ev_mode": "smart",
        "ev_charge_start_h": 19.0,
        "ev_charge_end_h": 23.9,
    }

    explanation = build_vpp_strategy_explanation(
        persona_config=persona,
        appliance_config=persona["appliances"],
        event=event,
        setpoint_c=26.0,
        reason="EV charge shifted after VPP",
        appliance_actions=actions,
        demand_context={"target_shed_kw": 1.2, "target_shed_kwh": 1.2},
        method="EnergyBridge",
        city="Germany",
    )

    assert explanation["schema_version"] == "vpp_strategy_explanation_v1"
    assert explanation["persona_role"] == "F"
    assert "VPP" in explanation["why_request"]
    assert len(explanation["alternatives"]) >= 2
    assert any(item["device"] == "ev" for item in explanation["recommended_actions"])
    assert any("SOC" in item for item in explanation["protected_constraints"])
    assert explanation["structured_control_constraints"]["hvac"]["setpoint_c"] == 26.0
    assert all(explanation["review_dimensions"].values())


def test_collect_and_write_strategy_explanation_artifacts(tmp_path: Path) -> None:
    persona = _persona("basic_role_a_commuter_price_cooperative")
    explanation = build_vpp_strategy_explanation(
        persona_config=persona,
        appliance_config=persona["appliances"],
        event={"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1},
        setpoint_c=26.0,
        reason="shift evening loads",
        appliance_actions={
            "washer_start_h": 19.0,
            "washer_skip": False,
            "dishwasher_start_h": 21.0,
            "dishwasher_skip": False,
            "water_heater_preheat_start_h": 15.0,
            "water_heater_preheat_end_h": 17.0,
            "water_heater_preheat_temp_c": 55.0,
            "water_heater_preheat": True,
        },
        demand_context={"target_shed_kw": 0.5},
        method="EnergyBridge",
        city="Germany",
    )

    class Result:
        method = "EnergyBridge"
        vpp_event_log = [
            {
                "id": "vpp1",
                "day": 1,
                "trigger_h": 18.0,
                "end_h": 19.0,
                "setpoint": 26.0,
                "reason": "shift evening loads",
                "score": 5,
                "strategy_explanation": explanation,
            }
        ]

    records = collect_strategy_explanation_records(Result(), persona, "Germany")
    paths = write_strategy_explanation_artifacts(records, tmp_path)

    assert len(records) == 1
    assert Path(paths["jsonl"]).read_text(encoding="utf-8").count("\n") == 1
    assert "basic_role_a_commuter_price_cooperative" in Path(paths["csv"]).read_text(encoding="utf-8")
    assert "VPP Strategy Explanation Review Data" in Path(paths["markdown"]).read_text(encoding="utf-8")
