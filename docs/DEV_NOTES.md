# Development Notes

## Legal and Reference Boundary

- `references/HEMA` is GPLv3 and used only for high-level reference.
- EnergyBridge implementation in this repository is independently written.
- Do not copy source code from `references/HEMA`.

## Stage-1 Scope (Current)

- Build a minimal runnable local agent loop.
- Use deterministic Python functions for business logic.
- Use LangGraph only as orchestration.
- Keep LLM calls optional and isolated in `energybridge/llm`.
- No frontend, no FastAPI, no real device connection.

## Recent Updates

### EnergyPlus 24.1.0 Local Setup

- Installed EnergyPlus 24.1.0 to `/home/ha_agent/EnergyPlus-24-1-0`.
- Updated `EPLUS_ROOT` to `Path("/home/ha_agent/EnergyPlus-24-1-0")`.
- Verified `pyenergyplus.api.EnergyPlusAPI` imports successfully in the `energybridge` conda environment.
- Verified `control_model.py` passes with `python control_model.py --help`.

### Session Memory Layer

- Added `session_summary` as a short-term memory layer.
- Reshaped it into `current_round_summary` plus a rolling window of the previous 3 rounds.
- Kept `stable_preferences` as long-term statistics only.
- Wired `session_summary` into preference merging, strategy generation, and explanation generation.
- `episodic_logs` still stores the full turn-by-turn episode history for replay and debugging.

### VPP Flow Cleanup

- Renamed the runtime VPP boundary from `grid_signal` to `grid_demand`.
- Moved VPP provenance fields into a separate `vpp_context` object.
- Updated metrics to read VPP IDs and basis fields from `vpp_context`.
- Simplified the VPP-1 flow so the adapter extracts `vpp_context`, the translator builds `translated_grid_signal`, and the example entrypoint only prints the translated signal.
- `python examples/run_agent_loop.py` has been tested.

### Feedback Flow Cleanup

- Moved user feedback collection to after the main graph run in `examples/run_agent_loop.py`.
- Added a dedicated `feedback` LangGraph node and `build_feedback_graph()` to persist only `user_feedback`.
- Removed the old `node_memory_update` node and stopped storing full `feedback_episode` snapshots in memory.
- Kept the demo flow simple: main run, then feedback update, then trajectory and memory logging.
- `python -m compileall energybridge examples/run_agent_loop.py` has been tested.

##  Stage-2 EnergyPlus Integration
### Overview

Added `energybridge/simulation/` as a adapter layer connecting the EnergyBridge agent loop to a real EnergyPlus building simulation via `pyenergyplus`.  The agent graph and all skills are completely unchanged; only the data source (home_state) and the actuation target (EnergyPlus actuators) are replaced.

### EnergyPlus Installation
Installed EnergyPlus 24.1.0 to `/home/ha_agent/EnergyPlus-24-1-0`

```bash
wget https://github.com/NREL/EnergyPlus/releases/download/v24.1.0/EnergyPlus-24.1.0-9d7789a3ac-Linux-Ubuntu22.04-x86_64.sh
chmod +x EnergyPlus-24.1.0-9d7789a3ac-Linux-Ubuntu22.04-x86_64.sh
./EnergyPlus-24.1.0-9d7789a3ac-Linux-Ubuntu22.04-x86_64.sh
```

### Introduction
## IDF 文件

`Family_Model/Family_Simple.idf`

IDF（Input Data File）是 EnergyPlus 的建筑模型输入文件，描述建筑的全部物理信息。这个文件是在 EnergyPlus 24.1 官方示例 `SingleFamilyHouse_HP_Slab.idf`（即 `original_model.idf`基础上改造而来的，主要改动记录在 `envelope_retrofit_report.md`第 8-10 节：

- **地点**：从美国费尔班克斯改为天津（纬度 39.08°N，经度 117.07°E）
- **围护结构**：外墙换成 `CN_ExteriorWall_AAC_RockWool_Cold`（加气混凝土 + 岩棉外保温），顶棚换成 `CN_AtticFloor_RockWool_Cold`，外窗换成 Low-E 中空玻璃
- **地面边界**：去掉了 Slab 预处理器依赖，改为 `Ground` + 月地温，可以直接运行
- **可控设备**：新增 EV 充电器（`EV_Charger`，7 kW）和储水式电热水器（`WaterHeater:Stratified`，120 L / 3 kW），各自挂了 `Schedule:Constant` 作为外部控制接口
- **Agent 控制接口**：IDF 中保留了 `cooling_sch` 和 `heating_sch` 两个 `Schedule:Compact`，这正是 `variable_catalog.py` 里 `ACTUATORS` 字典指向的 actuator 目标

另外还有一个 `Family_Simple_3day.idf`，RunPeriod 只跑 7 月 1-3 日，用于快速调试。

---

## EPW 文件

由 `_find_epw()` 按优先级搜索：

```
Family_Model/Weather/Tianjin/CHN_Tianjin.Tianjin.545270_CSWD.epw
```

EPW（EnergyPlus Weather）是逐小时气象数据文件。这个文件是天津的 CSWD（中国标准气象数据）格式气象文件，WMO 站号 545270，与 IDF 里 `Site:Location` 的坐标完全对应（39.08°N / 117.07°E / UTC+8 / 海拔 2.5 m）。

`_find_epw()` 还列了几个备用路径，最后兜底是 EnergyPlus 自带的芝加哥或旧金山 EPW，保证脚本在没有天津气象文件时也能跑通功能测试。

---

### Architecture: Event-Driven Loose Coupling

```
VPP signal injected into event queue
  → EnergyPlus timestep callback fires (every 10 min)
  → StateReader reads Zone Temp / Outdoor Temp / HVAC Power from EnergyPlus
  → EnergyBridge agent graph invoked (full loop: preference → strategy → MPC → safety → explanation)
  → ActuatorWriter writes cooling_sch / heating_sch setpoints back to EnergyPlus
  → EnergyPlus continues with new setpoints until next event
```

### New Files

| File | Purpose |
|---|---|
| `energybridge/simulation/__init__.py` | Package docstring |
| `energybridge/simulation/variable_catalog.py` | Centralised EnergyPlus variable/actuator name registry |
| `energybridge/simulation/state_reader.py` | EnergyPlus output variables → `home_state` dict |
| `energybridge/simulation/actuator_writer.py` | `control_plan` → EnergyPlus Schedule actuator writes |
| `energybridge/simulation/eplus_env.py` | Main env class: lifecycle, event queue, callback, agent invocation |
| `examples/run_eplus_agent_loop.py` | Demo entry point for EnergyPlus co-simulation mode |
| `Family_Model/Family_Simple_3day.idf` | 3-day test IDF (July 1–3) for fast functional testing |

### EnergyPlus Variable / Actuator Mapping

**State reads (variable_catalog.py):**

| home_state field | EnergyPlus variable | Key |
|---|---|---|
| `indoor_temp` | `Zone Mean Air Temperature` | `living_unit1` |
| `outdoor_temp` | `Zone Outdoor Air Drybulb Temperature` | `living_unit1` |
| `hvac_power_kw` | `Cooling Coil Total Cooling Rate` | `DX Cooling Coil_unit1` |
| `facility_power_kw` | `Facility Total Electricity Demand Rate` | `Whole Building` |

**Actuator writes (variable_catalog.py):**

| control_plan action | EnergyPlus actuator | Key |
|---|---|---|
| `set_hvac_temperature` → cooling | `Schedule:Compact / Schedule Value` | `cooling_sch` |
| `set_hvac_temperature` → heating | `Schedule:Compact / Schedule Value` | `heating_sch` |

Heating setpoint is automatically set to `cooling_setpoint - 2.0°C` to avoid
thermostat conflicts.

### Verified Test Run

```bash
python examples/run_eplus_agent_loop.py \
  --idf Family_Model/Family_Simple_3day.idf \
  --epw /home/ha_agent/EnergyPlus-24-1-0/WeatherData/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw \
  --output logs/eplus_test_run4 \
  --trigger 18.0
```

Result:
- EnergyPlus Completed Successfully (exit code 0)
- VPP event fired at sim_hour=18.00
- home_state read: indoor=23.86°C, outdoor=16.09°C, hvac=0.613 kW
- Agent decision: setpoint=26.5°C (cost_saving mode, safety passed)
- Actuator written: cooling_setpoint=26.5°C, heating_setpoint=24.5°C
- No WARNING messages; all variable/actuator handles resolved

### Next Steps

- Stage 2b: Merge EV/EWH control from `control_model.py` into `EplusEnv`
- Stage 3: Replace `mock_mpc` with a physics-based thermal prediction model
  now that real `home_state` is available
- Stage 4: Split `living_unit1` into multiple thermal zones with independent
  temperature control
- Add Tianjin EPW to `Family_Model/Weather/Tianjin/` for China-specific runs

## Current TODO

- Add unit tests for skills and safety checker edge cases.
- Expand VPP-1 adapter with stricter validation.
- Add configurable policy profiles for different households.
- Add regression tests for memory update behavior.
- Add Tianjin EPW file to enable China-specific EnergyPlus runs.
- Merge EV/EWH control from control_model.py into EplusEnv (Stage 2b).
- Replace mock_mpc with physics-based thermal model (Stage 3).
