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

## Current TODO

- Add unit tests for skills and safety checker edge cases.
- Expand VPP-1 adapter with stricter validation.
- Add configurable policy profiles for different households.
- Add regression tests for memory update behavior.
- Prepare interfaces for real MPC integration.
