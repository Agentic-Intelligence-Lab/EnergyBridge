"""Household demand-response capacity quantification."""

from .baseline_model import (
    BaselinePrediction,
    DeviceAwareBaselineModel,
    TimeSlotBaselineModel,
    WeatherAdjustedTimeSlotBaselineModel,
    evaluate_predictions,
    evaluate_weather_adjusted_predictions,
)
from .capacity_estimator import assess_vpp_request, estimate_dr_potential
from .event_baseline import (
    EventBaselineConfig,
    estimate_event_baseline_and_shed,
    estimate_vpp_event_baselines,
)
from .suite_adapter import assess_suite_vpp_request, suite_capacity_inputs
from .total_quantification import quantify_agent_vpp_events

__all__ = [
    "BaselinePrediction",
    "DeviceAwareBaselineModel",
    "TimeSlotBaselineModel",
    "WeatherAdjustedTimeSlotBaselineModel",
    "evaluate_predictions",
    "evaluate_weather_adjusted_predictions",
    "assess_vpp_request",
    "estimate_dr_potential",
    "EventBaselineConfig",
    "estimate_event_baseline_and_shed",
    "estimate_vpp_event_baselines",
    "assess_suite_vpp_request",
    "suite_capacity_inputs",
    "quantify_agent_vpp_events",
]
