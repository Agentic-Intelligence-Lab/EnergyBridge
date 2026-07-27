#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--run" ]]; then
  echo "This launches 50 seven-day simulations."
  echo "Re-run with --run after reading reproducibility/README.md."
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
output_root="${ENERGYBRIDGE_GENERATED_RESULTS_ROOT:-${repo_root}/generated_results}"
workers="${ENERGYBRIDGE_WORKERS:-5}"
methods=(EnergyBridge hema_agent mpc_dynamic rule_milp rl_ppo_pref_v2)

cd "${repo_root}"
export ENERGYBRIDGE_VPP_ACCEPTANCE_GATE=method_neutral_v1
export ENERGYBRIDGE_ROLEPLAY_ACCEPTANCE_GATE_USE_LLM=0
export ENERGYBRIDGE_PERSIST_AGENT_MEMORY=0

python experiments/benchmark/run_household_matrix.py \
  --methods "${methods[@]}" \
  --city Tianjin \
  --days 7 \
  --start-date 2025-06-01 \
  --price-csv experiments/real_data/tianjin_tou_price_normalized.csv \
  --vpp-start-hour 18 \
  --vpp-duration-hours 1 \
  --mpc-horizon 6 \
  --results-root "${output_root}/main_benchmark" \
  --date tianjin_7day \
  --workers "${workers}" \
  --resume \
  --fail-fast

python experiments/benchmark/run_household_matrix.py \
  --methods "${methods[@]}" \
  --city Germany \
  --days 7 \
  --start-date 2025-06-01 \
  --price-csv experiments/real_data/germany_2025_price.csv \
  --vpp-start-hour 18 \
  --vpp-duration-hours 1 \
  --mpc-horizon 6 \
  --results-root "${output_root}/main_benchmark" \
  --date germany_7day \
  --workers "${workers}" \
  --resume \
  --fail-fast

for region in tianjin germany; do
  summary="${output_root}/main_benchmark/${region}_7day/_batch_logs/household_matrix_summary_${region}_7days_H6.json"
  python experiments/benchmark/generate_baseline_matrix_report.py \
    --summary-json "${summary}" \
    --output-dir "${output_root}/main_benchmark_reports/${region}" \
    --artifact-prefix "${region}_household_5method" \
    --row-label Household \
    --energy-panel cost
done

echo "Generated main benchmark: ${output_root}/main_benchmark"
echo "Generated reports: ${output_root}/main_benchmark_reports"
