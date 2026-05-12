# Minimal Next Plan

**Date:** 2026-05-12  
**Principle:** Small concrete steps; no large rewrite.

---

## 1. Fix First to Make Simulator Run Reliably

### ✅ Already Fixed: `sim_hour` bug in `eplus_env.py`

`api.exchange.current_time()` returns hour-of-day (0–24), not cumulative hours.
Fixed by tracking `_sim_start_day` and computing:
```python
sim_hour = (day_of_year - start_day) * 24.0 + current_time
```
EP co-simulation now triggers VPP events correctly (verified 2026-05-12).

### Remaining: Tianjin EPW file

**Problem:** Tianjin EPW not on this machine (full system search confirmed).  
**Action:** Obtain `CHN_Tianjin.Tianjin.545270_CSWD.epw` from:
- EnergyPlus weather data portal: https://energyplus.net/weather
- CSWD (Chinese Standard Weather Data) dataset

Place at:
```
Family_Model/Weather/Tianjin/CHN_Tianjin.Tianjin.545270_CSWD.epw
```
No code change needed; `_find_epw()` in `run_eplus_agent_loop.py` already searches this path.

**Short-term workaround (already verified):**
```bash
python examples/run_eplus_agent_loop.py \
  --idf Family_Model/Family_Simple_3day.idf \
  --epw /home/ha_agent/EnergyPlus-24-1-0/WeatherData/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw \
  --trigger 42.0
```

---

## 2. Existing Interfaces to Reuse

| Interface | Decision |
|---|---|
| `agent/graph.py` + nodes | Keep as-is; works correctly |
| `EplusEnv` + `StateReader` + `ActuatorWriter` | Keep; EP bridge is complete |
| `EnergyBridgeState` TypedDict | Keep; add `actual_energy_kwh` when EP parser ready |
| `evaluation/metrics.py` | Keep; extend with EP-measured fields |
| `evaluation/logger.py` | Keep; trajectory format sufficient |
| `memory/store.py` | Keep; JSON memory fine for now |
| `simulation/simulation.py` | Use as inner loop in benchmark harness |

---

## 3. Minimal Benchmark Abstraction

**3 small steps, no changes to `energybridge/`:**

**Step 1** — Create `data/scenarios/scenario_01.json` (no code):
```json
{
  "id": "scenario_01",
  "name": "Invitation DR - 3day - Chicago EPW",
  "idf_path": "Family_Model/Family_Simple_3day.idf",
  "epw_path": "/home/ha_agent/EnergyPlus-24-1-0/WeatherData/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw",
  "vpp_context": {"vpp_task_type": "INVITATION_DEMAND_RESPONSE", ...},
  "trigger_hour": 42.0,
  "user_input": "我希望舒适但可以配合削峰",
  "expected_outcomes": {"safety_ok": true, "meets_vpp_requirement": true}
}
```

**Step 2** — Write `examples/run_benchmark.py` (~100 lines):
```python
for scenario_file in Path("data/scenarios").glob("*.json"):
    scenario = json.loads(scenario_file.read_text())
    # run EplusEnv or AgentSimulator
    # collect metrics from agent_results
    # append row to results.csv
```

**Step 3** — Write `evaluation/eso_parser.py` (~80 lines):
```python
def parse_electricity_demand(eso_path) -> list[dict]:
    # read Facility Total Electricity Demand Rate time series
    # return list of {timestamp, power_w, cumulative_kwh}
```

---

## 4. Easiest First Baseline

Write `energybridge/control/rule_based_controller.py` (~50 lines):

```python
def rule_based_control_plan(translated_grid_signal, home_state):
    intent = translated_grid_signal.get("control_intent", "normal_operation")
    urgency = translated_grid_signal.get("urgency", "low")
    current_sp = home_state.get("hvac_setpoint", 25.0)
    if intent == "reduce_load":
        delta = 2.0 if urgency == "high" else 1.0
        setpoint = min(28.0, current_sp + delta)
    else:
        setpoint = current_sp
    return {
        "action": "set_hvac_temperature",
        "setpoint": setpoint,
        "duration_minutes": translated_grid_signal.get("duration_minutes", 30),
        "estimated_power_kw": home_state.get("hvac_power_kw", 2.0) * 0.8,
        "estimated_reduction_kw": home_state.get("hvac_power_kw", 2.0) * 0.2,
        "controller": "rule_based_v0",
    }
```

Add `--agent rule_based` flag to `run_eplus_agent_loop.py`.

**MPC baseline:** `Family_Model/control_model/control_model.py` already implements MPC.
Integration requires: (a) wrap as agent-compatible function, (b) connect to EP via EplusEnv.
Estimated effort: 1–2 days.

---

## 5. First 3 Metrics (Minimal Extra Logging)

All three require only post-processing of existing EP `.eso` output:

| Metric | Source | Extra Code |
|---|---|---|
| **Actual total electricity (kWh)** | `eplusout.eso` → `Facility Total Electricity Demand Rate` | `eso_parser.py` (~80 lines) |
| **HVAC setpoint compliance rate** | `Zone Mean Air Temperature` vs `control_plan.setpoint` | post-process in `eso_parser.py` |
| **VPP requirement met (binary)** | `metrics.meets_vpp_requirement` in trajectory JSON | Already computed; just aggregate |

No changes to `energybridge/` needed.

---

## 6. Questions for Tiantian / Xuebing

| Question | Who | Why |
|---|---|---|
| Where is the Tianjin EPW file? | Tiantian / Xuebing | Needed for climatically valid EP runs |
| Are there plans for additional IDF scenarios (multi-zone, EV, hot water)? | Tiantian | Determines `variable_catalog.py` scope |
| Intended episode structure: single VPP event per run, or multi-event day? | Tiantian | Affects benchmark harness design |
| Should benchmark compare multiple LLM backends? | Tiantian | Determines if agent config needs parameterization |
| Is `Family_Model/control_model/control_model.py` intended as a baseline? | Xuebing | Medium effort to integrate; high value |
| Purpose of `Family_Model/original_model.idf`? | Tiantian / Xuebing | Role unclear; may be pre-retrofit baseline |

---

## Recommended Action Order

1. ✅ **Fix sim_hour bug** — Done (2026-05-12)
2. ✅ **Verify EP-Agent loop with 3day IDF + US EPW** — Done (2026-05-12)
3. **Obtain Tianjin EPW** — ask Tiantian/Xuebing or download from energyplus.net
4. **Create `data/scenarios/` JSON files** — no code change, 1–2 scenarios to start
5. **Write `evaluation/eso_parser.py`** — ~80 lines, actual energy from EP output
6. **Write `control/rule_based_controller.py`** — ~50 lines, first baseline
7. **Write `examples/run_benchmark.py`** — ~100 lines, minimal harness
8. **Clarify scope** with Tiantian/Xuebing (MPC baseline, IDF scenarios, episode design)
