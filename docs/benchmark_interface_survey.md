# Benchmark Interface Survey

**Date:** 2026-05-12

---

## 1. How to Run One EnergyPlus-Agent Episode

```bash
cd ~/work/EnergyBridge
python examples/run_eplus_agent_loop.py \
  --idf  Family_Model/Family_Simple_3day.idf \
  --epw  /home/ha_agent/EnergyPlus-24-1-0/WeatherData/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw \
  --output logs/eplus_run --trigger 42.0
```

Or via the benchmark smoke-test runner:

```bash
python examples/run_benchmark_smoke.py \
  --scenario data/scenarios/us_chicago_vpp_smoke.json \
  --agent current          # full LLM agent
  # --agent rule_based_balanced  # deterministic baseline, no EP needed
```

---

## 2. Required Inputs

| Input | CLI flag | Type | Notes |
|---|---|---|---|
| IDF path | `--idf` | file | `Family_Simple_3day.idf` tested |
| EPW path | `--epw` | file | Tianjin missing; Chicago as smoke fallback |
| Output dir | `--output` | dir | EP writes `.eso`, `.err` here |
| Trigger hour | `--trigger` | float (cumulative sim hours) | 42.0 = day 2, 18:00 |
| User preference | `--user` | string | Passed to agent `user_input` |
| VPP context | hardcoded in script | dict | Synthetic invitation DR; not yet CLI-configurable |

---

## 3. Available Outputs

### AgentResult fields (`energybridge/simulation/eplus_env.py`)

| Field | Content |
|---|---|
| `sim_hour` | Cumulative sim hour when agent fired |
| `home_state` | `indoor_temp`, `outdoor_temp`, `hvac_power_kw`, `facility_power_kw`, `hvac_setpoint` |
| `control_plan` | `action`, `setpoint`, `duration_minutes`, `estimated_reduction_kw` |
| `safety_report` | `safe` (bool), `violations` |
| `execution_result` | `status` (`"executed"` / error), `written` (actuator values) |
| `final_response` | Natural-language explanation |
| `trajectory` | LangGraph node names (10 steps) |

### EnergyPlus output directory

| File | Content | Status |
|---|---|---|
| `eplusout.eso` | Full variable time series | Present, **not yet parsed** |
| `eplusout.err` | Warnings and errors | Present |
| `eplusout.csv` | Time series in CSV (if requested) | Not enabled by default |

---

## 4. Outputs Reliable Enough for Benchmark Metrics Now

- `agent_triggered`, `valid_control_plan`, `action_type`, `setpoint` ✅
- `execution_status`, `safety_ok` ✅
- `indoor_temp_at_event`, `outdoor_temp_at_event` ✅
- `hvac_power_kw_at_event`, `facility_power_kw_at_event` ✅
- `estimated_reduction_kw` ✅ (agent estimate — proxy only)

---

## 5. Uncertain / Unverified Outputs

| Output | Uncertainty |
|---|---|
| EP physical response | `execution_result.status="executed"` means Python write succeeded; does NOT confirm EP adjusted temperatures. Needs `.eso` time-series comparison. |
| Actual energy saving | `estimated_reduction_kw` is agent estimate, not measured. Requires `eso_parser.py`. |
| VPP compliance | `estimated_vpp_compliance` is estimate ≥ threshold — not measured. |
| Comfort violation hours | Requires `.eso` temperature time series. |

---

## 6. What Still Depends on RA's Simulator Work

| Item | Owner |
|---|---|
| `energybridge/simulation/` correctness | Xuebing (RA) |
| EP physical closed-loop validation | Xuebing |
| Multi-device actuators | Xuebing + Tiantian |
| `Family_Simple.idf` correctness | Tiantian |
| Additional IDF scenarios | Tiantian |
| Tianjin EPW file | Tiantian / team |
