#!/bin/bash
# EnergyBridge Benchmark - one-command reproduction script
# 3 cities x 2 buildings x 2 methods = 12 EnergyPlus simulations
#
# Usage:
#   bash reproduce_benchmark.sh           # full run; clears old office results
#   bash reproduce_benchmark.sh --resume  # skip completed scenarios
#
# Dependencies:
#   conda activate energybridge
#   EnergyPlus 24.1 configured through EPLUS_ROOT
#   EPW files configured through ENERGYBRIDGE_EPW_ROOT
#   IDF files configured through ENERGYBRIDGE_IDF_ROOT

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
RESULTS_DIR="$SCRIPT_DIR/results"

echo "================================================"
echo " EnergyBridge Benchmark Suite"
echo " $(date)"
echo "================================================"

# Activate the configured conda environment. Set CONDA_SH when conda has not
# already initialized the current shell.
if [[ -n "${CONDA_SH:-}" ]]; then
    source "$CONDA_SH"
fi
conda activate "${ENERGYBRIDGE_CONDA_ENV:-energybridge}"

cd "$SCRIPT_DIR"

if [[ "$1" == "--resume" ]]; then
    echo "[Resume mode: skipping existing results]"
    python run_benchmark.py --skip-existing 2>&1 | tee "$RESULTS_DIR/run_$(date +%Y%m%d_%H%M%S).log"
else
    echo "[Full run: clearing office results]"
    rm -rf "$RESULTS_DIR"/office_*
    python run_benchmark.py --skip-existing 2>&1 | tee "$RESULTS_DIR/run_$(date +%Y%m%d_%H%M%S).log"
fi

echo ""
echo "Results saved to: $RESULTS_DIR/benchmark_results.json"
echo "Table saved to:   $RESULTS_DIR/benchmark_table.txt"
