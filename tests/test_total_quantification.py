from energybridge.quantification import quantify_agent_vpp_events
from experiments.benchmark.family_runner import VPP_EVENTS


def test_reference_a3_total_quantification_matches_reference_one_hour_event():
    result = quantify_agent_vpp_events(VPP_EVENTS)["vpp1"]

    assert result["avg_expected_shed_kw"] == 6.509694
    assert result["avg_reported_capacity_90_kw"] == 2.307392
    assert result["firm_min_capacity_90_kw"] == 0.0
    assert result["expected_shed_energy_kwh"] == 6.509693
    assert result["reported_shed_90_energy_kwh"] == 2.307392
