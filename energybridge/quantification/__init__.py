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
from .counterfactual_baseline import (
    apply_counterfactual_baseline,
    build_counterfactual_library,
    extract_counterfactual_baseline,
    find_matching_baseline,
)
from .agent_capacity_reporter import (
    apply_agent_capacity_reporting,
    report_event_capacity_with_agent,
)
from .dr_event_memory import (
    apply_dr_memory_capacity_estimate,
    build_dr_event_memory,
    estimate_event_capacity_from_memory,
    extract_dr_event_record,
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
    "apply_counterfactual_baseline",
    "build_counterfactual_library",
    "extract_counterfactual_baseline",
    "find_matching_baseline",
    "apply_agent_capacity_reporting",
    "report_event_capacity_with_agent",
    "apply_dr_memory_capacity_estimate",
    "build_dr_event_memory",
    "estimate_event_capacity_from_memory",
    "extract_dr_event_record",
    "assess_suite_vpp_request",
    "suite_capacity_inputs",
    "quantify_agent_vpp_events",
]
