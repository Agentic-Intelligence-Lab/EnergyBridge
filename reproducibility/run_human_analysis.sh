#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
output_root="${ENERGYBRIDGE_GENERATED_RESULTS_ROOT:-${repo_root}/generated_results}"

cd "${repo_root}"
python human_roleplay_data/scripts/validate_release.py
python human_roleplay_data/scripts/reproduce_analysis.py \
  --output-dir "${output_root}/human_roleplay" \
  --bootstrap-replicates 10000 \
  --bootstrap-seed 1

echo "Generated human-only analysis: ${output_root}/human_roleplay"
