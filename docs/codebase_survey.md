# EnergyBridge Codebase Survey

**Date:** 2026-05-12  
**Purpose:** Help Xudong understand the current repository layout before running or modifying anything.

---

## 1. Repository Root (`/home/ha_agent/work/EnergyBridge/`)

```
EnergyBridge/
├── README.md                    # Quick start + architecture overview
├── requirements.txt             # Pinned Python dependencies
├── .env / .env.example          # LLM API config
├── energybridge/                # Main Python package
├── examples/                    # Runnable entry-point scripts
├── Family_Model/                # EnergyPlus IDF models + control reference
├── VPP-1/                       # Standalone VPP grid task library
├── docs/                        # Design guides and notes
├── logs/                        # Runtime outputs (trajectories, memory, EP)
└── scripts/                     # Shell helper
```

---

## 2. Main Code Directories

### `energybridge/` — Core Package

| Sub-package | Role |
|---|---|
| `agent/` | LangGraph agent graph, node implementations, state schema |
| `simulation/` | EP environment wrapper, state reader, actuator writer, variable catalog |
| `skills/` | Strategy generator, preference parser, grid signal translator, explanation |
| `control/` | Mock MPC, mock actuator, fallback controller, safety checker |
| `evaluation/` | Metrics, trajectory logger, roleplay evaluator |
| `llm/` | LLM client wrapper, prompts, strategy advisor, roleplay user |
| `memory/` | JSON-based memory store |
| `grid/vpp_1/` | VPP-1 adapter, mock signal generator, schemas |
| `utils/` | Config loader (reads `.env`) |

### `examples/` — Entry Points

| Script | Purpose |
|---|---|
| `run_agent_loop.py` | **Main interactive demo**: VPP task → agent loop → mock actuation |
| `run_eplus_agent_loop.py` | **EP co-simulation demo**: connects agent loop to real EnergyPlus |
| `run_llm_test.py` | Quick LLM API connectivity test |
| `run_roleplay_evaluation.py` | Single-user role-play preference learning evaluation |
| `run_batch_roleplay_evaluation.py` | Batch role-play over N simulated users |

---

## 3. Simulator-Related Files

| File | Description |
|---|---|
| `energybridge/simulation/eplus_env.py` | Main EP wrapper (`EplusEnv`). Runs EP, fires agent at VPP trigger hour. |
| `energybridge/simulation/state_reader.py` | `StateReader`: reads zone temp / HVAC power from EP |
| `energybridge/simulation/actuator_writer.py` | `ActuatorWriter`: writes HVAC setpoints back to EP |
| `energybridge/simulation/variable_catalog.py` | Central registry of EP variable names and actuator keys |
| `energybridge/simulation/simulation.py` | `Simulation` coordinator (used by roleplay eval) |
| `energybridge/simulation/home.py` | `HomeSimulator`: mock home state |
| `energybridge/simulation/agent.py` | `AgentSimulator`: wraps the LangGraph graph |
| `energybridge/simulation/grid.py` | `GridSimulator`: VPP-1-backed grid task generator |
| `energybridge/simulation/user.py` | `SimulatedUser`: role-play user persona |

---

## 4. EP Interface Files

| File | Key Detail |
|---|---|
| `eplus_env.py` | Uses `pyenergyplus.api.EnergyPlusAPI`; registers timestep callback |
| `state_reader.py` | Reads: `Zone Mean Air Temperature`, `Zone Outdoor Air Drybulb Temperature`, `Cooling Coil Total Cooling Rate`, `Facility Total Electricity Demand Rate` |
| `actuator_writer.py` | Writes: `Schedule:Compact` → `cooling_sch` / `heating_sch` |
| `variable_catalog.py` | All EP variable/actuator names centralized here |
| `Family_Model/Family_Simple.idf` | Main IDF (Tianjin residential, 1 zone `living_unit1`) |
| `Family_Model/Family_Simple_3day.idf` | 3-day short simulation variant (faster testing) |
| `Family_Model/control_model/control_model.py` | Reference MPC controller (not yet integrated into main loop) |

---

## 5. Agent-Related Files

| File | Description |
|---|---|
| `agent/graph.py` | Builds LangGraph `StateGraph`: 10-node sequential pipeline |
| `agent/nodes.py` | All node implementations (load_memory → ... → logging) |
| `agent/state.py` | `EnergyBridgeState` TypedDict |
| `skills/strategy_generator.py` | LLM-backed or rule-based strategy generation |
| `skills/preference_parser.py` | Parses user text into structured preference dict |
| `skills/grid_signal_translator.py` | Converts VPP-1 context → internal grid signal |
| `control/mock_mpc.py` | Translates candidate strategy → concrete control_plan |
| `control/safety_checker.py` | Validates control_plan against hard bounds + user prefs |
| `control/mock_actuator.py` | Simulates execution (non-EP path) |
| `control/fallback_controller.py` | Safe default plan if safety check fails |

---

## 6. Scenario / Config Files

| File | Description |
|---|---|
| `.env` / `.env.example` | LLM API settings |
| `Family_Model/Family_Simple.idf` | Building scenario (single zone, Tianjin) |
| `Family_Model/Family_Simple_3day.idf` | Short 3-day variant |
| `energybridge/simulation/variable_catalog.py` | EP variable/actuator names per scenario |

No dedicated scenario config files exist yet. IDF/EPW paths are CLI arguments only.

---

## 7. Logging / Output Files

| Path | Content |
|---|---|
| `logs/memory.json` | Persistent agent memory (episodic logs, session summary, learned prefs) |
| `logs/trajectory_*.json` | Per-run full state trajectory (all node I/O) |
| `logs/eplus_test_run*/` | EnergyPlus output files from previous EP runs |
| `logs/evaluations/persona_*/` | Per-user roleplay evaluation outputs |

---

## 8. Dependencies

- **LangGraph / LangChain**: agent graph execution
- **openai**: LLM API client
- **python-dotenv**: `.env` loading
- **pydantic**: state schema validation
- **pyenergyplus**: EP Python API at `/home/ha_agent/EnergyPlus-24-1-0/` (path injection required; not pip-installed)

---

## 9. Key Observations

1. The `energybridge/` package is well-structured and self-contained.
2. The non-EP pipeline runs without external services when `USE_LLM=false`.
3. EP co-simulation requires: EnergyPlus 24.1.0, a valid EPW, `pyenergyplus` path injection.
4. Tianjin EPW **not found** on this machine; US EPW files are bundled with EnergyPlus.
5. No benchmark harness; evaluation covers only roleplay/preference-learning.
6. **Bug found and fixed**: `sim_hour` was computed from `current_time()` (hour-of-day 0–24) instead of cumulative hours. Fixed in `eplus_env.py` using `day_of_year + current_time`.
