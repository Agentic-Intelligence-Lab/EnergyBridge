"""Compatibility shim for the MPC baseline.

New code should import ``plan_mpc_action`` from
``experiments.benchmark.baselines.mpc``.
"""

from .mpc import plan_mpc_action

__all__ = ["plan_mpc_action"]
