# Physical Feedback Verification

*EnergyBridge benchmark — simulator interface audit*

**Status**: Snapshot-level EP feedback verified. Physical closed-loop response NOT yet verified.

---

## 1. Overview

This document audits exactly what physical data EnergyBridge currently receives from
EnergyPlus (EP), what is reflected from Python-side state, and what remains unverified
or not implemented.  It is intended to prevent over-claiming in benchmark results.

---

## 2. True EnergyPlus Variables (Directly Read)

These four variables are registered via `StateReader.request_variables()` and read
every timestep by `StateReader.read()` using EP variable handles:

| home_state field       | EP variable name                          | EP key            | Units  |
|------------------------|-------------------------------------------|-------------------|--------|
| `indoor_temp`          | Zone Mean Air Temperature                 | living_unit1      | °C     |
| `outdoor_temp`         | Zone Outdoor Air Drybulb Temperature      | living_unit1      | °C     |
| `hvac_power_kw`        | Cooling Coil Total Cooling Rate           | DX Cooling Coil_unit1 | kW (÷1000) |
| `facility_power_kw`    | Facility Total Electricity Demand Rate    | Whole Building    | kW (÷1000) |

Source: `energybridge/simulation/variable_catalog.py`, `energybridge/simulation/state_reader.py`

These are **reliably available** at the EnergyPlus callback
`callback_end_system_timestep_after_hvac_reporting` (post-HVAC reporting).

> **Note**: EP reports cooling coil rate in Watts; `state_reader.py` divides by 1000 to produce kW.
> `Facility Total Electricity Demand Rate` covers the entire building electrical load.

---

## 3. Python-Reflected State (NOT Read From EP)

| home_state field    | Source                                            | Notes                                          |
|---------------------|---------------------------------------------------|------------------------------------------------|
| `hvac_setpoint`     | `ActuatorWriter.last_cooling_setpoint` (Python)   | Reflects the last value *written* to EP, not read back from EP |
| `occupancy`         | Hardcoded `True`                                  | No EP occupancy variable requested or read     |

Source: `energybridge/simulation/eplus_env.py` — `home_state["hvac_setpoint"] = self._actuator_writer.last_cooling_setpoint`

### Why this matters
`execution_result.status = "executed"` means the Python `set_actuator_value()` call
succeeded (i.e., the API call did not throw).  It does **not** mean EnergyPlus has
thermally responded to the new setpoint.  The actual thermal impact (indoor temperature
change, HVAC power change) will appear in **subsequent** EP timesteps, not the current
one.

---

## 4. Actuator Writing (Control Feedback Path)

Actuators written by `ActuatorWriter`:

| Actuator name       | Component type    | Control type    | EP key       |
|---------------------|-------------------|-----------------|--------------|
| `cooling_setpoint`  | Schedule:Compact  | Schedule Value  | cooling_sch  |
| `heating_setpoint`  | Schedule:Compact  | Schedule Value  | heating_sch  |

Source: `energybridge/simulation/variable_catalog.py`, `energybridge/simulation/actuator_writer.py`

The heating setpoint is kept 2 °C below the cooling setpoint to prevent EP thermostat
conflict.  Setpoints are clamped to hard limits (`_COOLING_SETPOINT_MIN=18`,
`_COOLING_SETPOINT_MAX=30`).

---

## 5. What Is NOT Verified / Not Implemented

### 5a. Physical closed-loop response
Setpoint writeback → EP thermal model response **has not been verified** via time-series
data.  To verify, one would need to:

1. Parse the `.eso` output file produced by a full EP run
2. Extract the `Zone Mean Air Temperature` and `Cooling Coil Total Cooling Rate` time series
3. Compare pre-action vs. post-action values within the same simulation run

The `.eso` files exist under `logs/eplus_test_run*/` but are not currently parsed by
any EnergyBridge module.

### 5b. No ESO/CSV parser
There is no `eso_parser.py` or similar module.  Physical trajectory metrics (peak power
reduction, comfort violation minutes, setpoint tracking error, mean temperature deviation)
are marked as **`future_placeholder`** in `energybridge/evaluation/trajectory_metrics.py`.

### 5c. Occupancy sensor not connected
`home_state["occupancy"]` is hardcoded `True`.  EP has occupancy schedules in the IDF
but they are not read back via the Python API.

### 5d. Outdoor temperature coverage
In non-EP runs (baseline mode, mock runs), `home_state` does not contain `outdoor_temp`.
Only EP-based runs populate this field.

---

## 6. Reliable vs. Uncertain Fields Summary

| Field                  | Reliability           | Source type              |
|------------------------|-----------------------|--------------------------|
| `indoor_temp`          | Reliable (EP read)    | energyplus_snapshot      |
| `outdoor_temp`         | Reliable in EP runs   | energyplus_snapshot      |
| `hvac_power_kw`        | Reliable (EP read)    | energyplus_snapshot      |
| `facility_power_kw`    | Reliable (EP read)    | energyplus_snapshot      |
| `hvac_setpoint`        | Python-reflected only | python_reflected         |
| `occupancy`            | Hardcoded True        | hardcoded                |
| `estimated_reduction_kw` | LLM estimate        | agent_estimate           |
| `meets_vpp_requirement` | Proxy from estimate  | proxy                    |
| `actual_energy_kwh`    | NOT implemented       | future_placeholder       |
| `actual_peak_power_kw` | NOT implemented       | future_placeholder       |
| `comfort_violation_minutes` | NOT implemented  | future_placeholder       |

---

## 7. RA/Simulator Dependency

Implementing physical trajectory metrics requires:

1. **ESO parser** (new module, e.g. `energybridge/simulation/eso_parser.py`):
   - Read `logs/<run_dir>/eplusout.eso`
   - Extract time-indexed `Zone Mean Air Temperature` and `Cooling Coil Total Cooling Rate`
   - This is in RA/Xuebing's scope (do not modify `energybridge/simulation/` without coordination)

2. **AgentResult extension**:
   - Add `eso_path: str | None` to `AgentResult` dataclass in `eplus_env.py`
   - Pass EP working directory so `trajectory_metrics.py` can locate the `.eso` file

3. **Post-processing hook** (optional):
   - Call `eso_parser.parse_post_action_window(eso_path, event_hour, duration_minutes)` 
     after each EP run and merge results into `BenchmarkMetrics`

---

## 8. Tested Verification

The following was verified in a full EP + agent co-simulation run (`eplus_test_run3/`):

- `indoor_temp`, `outdoor_temp`, `hvac_power_kw`, `facility_power_kw` — all non-None
  at the VPP event callback
- `execution_result.status == "executed"` after writing cooling setpoint via actuator
- `sim_hour` correctly reflects cumulative simulation hours (bug fixed 2026-05-12, commit `1918ed3`)
- EP callback correctly fires at `end_system_timestep_after_hvac_reporting` so HVAC
  variables are populated (bug fixed, commit `99ce882`)

---

*Last updated: 2026-05  |  Author: xudongwu*
