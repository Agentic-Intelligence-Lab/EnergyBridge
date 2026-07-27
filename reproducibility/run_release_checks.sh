#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
python_bin="${PYTHON_BIN:-python}"

cd "${repo_root}"

# Keep release checks deterministic and independent of external services.
export USE_LLM=false
export ROLEPLAY_USE_LLM=false

"${python_bin}" human_roleplay_data/scripts/validate_release.py

PYTHONPATH="${repo_root}${PYTHONPATH:+:${PYTHONPATH}}" \
  "${python_bin}" -m pytest -q \
    tests/test_event_baseline.py \
    tests/test_capacity_consent_report.py \
    experiments/benchmark/tests/test_agent_capacity_reporter.py \
    experiments/benchmark/tests/test_counterfactual_baseline.py \
    experiments/benchmark/tests/test_dr_event_memory.py

# Run the standalone PPO package in a fresh process to keep imports isolated.
PYTHONPATH="${repo_root}${PYTHONPATH:+:${PYTHONPATH}}" \
  "${python_bin}" -m pytest -q tests/test_rl_energyplus.py

echo "Release validation and smoke tests passed."
