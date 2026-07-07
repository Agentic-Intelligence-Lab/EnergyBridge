from __future__ import annotations

import json
import re
from pathlib import Path

from experiments.benchmark.strategy_explanations import (
    build_vpp_strategy_explanation,
    collect_strategy_explanation_records,
    normalize_vpp_strategy_explanation,
    write_strategy_explanation_artifacts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


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
    assert explanation["language"] == "en-US"
    assert explanation["persona_role"] == "F"
    assert "VPP" in explanation["why_request"]
    assert len(explanation["alternatives"]) >= 2
    assert any(item["device"] == "ev" for item in explanation["recommended_actions"])
    assert any("SOC" in item for item in explanation["protected_constraints"])
    assert "\n\n" in explanation["natural_language"]
    assert "I would use" in explanation["natural_language"]
    assert "saved preference profile" in explanation["natural_language"]
    assert "EV routine requires" in explanation["natural_language"]
    assert "Recommended strategy" not in explanation["natural_language"]
    assert explanation["structured_control_constraints"]["hvac"]["setpoint_c"] == 26.0
    assert all(explanation["review_dimensions"].values())
    assert CJK_RE.search(json.dumps(explanation, ensure_ascii=False)) is None


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
    markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "VPP Strategy Explanation Review Data" in markdown
    assert "Preference basis:" not in markdown
    assert "saved preference profile" in markdown
    for path in paths.values():
        assert CJK_RE.search(Path(path).read_text(encoding="utf-8")) is None


def test_normalize_strategy_explanation_drops_cjk_llm_fields() -> None:
    persona = _persona("basic_role_b_home_comfort_gated")
    explanation = normalize_vpp_strategy_explanation(
        {
            "natural_language": "\u4e2d\u6587",
            "why_request": "\u4e2d\u6587",
            "alternatives": [{"name": "\u4fdd\u5b88\u65b9\u6848"}],
            "expected_benefit": {"message": "\u4e2d\u6587"},
        },
        persona_config=persona,
        appliance_config=persona["appliances"],
        event={"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1},
        setpoint_c=25.5,
        reason="\u4e2d\u6587",
        appliance_actions={"water_heater_preheat_start_h": 15.0, "water_heater_preheat_end_h": 17.0},
        demand_context={"target_shed_kw": 0.5},
        method="EnergyBridge",
        city="Germany",
    )

    assert "I would use" in explanation["natural_language"]
    assert "Recommended strategy" not in explanation["natural_language"]
    assert explanation["llm_raw_explanation"] == {"omitted": "non_english_text_detected"}
    assert explanation["agent_reason"] == ""
    assert CJK_RE.search(json.dumps(explanation, ensure_ascii=False)) is None
