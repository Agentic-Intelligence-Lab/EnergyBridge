"""Grid/VPP simulation object.

This class is the single simulation-facing entrypoint for upstream grid signals.
It uses the real VPP-1 runner and adapts VPP-1 task/query outputs into the
EnergyBridge internal grid signal schema.
"""

from __future__ import annotations

from copy import deepcopy

from energybridge.grid.vpp_1.adapter import adapt_vpp1_result_to_grid_signal, load_vpp1_dispatch


class GridSimulator:
    """Generate VPP-1-backed grid scenarios for a simulation turn."""

    def get_scenario(self, turn_index: int, home_state: dict, mode: str | None = None) -> dict:
        vpp_mode = mode or ("invitation" if turn_index % 2 == 1 else "emergency")
        vpp_result = load_vpp1_dispatch(mode=vpp_mode)
        grid_signal, vpp_task, vpp_query = adapt_vpp1_result_to_grid_signal(
            vpp_result,
            home_state,
        )
        return {
            "vpp_mode": vpp_mode,
            "grid_signal_source": f"vpp_1:{vpp_mode}",
            "vpp_task": vpp_task,
            "vpp_query": vpp_query,
            "grid_signal": grid_signal,
            "home_state": deepcopy(home_state),
        }
