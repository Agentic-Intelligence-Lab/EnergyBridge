# :::
# 本文件说明：
# 本文件实现 VPP-1 的最小 demo runner。
# 输入是 ScenarioConfig、运行模式和轮数。
# 输出是包含上游任务 dict 和查询 JSON dict 的结果列表。
# 本文件不调用建筑侧 Agent、不调用 EnergyPlus、不执行控制、不结算收益。
# :::
"""VPP-1 runner for repeated task-to-query demos."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from vpp_1.core.serializer import to_plain_dict
from vpp_1.interpreter.task_interpreter import TaskInterpreter
from vpp_1.market.city_task_templates import TaskGenerationConfig
from vpp_1.market.market_task_factory import MarketTaskFactory
from vpp_1.simulation.scenario_config import ScenarioConfig, create_default_scenario_config


class VPP1Runner:
    """Runs VPP-1 task generation and query translation."""

    def __init__(self, config: ScenarioConfig | None = None) -> None:
        self.config = config or create_default_scenario_config()
        self.interpreter = TaskInterpreter()

    def run_once(self, mode: str) -> dict[str, Any]:
        """Generate one task and translate it into a query dict."""

        if mode not in self.config.allowed_task_modes:
            raise ValueError("Unsupported task mode. Allowed modes: invitation, emergency.")

        round_seed = self.config.random_seed + getattr(self, "_round_index", 0)
        base_datetime = datetime(2026, 7, 15, 9, 0) + timedelta(hours=getattr(self, "_round_index", 0))
        task_config = TaskGenerationConfig(
            city="广州" if mode == "invitation" else "深圳",
            random_seed=round_seed,
            base_datetime=base_datetime,
        )
        task = MarketTaskFactory(task_config).create_task_by_mode(mode)
        target_group = self.config.create_target_group()
        query = self.interpreter.build_flexibility_query(task, target_group)

        return {
            "mode": mode,
            "task": to_plain_dict(task),
            "query": query.to_dict(),
        }

    def run_multiple(
        self,
        rounds: int = 10,
        mode_sequence: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Run multiple rounds; by default invitation and emergency alternate."""

        results: list[dict[str, Any]] = []
        sequence = mode_sequence or ["invitation", "emergency"]
        for index in range(rounds):
            self._round_index = index
            mode = sequence[index % len(sequence)]
            results.append(self.run_once(mode))
        return results

    def save_results(self, results: list[dict[str, Any]], output_path: Path) -> None:
        """Save demo results to a JSON file."""

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
