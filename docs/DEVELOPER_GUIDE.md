# EnergyBridge Developer Guide

## 1. Environment Activation

```bash
cd ~/work/EnergyBridge
source ~/miniconda3/etc/profile.d/conda.sh
conda activate energybridge
```

## 2. Dependencies

Install from requirements if needed:

```bash
pip install -r requirements.txt
```

## 3. Run Interactive Demo

```bash
python examples/run_agent_loop.py
```

Expected behavior:
- prompts for grid signal source, user preference, and strategy choice;
- when `USE_LLM=true`, calls the configured OpenAI-compatible API for strategy options;
- prints final response, control plan, safety report, execution result, and trajectory steps;
- creates `logs/memory.json`;
- creates `logs/trajectory_YYYYMMDD_HHMMSS.json`.

## 4. Optional LLM Wrapper Test

1. Copy `.env.example` to `.env` and set values.
2. Set `USE_LLM=true`.
3. Run:

```bash
python examples/run_llm_test.py
```

This test is optional and not required for the default demo.

The local API settings are read from `.env` using these fields:
- `USE_LLM`
- `LLM_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`

## 5. Current Module Layout

- `energybridge/agent`: state schema, nodes, graph orchestration
- `energybridge/skills`: deterministic business logic
- `energybridge/control`: mock MPC, safety checker, fallback controller, mock actuator
- `energybridge/grid/vpp_1`: VPP-1 boundary schema and adapter
- `energybridge/memory`: JSON memory load/save/update
- `energybridge/evaluation`: trajectory logger and minimal metrics
- `energybridge/llm`: provider-agnostic LLM access layer
- `examples`: runnable scripts

## 6. Add a New Skill

1. Create a deterministic function in `energybridge/skills/your_skill.py`.
2. Add a node function in `energybridge/agent/nodes.py` to call it.
3. Insert the node in `energybridge/agent/graph.py`.
4. Extend `EnergyBridgeState` in `energybridge/agent/state.py` if new fields are needed.
5. Update docs and add tests as needed.