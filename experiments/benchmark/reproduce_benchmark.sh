#!/bin/bash
# EnergyBridge Benchmark — 一键复现脚本
# 3 城市 × 2 建筑 × 2 方法 = 12 EnergyPlus 仿真
#
# 使用方法:
#   bash reproduce_benchmark.sh          # 全量运行（清除旧结果）
#   bash reproduce_benchmark.sh --resume  # 跳过已完成的场景
#
# 依赖:
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
