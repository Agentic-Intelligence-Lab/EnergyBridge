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
#   EnergyPlus-24-1-0 at /home/hku_user/EnergyPlus-24-1-0/
#   EPW files at /home/ha_agent/work/supporting/weather/epw/
#   IDF files at /home/ha_agent/work/supporting/models/

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
RESULTS_DIR="$SCRIPT_DIR/results"

echo "================================================"
echo " EnergyBridge Benchmark Suite"
echo " $(date)"
echo "================================================"

# Activate conda env
source /home/ha_agent/miniconda3/etc/profile.d/conda.sh
conda activate energybridge

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
