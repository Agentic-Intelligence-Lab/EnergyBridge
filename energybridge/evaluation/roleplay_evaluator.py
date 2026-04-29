"""Compatibility wrappers for role-play simulations.

The implementation now lives in `energybridge.simulation.simulation`, where the
simulation is organized around User, Agent, Grid, and Home objects. This module
keeps the previous import path stable for examples and notebooks.
"""

from __future__ import annotations

from energybridge.simulation.simulation import (
    run_batch_roleplay_simulation,
    run_roleplay_simulation,
)


def run_roleplay_evaluation(turns: int = 5, output_root: str = "logs/evaluations") -> dict:
    return run_roleplay_simulation(turns=turns, output_root=output_root)


def run_batch_roleplay_evaluation(
    user_count: int = 10,
    turns: int = 5,
    output_root: str = "logs/evaluations",
) -> dict:
    return run_batch_roleplay_simulation(
        user_count=user_count,
        turns=turns,
        output_root=output_root,
    )
