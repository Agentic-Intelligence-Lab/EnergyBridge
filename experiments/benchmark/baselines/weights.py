"""PDF v1.5 MPC objective weights."""

from __future__ import annotations

PDF_V15_WEIGHTS_DEFAULT = {
    "alpha_cost": 0.30,
    "alpha_user": 0.45,
    "alpha_grid": 0.25,
    "lambda_slack": 100.0,
    "q_time": 2.0,
    "kappa_rebound": 1.0,
    "delta_acc_default_c": 1.0,
}

PDF_V15_WEIGHTS_DR = {
    "alpha_cost": 0.25,
    "alpha_user": 0.40,
    "alpha_grid": 0.35,
    "lambda_slack": 100.0,
    "q_time": 2.0,
    "kappa_rebound": 1.0,
    "delta_acc_default_c": 1.0,
}


def pdf_v15_weights(*, dr_event: bool = False, **overrides: float) -> dict:
    """Return mutable PDF v1.5 objective weights with optional overrides."""
    weights = dict(PDF_V15_WEIGHTS_DR if dr_event else PDF_V15_WEIGHTS_DEFAULT)
    weights.update(overrides)
    return weights


__all__ = [
    "PDF_V15_WEIGHTS_DEFAULT",
    "PDF_V15_WEIGHTS_DR",
    "pdf_v15_weights",
]
