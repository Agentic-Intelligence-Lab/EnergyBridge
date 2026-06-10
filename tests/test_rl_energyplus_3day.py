import numpy as np

from baselines.rl_energyplus_3day.environment import EnergyPlusFamilyEnv


def test_normalized_action_is_decoded_before_control():
    decoded = EnergyPlusFamilyEnv._decode_action(
        np.array([0.0, 0.0, 0.0], dtype=np.float32)
    )

    assert decoded.tolist() == [25.0, 0.5, 0.5]
