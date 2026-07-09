from __future__ import annotations

import json

from energybridge.quantification.agent_capacity_reporter import apply_agent_capacity_reporting


class FakeCapacityLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[tuple[str, str]] = []

    def chat_with_metrics(self, system_prompt: str, user_prompt: str, **_: object) -> dict:
        self.prompts.append((system_prompt, user_prompt))
        return {"text": json.dumps(self.payload), "metrics": {"used": True, "fake": True}}


def test_agent_capacity_reporter_recommends_llm_quantile_from_precomputed_options() -> None:
    memory = {
        "events": [
            {
                "memory_event_id": f"hist{i}",
                "entity_id": "household_s1",
                "city": "Germany",
                "method": "EnergyBridge",
                "hour_of_day": 18.0,
                "duration_h": 1.0,
                "memory_source_day": i,
                "no_dr_baseline_kwh": 10.0,
                "realized_delivery_kw": kw,
                "realized_delivery_kwh": kw,
            }
            for i, kw in enumerate([2.0, 3.0, 4.0, 5.0, 6.0], start=1)
        ]
    }
    result = {
        "method": "EnergyBridge",
        "weather": "Germany",
        "user_pref_score": 4.8,
        "stable_preferences": {"grid_priority": "high", "comfort_priority": "medium"},
        "vpp_event_log": [
            {
                "id": "future1",
                "day": 1,
                "trigger_h": 18.0,
                "end_h": 19.0,
                "counterfactual_baseline_kwh": 10.0,
            }
        ],
    }
    llm = FakeCapacityLLM(
        {
            "recommended_quantile": "p90",
            "preference_profile": "grid_cooperative",
            "strategy_bias": "savings_first",
            "risk_level": "medium",
            "reason": "History is reliable and the user is grid cooperative.",
        }
    )

    updated = apply_agent_capacity_reporting(
        result,
        memory,
        metadata={"household_id": "household_s1", "method": "EnergyBridge", "city": "Germany"},
        client=llm,
        top_k=5,
    )

    report = updated["vpp_event_log"][0]["agent_capacity_report"]
    assert report["recommended_quantile"] == "p90"
    assert report["preference_profile"] == "grid_cooperative"
    assert report["reported_capacity_kw"] == report["vpp_capacity_options"]["options"]["p90"]["reported_capacity_kw"]
    assert set(report["vpp_capacity_options"]["options"]) == {"p50", "p70", "p90"}
    assert updated["agent_capacity_report_primary_recommended_quantile"] == "p90"
    assert "recommended_quantile must be one of p50, p70, p90" in llm.prompts[0][1]
