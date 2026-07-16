#!/usr/bin/env bash
set -euo pipefail

cd /home/hku_user/work/EnergyBridge

echo "[1/8] Verify fair fallback tests"
PYTHONPATH=. pytest -q experiments/benchmark/tests/test_family_runner_skip_confirmation.py
python -m py_compile \
  experiments/benchmark/family_runner.py \
  experiments/benchmark/analyze_eb_capacity_loop.py \
  experiments/benchmark/create_controlled_adapt_personas.py \
  experiments/benchmark/analyze_persona_adaptability.py

echo "[2/8] Run non-EB household matrix with fair fallback"
python experiments/benchmark/run_household_matrix.py \
  --households household_s1_dual_commuter_standard household_s2_multigeneration_caregiver household_s3_hybrid_work_from_home household_s4_ev_commuter_flexible household_s5_shared_roommates_irregular \
  --methods mpc_dynamic rule_milp rl_ppo_pref_v2 \
  --city Tianjin --days 1 \
  --date 2026-07-17_paper_household_fair_fallback_non_eb_v1 \
  --price-csv experiments/real_data/tianjin_tou_price_normalized.csv \
  --workers 4 --fail-fast

echo "[3/8] Merge reused EB rows with rerun non-EB rows"
python - <<'PY'
import csv, json
from pathlib import Path

root = Path('/home/hku_user/work/EnergyBridge')
eb_json = root / 'benchmark_results/2026-07-17_paper_household_cost_rank_v2/_batch_logs/household_matrix_summary_tianjin_1days_H6.json'
eb_csv = root / 'benchmark_results/2026-07-17_paper_household_cost_rank_v2/_batch_logs/household_matrix_summary_tianjin_1days_H6.csv'
non_json = root / 'benchmark_results/2026-07-17_paper_household_fair_fallback_non_eb_v1/_batch_logs/household_matrix_summary_tianjin_1days_H6.json'
non_csv = root / 'benchmark_results/2026-07-17_paper_household_fair_fallback_non_eb_v1/_batch_logs/household_matrix_summary_tianjin_1days_H6.csv'
out = root / 'benchmark_results/2026-07-17_paper_household_fair_fallback_merged_v1'
logs = out / '_batch_logs'
logs.mkdir(parents=True, exist_ok=True)

rows = [r for r in json.loads(eb_json.read_text(encoding='utf-8')) if r.get('method') == 'EnergyBridge']
rows += [r for r in json.loads(non_json.read_text(encoding='utf-8')) if r.get('method') != 'EnergyBridge']
method_order = {'EnergyBridge': 0, 'mpc_dynamic': 1, 'rule_milp': 2, 'rl_ppo_pref_v2': 3}
rows.sort(key=lambda r: (r.get('persona_id') or r.get('household_id') or '', method_order.get(r.get('method'), 99)))

out_json = logs / 'household_matrix_summary_tianjin_1days_H6.json'
out_csv = logs / 'household_matrix_summary_tianjin_1days_H6.csv'
out_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')

fieldnames = []
for path in (eb_csv, non_csv):
    with path.open(newline='', encoding='utf-8') as fh:
        for name in csv.DictReader(fh).fieldnames or []:
            if name not in fieldnames:
                fieldnames.append(name)
for row in rows:
    for name in row:
        if name not in fieldnames:
            fieldnames.append(name)
with out_csv.open('w', newline='', encoding='utf-8') as fh:
    writer = csv.DictWriter(fh, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, '') for key in fieldnames})
print(out_json)
print(out_csv)
PY

echo "[4/8] Generate household main report"
python experiments/benchmark/generate_baseline_matrix_report.py \
  --summary-json benchmark_results/2026-07-17_paper_household_fair_fallback_merged_v1/_batch_logs/household_matrix_summary_tianjin_1days_H6.json \
  --output-dir benchmark_results/2026-07-17_paper_household_fair_fallback_merged_v1/mainfig_household_5x1_fair_fallback_report \
  --artifact-prefix household_5x1_tianjin_fair_fallback \
  --row-label Household \
  --energy-panel cost

echo "[5/8] Run EB-only capacity loop analysis"
python experiments/benchmark/analyze_eb_capacity_loop.py \
  --summary-json benchmark_results/2026-07-17_paper_household_cost_rank_v2/_batch_logs/household_matrix_summary_tianjin_1days_H6.json \
  --output-dir benchmark_results/2026-07-17_paper_household_fair_fallback_merged_v1/capacity_loop_eb_only

echo "[6/8] Generate controlled adaptability personas"
python experiments/benchmark/create_controlled_adapt_personas.py

echo "[7/8] Run controlled persona matrix"
python experiments/benchmark/run_baseline_matrix.py \
  --personas paper_adapt_a_price_cooperative paper_adapt_b_comfort_gated paper_adapt_c_irregular_cautious paper_adapt_d_ideal_dr paper_adapt_e_caregiver_low_dr \
  --methods EnergyBridge mpc_dynamic rule_milp rl_ppo_pref_v2 \
  --city Tianjin --days 1 \
  --date 2026-07-17_paper_persona_adapt_controlled_v1 \
  --price-csv experiments/real_data/tianjin_tou_price_normalized.csv \
  --workers 4 --fail-fast

echo "[8/8] Generate controlled persona reports"
python experiments/benchmark/generate_baseline_matrix_report.py \
  --summary-json benchmark_results/2026-07-17_paper_persona_adapt_controlled_v1/_batch_logs/baseline_matrix_summary_tianjin_1days_H6.json \
  --output-dir benchmark_results/2026-07-17_paper_persona_adapt_controlled_v1/persona_adapt_report \
  --artifact-prefix persona_adapt_controlled_tianjin \
  --row-label Persona \
  --energy-panel cost
python experiments/benchmark/analyze_persona_adaptability.py \
  --summary-json benchmark_results/2026-07-17_paper_persona_adapt_controlled_v1/_batch_logs/baseline_matrix_summary_tianjin_1days_H6.json \
  --output-dir benchmark_results/2026-07-17_paper_persona_adapt_controlled_v1/adaptability_analysis

echo "[OK] Paper experiment package reproduced."
