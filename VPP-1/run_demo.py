# :::
# 本文件说明：
# 本文件是 VPP-1 的命令行 demo 入口。
# 它连续运行 10 轮上游任务生成与建筑侧能力查询 JSON 生成流程，
# 打印每轮任务摘要和完整查询 JSON，并保存结果到 outputs/vpp_1_demo_results.json。
# 本文件不负责建筑侧仿真、控制执行、用户通知或收益结算。
# :::
"""Run the VPP-1 10-round demo."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from vpp_1.simulation.scenario_config import create_default_scenario_config  # noqa: E402
from vpp_1.simulation.vpp_1_runner import VPP1Runner  # noqa: E402


def main() -> None:
    config = create_default_scenario_config()
    runner = VPP1Runner(config)
    results = runner.run_multiple(rounds=10)

    for index, item in enumerate(results, start=1):
        task = item["task"]
        query = item["query"]
        print(f"[VPP-1 第 {index} 轮]")
        print(f"任务类型：{task['task_type']}")
        print(f"城市：{task['city']}")
        print(f"发布时间：{task['publish_time']}")
        print(f"响应窗口：{task['start_time']} -> {task['end_time']}")
        print(f"需求容量：{task['required_capacity_kw']:.2f} kW")
        print(f"目标查询容量：{task['target_query_capacity_kw']:.2f} kW")
        print("查询 JSON：")
        print(json.dumps(query, ensure_ascii=False, indent=2))
        print()

    output_path = PROJECT_ROOT / "outputs" / "vpp_1_demo_results.json"
    runner.save_results(results, output_path)
    print(f"完整结果已保存到：{output_path}")


if __name__ == "__main__":
    main()
