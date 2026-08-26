#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--run" ]]; then
  echo "This launches 840 independent one-day simulations."
  echo "Re-run with --run after reading reproducibility/README.md."
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
output_root="${ENERGYBRIDGE_GENERATED_RESULTS_ROOT:-${repo_root}/generated_results}"
results_root="${output_root}/capacity_reporting"
workers="${ENERGYBRIDGE_WORKERS:-5}"
methods=(no_dr EnergyBridge hema_agent mpc_dynamic rule_milp rl_ppo_pref_v2)

cd "${repo_root}"
export ENERGYBRIDGE_HARNESS_PROFILE="${ENERGYBRIDGE_HARNESS_PROFILE:-adaptive_v2}"
if [[ "${ENERGYBRIDGE_HARNESS_PROFILE}" == "paper_v1" ]]; then
  export ENERGYBRIDGE_VPP_ACCEPTANCE_GATE=method_neutral_v1
  export ENERGYBRIDGE_ROLEPLAY_ACCEPTANCE_GATE_USE_LLM=0
  export ENERGYBRIDGE_PERSIST_AGENT_MEMORY=0
else
  export ENERGYBRIDGE_VPP_ACCEPTANCE_GATE=adaptive_roleplay_v2
  export ENERGYBRIDGE_ROLEPLAY_ACCEPTANCE_GATE_USE_LLM=1
  export ENERGYBRIDGE_PERSIST_AGENT_MEMORY=1
fi

python experiments/benchmark/run_daily_dr_memory_matrix.py \
  --methods "${methods[@]}" \
  --cities Germany Tianjin \
  --days 30 \
  --max-samples 7 \
  --start-date 2025-06-01 \
  --vpp-events-json \
    dr_capacity_memory_toolkit/june_2025_daily_eb/config/vpp_events_june_memory_merged30.json \
  --results-root "${results_root}" \
  --date june_7day \
  --workers "${workers}" \
  --resume \
  --fail-fast

python experiments/benchmark/run_daily_dr_memory_matrix.py \
  --methods "${methods[@]}" \
  --cities Germany Tianjin \
  --days 30 \
  --max-samples 7 \
  --start-date 2025-07-01 \
  --vpp-events-json \
    dr_capacity_memory_toolkit/july_2025_daily_eb/config/vpp_events_july_memory_merged30.json \
  --results-root "${results_root}" \
  --date july_7day \
  --workers "${workers}" \
  --resume \
  --fail-fast

python experiments/benchmark/run_capacity_consent_joined_replay.py \
  --daily-summary \
    "${results_root}/july_7day/_batch_logs/daily_dr_memory_summary_with_counterfactual.json" \
  --pool-daily-summary \
    "${results_root}/june_7day/_batch_logs/daily_dr_memory_summary_with_counterfactual.json" \
  --output-dir "${output_root}/capacity_reporting_analysis"

echo "Generated capacity runs: ${results_root}"
echo "Generated capacity analysis: ${output_root}/capacity_reporting_analysis"
