# Control Loop Map — EnergyBridge EP-Agent Integration

**Date:** 2026-05-12

---

## Conceptual Loop

```
EP simulation timestep fires
    → StateReader reads zone/HVAC variables from EnergyPlus
    → VPP event trigger check (cumulative-hour queue)
    → EnergyBridge agent graph invoked
        load_memory → parse_preference → translate_grid → generate_strategy
        → control/mock_mpc → safety → actuate → explanation → metrics → logging
    → ActuatorWriter writes setpoint back to EnergyPlus
    → EnergyPlus continues simulation
```

---

## Component Status Table

| Component | Status | File / Function | Notes |
|---|---|---|---|
| EP state extraction | ✅ Implemented | `simulation/state_reader.py` → `StateReader.read()` | Reads indoor_temp, outdoor_temp, hvac_power_kw, facility_power_kw |
| EP variable registration | ✅ Implemented | `StateReader.request_variables()` | Must call before `api.run()` |
| EP actuator write | ✅ Implemented | `simulation/actuator_writer.py` → `ActuatorWriter.apply()` | Writes cooling_sch / heating_sch; only `set_hvac_temperature` action |
| EP variable catalog | ✅ Implemented | `simulation/variable_catalog.py` | All EP strings centralized |
| EP environment wrapper | ✅ Implemented | `simulation/eplus_env.py` → `EplusEnv` | Timestep callback, VPP queue, thread lock, result collection |
| VPP event injection | ✅ Implemented | `EplusEnv.inject_vpp_event()` | Trigger by cumulative hour (after bug fix) |
| sim_hour computation | ✅ Fixed | `eplus_env.py` `_make_timestep_callback` | Was using `current_time()` only; fixed to use `day_of_year + current_time` |
| Agent graph | ✅ Implemented | `agent/graph.py` → `build_energybridge_graph()` | 10-node LangGraph sequential pipeline |
| Agent state schema | ✅ Implemented | `agent/state.py` → `EnergyBridgeState` | TypedDict with all fields |
| User preference parsing | ✅ Implemented | `skills/preference_parser.py` | Rule-based + LLM optional |
| Grid signal translation | ✅ Implemented | `skills/grid_signal_translator.py` | VPP-1 context → internal signal |
| Strategy generation | ✅ Implemented | `skills/strategy_generator.py` | LLM (3 options) or rule-based fallback |
| Mock MPC control | ✅ Implemented | `control/mock_mpc.py` → `run_mock_mpc()` | Strategy → concrete setpoint + kW estimates |
| Safety checker | ✅ Implemented | `control/safety_checker.py` → `validate_safety()` | Hard HVAC bounds 18–30°C + user prefs |
| Fallback controller | ✅ Implemented | `control/fallback_controller.py` | Safe default when safety fails |
| Mock actuator | ✅ Implemented | `control/mock_actuator.py` | Returns "executed" status (non-EP path) |
| Explanation generation | ✅ Implemented | `skills/explanation_generator.py` | Final response text |
| Metrics computation | ✅ Implemented | `evaluation/metrics.py` → `summarize_run()` | Energy, VPP compliance, safety, LLM latency |
| Trajectory logging | ✅ Implemented | `evaluation/logger.py` → `save_trajectory()` | Per-run JSON |
| Memory store | ✅ Implemented | `memory/store.py` | JSON episodic + session memory |
| Role-play user simulator | ✅ Implemented | `simulation/user.py` / `llm/roleplay_user.py` | LLM-simulated user with persona |
| Simulation coordinator | ✅ Implemented | `simulation/simulation.py` | Multi-turn roleplay |
| EP co-sim entry point | ✅ Implemented | `examples/run_eplus_agent_loop.py` | CLI to launch EP + agent |
| Tianjin EPW file | ❌ Missing | Expected: `Family_Model/Weather/Tianjin/CHN_Tianjin*.epw` | Not on this machine; US fallback available |
| Multi-scenario config | ❌ Missing | — | No YAML/JSON scenario registry |
| Multi-agent/baseline harness | ❌ Missing | — | Only one agent type |
| Comfort violation metric | ⚠️ Partial | `evaluation/metrics.py` | `safety_ok` flag only; no continuous temp-deviation metric |
| Real EP setpoint readback | ⚠️ Partial | `state_reader.py` | `hvac_setpoint` reflected from last write, not read from EP |
| Occupancy modeling | ⚠️ Partial | `state_reader.py` (hardcoded True) | No schedule-based occupancy |
| Multi-device control | ⚠️ Partial | `actuator_writer.py` | Only HVAC; no EV, hot water, lighting |
| Benchmark evaluation pipeline | ❌ Missing | — | No scenario×agent loop harness |
| Ground-truth / scoring oracle | ❌ Missing | — | No reference optimal policy |

---

## Data Flow Detail

### Non-EP Path (mock home state)
```
run_agent_loop.py
  HomeSimulator.get_state() → home_state (mock)
  GridSimulator.get_task()  → vpp_context (VPP-1)
  AgentSimulator.run()      → build_energybridge_graph().invoke()
                               [10 nodes] → final_response + control_plan + metrics
  HomeSimulator.update(control_plan)
  save trajectory + memory
```

### EP Path
```
run_eplus_agent_loop.py
  EplusEnv.inject_vpp_event(vpp_context, user_input, trigger_hour=42.0)
  EplusEnv.run()
    api.runtime.run_energyplus(...)
      _timestep_callback() fires every zone timestep
        StateReader.init_handles()     [once]
        ActuatorWriter.init_handles()  [once]
        sim_hour = (day_of_year - start_day)*24 + current_time
        if event due:
          home_state = StateReader.read()
          build_energybridge_graph().invoke(initial_state)
          ActuatorWriter.apply(control_plan)
          store AgentResult
  print AgentResult summary
```

---

## IDF ↔ Code Coupling

| IDF object | Python key | File |
|---|---|---|
| Zone: `living_unit1` | `VARIABLES["indoor_temp"]` | `variable_catalog.py` |
| Coil: `DX Cooling Coil_unit1` | `VARIABLES["cooling_rate_w"]` | `variable_catalog.py` |
| Schedule: `cooling_sch` | `ACTUATORS["cooling_setpoint"]` | `variable_catalog.py` |
| Schedule: `heating_sch` | `ACTUATORS["heating_setpoint"]` | `variable_catalog.py` |

If the IDF renames these objects, `variable_catalog.py` must be updated.
