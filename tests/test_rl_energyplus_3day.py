import numpy as np

from baselines.rl_energyplus_3day.environment import EnergyPlusFamilyEnv
from energybridge.simulation.appliance_sim import ApplianceSuite
from experiments.benchmark.family_runner import VPP_EVENTS, _FamilyLoop


def test_normalized_action_is_decoded_before_control():
    decoded = EnergyPlusFamilyEnv._decode_action(
        np.array([0.0, 0.0, 0.0], dtype=np.float32)
    )

    assert decoded.tolist() == [25.0, 0.5, 0.5]


def test_observation_matches_agent_visible_context():
    env = EnergyPlusFamilyEnv("/tmp/energybridge_rl_observation_test")
    loop = _FamilyLoop()
    loop.appliance_suite = ApplianceSuite(
        env.persona["appliances"], sim_days=3, vpp_events=VPP_EVENTS
    )
    assessment = {
        "committable_kw": 1.8,
        "recommended_bid_kw": 0.9,
        "success_probability": 0.57,
        "main_constraints": ["water_heater:water_heater_at_setpoint", "washer:task_finished"],
    }

    observation = env._observation(18.0, 25.5, 32.0, True, assessment, loop)
    values = dict(zip(env.OBSERVATION_NAMES, observation.tolist()))

    assert observation.shape == env.observation_space.shape
    assert len(observation) == len(env.OBSERVATION_NAMES)
    assert "facility_power_kw" not in env.OBSERVATION_NAMES
    assert "capacity_success_probability" not in env.OBSERVATION_NAMES
    assert values["vpp_active"] == 1.0
    assert np.isclose(values["vpp_target_kwh_scaled"], 0.55)
    assert values["capacity_water_heater_constrained"] == 1.0
    assert values["capacity_washer_constrained"] == 1.0
    assert values["capacity_ev_constrained"] == 0.0
    assert values["washer_present"] == 1.0
    assert values["water_heater_present"] == 1.0
    assert values["ev_present"] == 0.0

    normal_observation = env._observation(17.0, 25.5, 32.0, False, assessment, loop)
    normal_values = dict(zip(env.OBSERVATION_NAMES, normal_observation.tolist()))
    assert normal_values["capacity_committable_kw_scaled"] == 0.0
    assert normal_values["capacity_water_heater_constrained"] == 0.0
