# Sample Metric Walkthrough — Benchmark Dry Run

**Date**: 2026-05-12  
**Author**: xudongwu  
**Purpose**: Concrete walkthrough of one complete benchmark run. Demonstrates
pipeline health — not a validated EnergyPlus physical benchmark.

---

## A. Run Information

### Baseline Run (primary walkthrough)

| Item | Value |
|------|-------|
| Command | `python examples/run_benchmark_smoke.py --scenario data/scenarios/us_chicago_vpp_smoke.json --agent rule_based_balanced` |
| Scenario ID | `us_chicago_vpp_smoke` |
| Agent ID | `rule_based_balanced` |
| Output directory | `logs/benchmark_runs/us_chicago_vpp_smoke/20260512_201839/` |
| Run type | **Baseline / mock run — no EnergyPlus, no LLM** |

> **Important**: `rule_based_balanced` is a deterministic rule-based baseline.
> It does NOT launch EnergyPlus.  The `home_state` values are a fixed mock dict
> (`indoor_temp=24.5°C, outdoor_temp=22.0°C, hvac_power_kw=2.2 kW,
> facility_power_kw=3.1 kW`).  This run **does not** validate the EnergyPlus
> physical closed loop.

### Current-Agent Run (EP comparison)

| Item | Value |
|------|-------|
| Command | `python examples/run_benchmark_smoke.py --scenario data/scenarios/us_chicago_vpp_smoke.json --agent current` |
| Scenario ID | `us_chicago_vpp_smoke` |
| Agent ID | `current` |
| Output directory | `logs/benchmark_runs/us_chicago_vpp_smoke/20260512_201930/` |
| Run type | **Full EnergyPlus co-simulation** (exit code 0) |

---

## B. Raw Result Summary

### `raw_agent_result.json` (baseline run)

```json
{
  "sim_hour": 42.0,
  "home_state": {
    "indoor_temp": 24.5,
    "outdoor_temp": 22.0,
    "hvac_power_kw": 2.2,
    "facility_power_kw": 3.1,
    "hvac_setpoint": 25.0,
    "occupancy": true
  },
  "control_plan": {
    "action": "set_hvac_temperature",
    "setpoint": 27.0,
    "duration_minutes": 60,
    "estimated_power_kw": 1.7,
    "estimated_reduction_kw": 0.5,
    "controller": "rule_based_balanced_v0",
    "notes": "urgency=medium; req=0.5kW; delta=2.0°C."
  },
  "safety_report": {
    "safe": true,
    "violations": [],
    "source": "runner_check"
  },
  "execution_result": {
    "status": "simulated",
    "source": "baseline_runner"
  },
  "final_response": "[rule_based_balanced] setpoint=27.0°C",
  "trajectory": []
}
```

**Field notes:**
- `sim_hour=42.0`: set from scenario `trigger_hour`; not from a live EP simulation
- `home_state`: **mock values** injected by `_run_baseline()` in the runner
- `control_plan.setpoint=27.0`: rule output = `25.0 + delta(urgency=medium, req=0.5kW, hvac=2.2kW)` = `25.0 + 2.0 = 27.0°C`
- `execution_result.status="simulated"`: no EP actuator was written; label is set by the baseline runner
- `trajectory=[]`: no LangGraph node execution in baseline mode

---

## C. Metrics Summary

### `metrics.json` (baseline run)

```json
{
  "agent_triggered": true,
  "valid_control_plan": true,
  "action_type": "set_hvac_temperature",
  "setpoint": 27.0,
  "execution_status": "simulated",
  "safety_ok": true,
  "requested_reduction_kw": 0.5,
  "estimated_reduction_kw": 0.5,
  "estimated_vpp_compliance": true,
  "indoor_temp_at_event": 24.5,
  "outdoor_temp_at_event": 22.0,
  "setpoint_after_action": 27.0,
  "simple_temp_deviation": 2.5,
  "hvac_power_kw_at_event": 2.2,
  "facility_power_kw_at_event": 3.1,
  "sim_hour": 42.0,
  "scenario_id": "us_chicago_vpp_smoke",
  "agent_id": "rule_based_balanced"
}
```

### `unified_metrics.json` — key fields (baseline run)

| Field | Value | Note |
|-------|-------|------|
| `agent_triggered` | `true` | agent ran |
| `valid_control_plan` | `true` | action field non-empty |
| `action_type` | `set_hvac_temperature` | |
| `setpoint` | `27.0` | °C |
| `execution_status` | `simulated` | mock only |
| `safety_ok` | `true` | setpoint in [18,30] |
| `requested_reduction_kw` | `0.5` | from scenario |
| `estimated_reduction_kw` | `0.5` | rule estimate |
| `estimated_vpp_compliance` | `true` | 0.5 ≥ 0.5 |
| `indoor_temp_at_event` | `24.5` | **mock** |
| `outdoor_temp_at_event` | `22.0` | **mock** |
| `hvac_power_kw_at_event` | `2.2` | **mock** |
| `facility_power_kw_at_event` | `3.1` | **mock** |
| `hvac_setpoint_at_event` | `25.0` | python-reflected |
| `simple_temp_deviation` | `2.5` | `27.0 − 24.5` |
| `api_latency_seconds` | `null` | no LLM |
| `total_tokens` | `null` | no LLM |
| `llm_model` | `null` | no LLM |
| `user_satisfaction_score` | `null` | no feedback session |
| `actual_energy_kwh` | `null` | future placeholder |
| `comfort_violation_minutes` | `null` | future placeholder |

### `metric_status` block

```json
{
  "api_metrics":                 "missing",
  "event_physical_snapshot":     "available",
  "user_preference_metrics":     "missing",
  "physical_trajectory_metrics": "not_implemented"
}
```

### `summary.md` (baseline run)

```markdown
# Benchmark Smoke-Test Summary

**Scenario :** us_chicago_vpp_smoke
**Agent    :** rule_based_balanced
**Run time :** 2026-05-12 20:18:39

## Control Outcome
| agent_triggered       | True           |
| valid_control_plan    | True           |
| action_type           | set_hvac_temperature |
| setpoint_after_action | 27.0 °C        |
| execution_status      | simulated      |
| safety_ok             | True           |

## VPP Compliance
| requested_reduction_kw    | 0.5 kW |
| estimated_reduction_kw    | 0.5 kW |
| estimated_vpp_compliance  | True   |

## Building State at Event
| sim_hour                  | 42.0   |
| indoor_temp_at_event      | 24.5 °C |
| outdoor_temp_at_event     | 22.0 °C |
| hvac_power_kw_at_event    | 2.2 kW |
| facility_power_kw_at_event| 3.1 kW |
| simple_temp_deviation     | 2.5 °C |
```

---

## D. How Each Metric Is Generated

| Metric | Source | Code path |
|--------|--------|-----------|
| `agent_triggered` | Trajectory / AgentResult exists | `extract_metrics_from_agent_result()`: hardcoded `True` when result object is present |
| `valid_control_plan` | `control_plan.action` is non-empty | `bool(cp.get("action"))` |
| `action_type` | `control_plan.action` | direct field read |
| `setpoint` | `control_plan.setpoint` | direct field read |
| `duration_minutes` | `control_plan.duration_minutes` | direct field read |
| `execution_status` | `execution_result.status` | `"executed"` in EP run; `"simulated"` in baseline |
| `safety_ok` | `safety_report.safe` | `SafetyChecker` in EP run; runner range-check in baseline |
| `requested_reduction_kw` | `scenario.vpp_context.requested_reduction_kw` | from scenario JSON |
| `estimated_reduction_kw` | `control_plan.estimated_reduction_kw` | agent or rule estimate |
| `estimated_vpp_compliance` | `estimated_reduction_kw >= requested_reduction_kw` | computed in `trajectory_metrics.py` |
| `indoor_temp_at_event` | `home_state.indoor_temp` | EP: `Zone Mean Air Temperature` via `StateReader`; baseline: mock constant |
| `outdoor_temp_at_event` | `home_state.outdoor_temp` | EP: `Zone Outdoor Air Drybulb Temperature`; baseline: mock |
| `hvac_power_kw_at_event` | `home_state.hvac_power_kw` | EP: `Cooling Coil Total Cooling Rate ÷ 1000`; baseline: mock |
| `facility_power_kw_at_event` | `home_state.facility_power_kw` | EP: `Facility Total Electricity Demand Rate ÷ 1000`; baseline: mock |
| `simple_temp_deviation` | `setpoint − indoor_temp_at_event` | computed: `27.0 − 24.5 = 2.5°C` |
| `api_latency_seconds` | LLM call timing | `llm_metrics.latency_seconds`; `null` for rule-based |
| `total_tokens` | LLM token count | `llm_metrics.token_usage.total_tokens`; `null` for rule-based |
| `llm_model` | LLM model name | `llm_metrics.model`; `null` for rule-based |
| `user_satisfaction_score` | User feedback session | `user_feedback.satisfaction_score`; `null` in smoke run |

---

## E. Metric Source Classification

### 1. API / Control Metrics
*Sourced from the agent graph execution or runner logic.*

| Metric | Value (baseline) | Value (EP run) |
|--------|-----------------|----------------|
| `agent_triggered` | `true` | `true` |
| `valid_control_plan` | `true` | `true` |
| `action_type` | `set_hvac_temperature` | `set_hvac_temperature` |
| `setpoint` | `27.0°C` | `26.5°C` |
| `execution_status` | `simulated` | `executed` |
| `safety_ok` | `true` | `true` |

### 2. Event-Level Physical / Mock Snapshot
*EP run: directly read from EnergyPlus via `StateReader` at the VPP callback timestep.*  
*Baseline: fixed mock constants in `_run_baseline()`.*

| Metric | Baseline (MOCK) | EP Run (real EP read) |
|--------|-----------------|-----------------------|
| `indoor_temp_at_event` | 24.5°C (mock) | **23.89°C** (EP: Zone Mean Air Temp) |
| `outdoor_temp_at_event` | 22.0°C (mock) | **21.69°C** (EP: Zone Outdoor Air Drybulb) |
| `hvac_power_kw_at_event` | 2.2 kW (mock) | **2.174 kW** (EP: Cooling Coil Rate ÷ 1000) |
| `facility_power_kw_at_event` | 3.1 kW (mock) | **0.846 kW** (EP: Facility Electricity ÷ 1000) |

> **Note on hvac_setpoint_at_event**: This field is `python_reflected` in **both** runs.
> It comes from `ActuatorWriter.last_cooling_setpoint` (Python-side), not from an EP
> output variable read.  It reflects the last setpoint *written* to EP, not confirmed
> by EP's thermal model.

### 3. Agent-Estimated / Rule-Estimated Metrics
*Agent LLM output or rule logic output — not physical measurements.*

| Metric | Value | Note |
|--------|-------|------|
| `estimated_reduction_kw` | 0.5 kW (baseline) / 0.4 kW (EP) | Rule: proportional to req/hvac ratio |
| `estimated_vpp_compliance` | `true` (baseline) / `false` (EP) | Threshold: 0.5 ≥ 0.5 / 0.4 < 0.5 |
| `estimated_energy_kwh` | `null` | Not computed in either run |

### 4. API / LLM Runtime Metrics
*From LLM call metadata; only available in runs that call the LLM.*

| Metric | Baseline | EP Run | Note |
|--------|----------|--------|------|
| `api_latency_seconds` | `null` | `null` | Neither run used LLM in these smoke tests |
| `total_tokens` | `null` | `null` | |
| `llm_model` | `null` | `null` | |

> **Observation**: The `current` agent EP run also shows `api_metrics: missing`.
> This is because the EP run used the rule-based MPC controller path, not the full
> LLM path.  LLM metrics become available when the agent's LLM strategy node is
> invoked (requires `ANTHROPIC_API_KEY` and a working LLM call).

### 5. User Feedback Metrics
*From interactive user sessions; not available in smoke runs.*

| Metric | Value | Note |
|--------|-------|------|
| `user_satisfaction_score` | `null` | No interactive session |
| `comfort_score` | `null` | |
| `user_feedback_text` | `null` | |

### 6. Future Physical Trajectory Metrics (Placeholders)
*These fields require `.eso` / `.csv` time-series parsing after a full EP run.*
*Currently `null` in ALL runs.*

| Metric | Status | What's needed |
|--------|--------|---------------|
| `actual_energy_kwh` | `null` — future placeholder | Parse `eplusout.eso` electricity time series |
| `actual_peak_power_kw` | `null` — future placeholder | Peak demand from `.eso` |
| `actual_peak_reduction_kw` | `null` — future placeholder | Δ demand vs. baseline run |
| `comfort_violation_minutes` | `null` — future placeholder | Minutes where `Zone Mean Air Temp > preferred_max` |
| `mean_temperature_deviation` | `null` — future placeholder | Mean `|T_zone − T_setpoint|` over event window |
| `setpoint_tracking_error` | `null` — future placeholder | Same as above (control quality) |
| `post_action_temperature_delta` | `null` — future placeholder | `T(event_end) − T(event_start)` |

---

## F. What This Run Proves

✅ **Benchmark runner works**: `run_benchmark_smoke.py` executes without error for
both `rule_based_balanced` and `current` agent modes.

✅ **Metric extraction and saving work**: `metrics.json`, `unified_metrics.json`,
`summary.md` are all generated correctly.

✅ **CSV export works**: `inspect_metrics.py --dir logs --export` and
`export_metrics_table.py` both produce valid CSV files under `logs/metric_exports/`.

✅ **Unified metric schema enforced**: `unified_metrics.json` contains all 40
`REQUIRED_METRIC_FIELDS`; missing values are `null`, never omitted.

✅ **EnergyPlus co-simulation runs** (`--agent current`): EP exit code 0; agent
callback fires at sim_hour=42.0; all four EP variables populate in `home_state`.

❌ **Does NOT prove EnergyPlus physical closed-loop control**: `execution_status=
"executed"` means only that `set_actuator_value()` succeeded in Python. The
temperature response to the setpoint change in the EP thermal model has NOT been
verified via `.eso` time-series comparison.

❌ **Does NOT prove actual energy saving**: `estimated_reduction_kw` is a rule/LLM
estimate. No `.eso`-derived actual power measurements exist.

❌ **Does NOT validate Tianjin scenario**: All runs use Chicago EPW. The target
building (Tianjin climate) is not tested.

❌ **Does NOT prove LLM agent quality**: The EP run used the MPC rule path, not the
full LLM strategy path. LLM metrics are `null`.

---

## G. What Would Change With a Full EnergyPlus + LLM Run

Running:
```bash
python examples/run_benchmark_smoke.py \
  --scenario data/scenarios/us_chicago_vpp_smoke.json \
  --agent current
```

**What does change:**
- `execution_status` → `"executed"` (real EP actuator write via `set_actuator_value()`)
- `written_actuators` → `{"cooling_setpoint": 26.5, "heating_setpoint": 24.5}` (real EP values)
- `indoor_temp_at_event`, `outdoor_temp_at_event`, `hvac_power_kw_at_event`,
  `facility_power_kw_at_event` → **real EnergyPlus values** at event timestep
- If LLM path is triggered: `api_latency_seconds`, `total_tokens`, `llm_model` become non-null

**What still does NOT change:**
- Physical trajectory metrics remain `null` — still requires `eso_parser.py` (future)
- `hvac_setpoint_at_event` is still python-reflected, not EP-read
- `occupancy` is still hardcoded `True`
- `user_satisfaction_score` still `null` (no interactive session)

---

## H. Baseline vs. Current-Agent Comparison

| Metric | Baseline (mock) | EP run (current) |
|--------|----------------|-----------------|
| **Run type** | Mock, no EP | Full EP co-simulation |
| **execution_status** | `simulated` | `executed` |
| **setpoint** | 27.0°C | 26.5°C |
| **indoor_temp_at_event** | 24.5°C (mock) | **23.89°C (real EP)** |
| **outdoor_temp_at_event** | 22.0°C (mock) | **21.69°C (real EP)** |
| **hvac_power_kw_at_event** | 2.2 kW (mock) | **2.174 kW (real EP)** |
| **facility_power_kw_at_event** | 3.1 kW (mock) | **0.846 kW (real EP)** |
| **estimated_reduction_kw** | 0.5 kW | 0.4 kW |
| **estimated_vpp_compliance** | `true` (0.5 ≥ 0.5) | `false` (0.4 < 0.5) |
| **written_actuators** | `null` | `{"cooling_setpoint": 26.5, "heating_setpoint": 24.5}` |
| **api_latency_seconds** | `null` | `null` (MPC path used) |
| **actual_energy_kwh** | `null` | `null` (no .eso parser) |

**Key observation**: The EP run's `facility_power_kw_at_event=0.846 kW` is substantially
lower than the baseline mock `3.1 kW`. This is because the mock value is a fixed constant,
while the EP value reflects Chicago weather at hour 42 (night-time, low cooling load).
Neither value is a "truth" — the EP value is more physically meaningful, but it is still
a **snapshot** at the trigger moment, not a trajectory-level measurement.

---

## I. Metric Inspection CLI Output Summary

### `python examples/inspect_metrics.py --latest`

Reads the most recent `logs/trajectory_*.json`:

```
=== Benchmark Metrics ===
  action_type:        set_hvac_temperature
  setpoint:           26.8°C
  execution_status:   executed
  safety_ok:          True
  indoor_temp:        23.35°C
  outdoor_temp:       16.69°C
  hvac_power_kw:      0.0 kW
  est_reduction_kw:   0.0 kW
  metric_status:
    api_metrics:               available (latency=0.0s, not_used model)
    event_physical_snapshot:   available (real EP run)
    user_preference_metrics:   missing
    physical_trajectory_metrics: not_implemented
```

### `python examples/inspect_metrics.py --dir logs --export`

- Scanned: 16 `trajectory_*.json` files under `logs/`
- CSV exported to: `logs/metric_exports/metrics_20260512_201856.csv`

**First 5 rows (key columns):**

| run_id | action | setpoint | safety | est_red_kw | indoor_T | api_latency | tokens | llm_model | satisfaction |
|--------|--------|----------|--------|-----------|---------|-------------|--------|-----------|-------------|
| trajectory_20260502_161151 | set_hvac_temperature | 25.5 | True | 0.4 | 25.8 | 15.783 | 2707 | claude-sonnet-4-6 | 4 |
| trajectory_20260506_110718 | set_hvac_temperature | 26.5 | True | 0.4 | 25.8 | 11.088 | 1515 | claude-sonnet-4-6 | 3 |
| trajectory_20260512_120304 | set_hvac_temperature | 26.5 | True | 0.013 | 23.86 | 0.0 | 0 | not_used | null |
| trajectory_20260512_120611 | set_hvac_temperature | 26.5 | True | 0.013 | 23.86 | 0.0 | 0 | not_used | null |
| trajectory_20260512_120758 | set_hvac_temperature | 26.5 | True | 0.013 | 23.86 | 0.0 | 0 | not_used | null |

**Notable patterns:**
- Early runs (Apr 30, May 2, May 6): real LLM calls (latency 10–400s, tokens 1500–17000, user satisfaction 3–4)
- EP runs (May 12): `not_used` model, 0 tokens — agent used rule/MPC path, not LLM
- `outdoor_temp` is `null` in older non-EP trajectories (StateReader not active)
- `scenario_id` and `agent_id` are `null` in trajectory files (not stored in that format)

### `python examples/export_metrics_table.py`

- Scanned: `logs/trajectory_*.json` (12 files) + `logs/benchmark_runs/unified_metrics.json` (1 file)
- Exported 13 rows to `logs/metric_exports/metrics_20260512_201856.csv`

---

## J. Summary

| Item | Result |
|------|--------|
| Benchmark runner | ✅ Works |
| Metric extraction | ✅ Works |
| File outputs | ✅ metrics.json, unified_metrics.json, summary.md, CSV all generated |
| EnergyPlus snapshot data | ✅ Available in `--agent current` run |
| EnergyPlus physical closed-loop verified | ❌ Not verified — no .eso parser |
| LLM agent path tested | ❌ MPC path used; LLM not invoked |
| Tianjin scenario | ❌ Missing EPW |
| Actual energy metrics | ❌ All null — future placeholder |

**Bottom line**: This dry run demonstrates the benchmark/metric pipeline, not a
completed EnergyPlus physical benchmark. The infrastructure to collect, save, and
inspect metrics is in place and working.

---

*Last updated: 2026-05-12  |  Author: xudongwu*
