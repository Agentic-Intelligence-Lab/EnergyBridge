# EnergyBridge

EnergyBridge is a home-grid coordination agent framework that connects user preferences, grid/VPP signals, home state, control decisions, safety checks, execution, and memory logging.

Current status: stage-1 interactive agent loop is implemented with LangGraph. It runs locally without an API key by default, and can optionally use an OpenAI-compatible API from a local .env file to generate strategy options.

## Quick Start

1. Activate environment:

```bash
cd ~/work/EnergyBridge
source ~/miniconda3/etc/profile.d/conda.sh
conda activate energybridge
```

2. Run interactive demo:

```bash
python examples/run_agent_loop.py
```

3. Optional LLM wrapper test (requires `.env` and `USE_LLM=true`):

```bash
python examples/run_llm_test.py
```

The demo will:

- load a grid signal from mock VPP-1 or custom input;
- ask for user preference text;
- optionally call the configured API to generate strategy options;
- let the user choose a strategy;
- run safety checks and mock actuation;
- update memory and save a trajectory log.

## Minimal Loop

`user_input + grid_signal + home_state`
`-> load memory`
`-> parse preference`
`-> translate grid signal`
`-> generate strategy`
`-> user selects strategy`
`-> run mock control`
`-> safety validation`
`-> mock actuation`
`-> explanation`
`-> memory update`
`-> trajectory logging`

## Reference Note

`references/HEMA` is kept only as architecture inspiration because it is GPLv3. EnergyBridge code is implemented independently and does not copy HEMA source code.
