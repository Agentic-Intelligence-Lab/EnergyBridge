#!/usr/bin/env bash
set -euo pipefail

cd /home/hku_user/work/EnergyBridge

echo "[1/6] Verify recalled manual-override fallback code"
PYTHONPATH=. pytest -q experiments/benchmark/tests/test_family_runner_skip_confirmation.py
python -m py_compile \
  experiments/benchmark/family_runner.py \
  experiments/benchmark/analyze_eb_capacity_loop.py \
  experiments/benchmark/create_controlled_adapt_personas.py \
  experiments/benchmark/analyze_persona_adaptability.py

echo "[2/6] Re-generate recalled household main report from previous 7-day summary"
python experiments/benchmark/generate_baseline_matrix_report.py \
  --summary-json benchmark_results/2026-07-16_mainfig_household_manual_override_v1/_batch_logs/household_matrix_summary_tianjin_7days_H6.json \
  --output-dir benchmark_results/2026-07-17_recalled_manual_override_results_v1/mainfig_household_5x1_tianjin_manual_override_report \
  --artifact-prefix household_5x1_tianjin_manual_override_recalled \
  --row-label Household \
  --energy-panel cost

echo "[3/6] Run EB-only capacity diagnostic on recalled household summary"
python experiments/benchmark/analyze_eb_capacity_loop.py \
  --summary-json benchmark_results/2026-07-16_mainfig_household_manual_override_v1/_batch_logs/household_matrix_summary_tianjin_7days_H6.json \
  --output-dir benchmark_results/2026-07-17_recalled_manual_override_results_v1/capacity_loop_eb_only

echo "[4/6] Generate controlled adaptability personas"
python experiments/benchmark/create_controlled_adapt_personas.py

echo "[5/6] Run controlled persona matrix with manual-override fallback"
python experiments/benchmark/run_baseline_matrix.py \
  --personas paper_adapt_a_price_cooperative paper_adapt_b_comfort_gated paper_adapt_c_irregular_cautious paper_adapt_d_ideal_dr paper_adapt_e_caregiver_low_dr \
  --methods EnergyBridge mpc_dynamic rule_milp rl_ppo_pref_v2 \
  --city Tianjin --days 1 \
  --date 2026-07-17_recalled_manual_override_persona_v1 \
  --price-csv experiments/real_data/tianjin_tou_price_normalized.csv \
  --workers 4 --fail-fast

echo "[6/6] Generate controlled persona reports"
python experiments/benchmark/generate_baseline_matrix_report.py \
  --summary-json benchmark_results/2026-07-17_recalled_manual_override_persona_v1/_batch_logs/baseline_matrix_summary_tianjin_1days_H6.json \
  --output-dir benchmark_results/2026-07-17_recalled_manual_override_persona_v1/persona_adapt_report \
  --artifact-prefix persona_adapt_manual_override_tianjin \
  --row-label Persona \
  --energy-panel cost
python experiments/benchmark/analyze_persona_adaptability.py \
  --summary-json benchmark_results/2026-07-17_recalled_manual_override_persona_v1/_batch_logs/baseline_matrix_summary_tianjin_1days_H6.json \
  --output-dir benchmark_results/2026-07-17_recalled_manual_override_persona_v1/adaptability_analysis

echo "[OK] Recalled manual-override result package reproduced."
