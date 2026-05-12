# Unified Metric Schema

*EnergyBridge benchmark — metric field taxonomy*

**Version**: 0.2  |  **Module**: `energybridge/evaluation/trajectory_metrics.py`

---

## Overview

All benchmark metrics are represented as a flat Python `dict` with keys defined in
`REQUIRED_METRIC_FIELDS`.  Missing values are always `None` — never omitted, never
silently defaulted.

A `metric_status` sub-dict reports availability by category:

```python
{
  "api_metrics":                  "available" | "missing",
  "event_physical_snapshot":      "available" | "partial"  | "missing",
  "user_preference_metrics":      "available" | "missing",
  "physical_trajectory_metrics":  "not_implemented",
}
```

---

## Category 1: API / LLM Runtime

Fields populated from LLM call metadata.

| Field                  | Type    | Source             | Notes                                   |
|------------------------|---------|--------------------|---------------------------------------- |
| `api_latency_seconds`  | float   | llm_runtime        | Wall-clock seconds for all LLM calls    |
| `total_tokens`         | int     | llm_runtime        | Total input + output tokens             |
| `prompt_tokens`        | int     | llm_runtime        | Input tokens                            |
| `completion_tokens`    | int     | llm_runtime        | Output tokens                           |
| `llm_model`            | str     | llm_runtime        | e.g. `claude-sonnet-4-6`                |
| `llm_provider`         | str     | llm_runtime        | e.g. `openai_compatible`                |
| `api_success`          | bool    | llm_runtime        | False if LLM was not used               |

`None` for all fields in non-LLM baseline runs.

---

## Category 2: Control / Action

Fields representing the agent's decision and its execution status.

| Field                  | Type    | Source             | Notes                                   |
|------------------------|---------|--------------------|---------------------------------------- |
| `agent_triggered`      | bool    | api_control        | Always True if a trajectory exists      |
| `valid_control_plan`   | bool    | api_control        | True if `action` field is non-empty     |
| `action_type`          | str     | agent_estimate     | e.g. `set_hvac_temperature`             |
| `setpoint`             | float   | agent_estimate     | Target cooling setpoint (°C)            |
| `duration_minutes`     | int     | agent_estimate     | Event duration                          |
| `execution_status`     | str     | api_control        | `"executed"` / `"partial"` / `"skipped"` |
| `written_actuators`    | dict    | api_control        | `{"cooling_setpoint": val, ...}`        |
| `safety_ok`            | bool    | api_control        | From `SafetyChecker`                    |
| `safety_violations`    | list    | api_control        | Rule names that fired                   |

> **Note**: `execution_status == "executed"` means the Python `set_actuator_value()`
> call succeeded.  It does NOT confirm EnergyPlus thermal response.
> See `docs/physical_feedback_verification.md` §3.

---

## Category 3: VPP / Grid

Fields from the VPP operator request and agent response.

| Field                      | Type    | Source        | Notes                                        |
|----------------------------|---------|---------------|----------------------------------------------|
| `vpp_task_type`            | str     | vpp_context   | e.g. `INVITATION_DEMAND_RESPONSE`            |
| `requested_reduction_kw`   | float   | vpp_context   | Operator-requested capacity (kW)             |
| `estimated_reduction_kw`   | float   | agent_estimate| Agent's estimated power reduction (kW)       |
| `estimated_vpp_compliance` | bool    | proxy         | `estimated >= requested` (simple threshold)  |
| `event_start_time`         | str     | vpp_context   | e.g. `"18:00"`                               |
| `event_end_time`           | str     | vpp_context   | e.g. `"19:00"`                               |
| `trigger_hour`             | float   | vpp_context   | Simulation hour when event fires             |
| `sim_hour`                 | float   | api_control   | Actual sim hour at agent callback            |

> **Note**: `estimated_vpp_compliance` is a proxy metric.  It compares agent
> estimates, NOT actual measured power reduction from EP.

---

## Category 4: User Preference / Satisfaction

Fields from user interaction and preference learning.

| Field                      | Type    | Source         | Notes                                        |
|----------------------------|---------|----------------|----------------------------------------------|
| `user_satisfaction_score`  | int     | user_feedback  | 1–5 (None if not provided)                   |
| `comfort_score`            | float   | user_feedback  | Optional comfort rating (not always present) |
| `preference_learning_score`| float   | proxy          | Computed by `metrics.learning_score()`       |
| `comfort_priority`         | float   | user_feedback  | 0–1 learned weight for comfort               |
| `cost_priority`            | float   | user_feedback  | 0–1 learned weight for cost                  |
| `grid_priority`            | float   | user_feedback  | 0–1 learned weight for grid                  |
| `user_feedback_text`       | str     | user_feedback  | Free-text comment from user                  |

---

## Category 5: Physical Snapshot at Event

Single-point reading of building state at the VPP event callback.

| Field                       | Type    | Source                  | Notes                                  |
|-----------------------------|---------|-------------------------|----------------------------------------|
| `indoor_temp_at_event`      | float   | energyplus_snapshot     | °C from EP Zone Mean Air Temp          |
| `outdoor_temp_at_event`     | float   | energyplus_snapshot     | °C from EP Zone Outdoor Air Drybulb    |
| `hvac_power_kw_at_event`    | float   | energyplus_snapshot     | kW from EP Cooling Coil Total Rate     |
| `facility_power_kw_at_event`| float   | energyplus_snapshot     | kW from EP Facility Electricity Demand |
| `hvac_setpoint_at_event`    | float   | python_reflected        | Python-side last written setpoint      |
| `occupancy`                 | bool    | hardcoded               | Hardcoded True; not read from EP       |

> EP variables are available only in full EnergyPlus co-simulation runs.
> In baseline (mock) mode, values come from the scenario's `mock_home_state`.

---

## Category 6: Energy / Comfort Proxy

Derived estimates.  These are NOT physical measurements.

| Field                       | Type    | Source          | Notes                                        |
|-----------------------------|---------|-----------------|----------------------------------------------|
| `estimated_energy_kwh`      | float   | agent_estimate  | Agent-estimated energy over duration         |
| `estimated_cost`            | float   | agent_estimate  | Agent-estimated cost saving (if present)     |
| `simple_temp_deviation`     | float   | proxy           | `setpoint − indoor_temp_at_event` (°C)       |
| `comfort_violation_flag`    | bool    | proxy           | True if `indoor_temp > preferred_temp_max`   |

---

## Category 7: Future Physical Trajectory (Placeholders)

These fields require `.eso` time-series parsing.  They are `None` in all current
benchmark runs.  See `docs/physical_feedback_verification.md` §7 for RA dependencies.

| Field                       | Source              | Required for                              |
|-----------------------------|---------------------|-------------------------------------------|
| `actual_energy_kwh`         | future_placeholder  | True energy measurement post-event        |
| `actual_peak_power_kw`      | future_placeholder  | Peak demand during event                  |
| `actual_peak_reduction_kw`  | future_placeholder  | Peak demand delta vs. baseline            |
| `comfort_violation_minutes` | future_placeholder  | Minutes where T > preferred_temp_max      |
| `mean_temperature_deviation`| future_placeholder  | Mean \|T − setpoint\| over event window  |
| `setpoint_tracking_error`   | future_placeholder  | Mean \|T − setpoint\| (control quality)  |
| `post_action_temperature_delta` | future_placeholder | ΔT from event start to end             |

Implementation path: parse `logs/<run_dir>/eplusout.eso` after each EP run,
call (future) `eso_parser.parse_post_action_window()`, merge into `BenchmarkMetrics`.

---

## Source Labels

| Source label            | Meaning                                               |
|-------------------------|-------------------------------------------------------|
| `llm_runtime`           | Measured from LLM API call                            |
| `api_control`           | From agent graph execution                            |
| `agent_estimate`        | Agent LLM output (not physically verified)            |
| `vpp_context`           | From VPP operator request                             |
| `proxy`                 | Derived / computed from other fields                  |
| `user_feedback`         | From user interaction                                 |
| `energyplus_snapshot`   | Directly read from EnergyPlus via Python API          |
| `python_reflected`      | Python-side value, not read from EP                   |
| `hardcoded`             | Fixed value, not from simulation or user              |
| `future_placeholder`    | Not implemented; requires `.eso` parsing              |

---

*Last updated: 2026-05  |  Author: xudongwu*
