# Run Log — EnergyBridge Pipeline

**Date:** 2026-05-12

---

## Attempt 1: Non-EP Interactive Demo (`run_agent_loop.py`)

### Command
```bash
printf "1\n我希望舒适但可以配合削峰\n1\n5\n满意\n" | python examples/run_agent_loop.py
```

### Environment
- Python: `/home/ha_agent/miniconda3/bin/python`
- `USE_LLM=true`，LLM 模型: `claude-sonnet-4-6`
- pyenergyplus: 不需要

### Result: SUCCESS (exit code 0)

```
=== Strategy Options ===
1. Comfort-First Mild Grid Support  (setpoint=25.5C, source=llm)
2. Balanced Grid Cooperation        (setpoint=26.0C, source=llm)
3. Minimal Intervention Hold        (setpoint=25.0C, source=llm)

=== Final Response ===
Mode: grid_support. HVAC setpoint → 25.5C for 30min. Safety checks passed.

=== Metrics ===
api_latency=14.9s, total_tokens=1370, meets_vpp_requirement=true,
safety_ok=true, llm_model=claude-sonnet-4-6

=== Trajectory Steps (10) ===
load_memory → parse_preference → translate_grid → generate_strategy →
control → safety → actuate → explanation → metrics → logging

Trajectory saved: logs/trajectory_20260512_165432.json
Memory updated: logs/memory.json
```

---

## Attempt 2: EP Co-simulation (First Attempt — Agent Not Triggered)

### Command
```bash
python examples/run_eplus_agent_loop.py \
  --idf Family_Model/Family_Simple_3day.idf \
  --epw /home/ha_agent/EnergyPlus-24-1-0/WeatherData/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw \
  --output logs/eplus_test_us_epw --trigger 42.0
```

### Result: EP ran successfully but agent NOT triggered

```
EnergyPlus Completed Successfully.
WARNING: No agent results recorded. The VPP event may not have fired.
  Check that trigger_hour=42.0 falls within the simulation period.
```

### Root Cause: Bug in `sim_hour` computation

`api.exchange.current_time()` returns hour-of-day (0–24), NOT cumulative simulation hours.
So `sim_hour` was always in [0, 24], and `trigger_hour=42` was never reached.

---

## Bug Fix: `eplus_env.py` sim_hour Computation

**File changed:** `energybridge/simulation/eplus_env.py`

**Added to `__init__`:**
```python
self._sim_start_day: int | None = None
```

**Replaced in `_make_timestep_callback`:**
```python
# Before (wrong):
sim_hour = api.exchange.current_time(s)

# After (correct):
day = api.exchange.day_of_year(s)
if self._sim_start_day is None:
    self._sim_start_day = day
sim_hour = (day - self._sim_start_day) * 24.0 + api.exchange.current_time(s)
```

---

## Attempt 3: EP Co-simulation After Bug Fix (SUCCESS)

### Result: Full EP-Agent loop verified end-to-end

```
[EplusEnv] VPP event processed at sim_hour=42.00
  home_state : indoor=23.89°C  outdoor=21.69°C  hvac=2.21 kW
  control    : setpoint=26.5°C  action=set_hvac_temperature
  execution  : executed  written={'cooling_setpoint': 26.5, 'heating_setpoint': 24.5}

Control plan: action=set_hvac_temperature, setpoint=26.5, duration=60min
Safety report: safe=true, violations=[]
Execution result: status=executed, actuator=eplus_actuator_v1

EnergyPlus finished with exit code: 0
```

### Verified Components
1. EnergyPlus 3-day simulation ran with US EPW (Chicago)
2. VPP event fired at simulation hour 42 (day 2, 18:00)
3. `StateReader` read real building state (indoor 23.89°C, HVAC 2.21 kW)
4. EnergyBridge 10-node agent graph ran and produced control plan
5. `ActuatorWriter` wrote setpoints back to EnergyPlus schedule actuators

**Note:** Chicago EPW used for testing; Tianjin EPW needed for valid results.
**Note:** Tianjin EPW not found anywhere on this machine (full system search confirmed).

---

## Summary

| Pipeline | Status | Notes |
|---|---|---|
| Non-EP demo (`run_agent_loop.py`) | ✅ Success | Full LangGraph, LLM used, mock home |
| EP co-simulation (`run_eplus_agent_loop.py`) | ✅ Success (after fix) | Bug fixed in `eplus_env.py` |
| Roleplay evaluation | ✅ Previously run | Logs in `logs/evaluations/persona_4827_*/` |
| Batch roleplay | ✅ Previously run | Trajectories in `logs/trajectory_*.json` |
