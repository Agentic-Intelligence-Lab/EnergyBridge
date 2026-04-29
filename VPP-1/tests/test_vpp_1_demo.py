from __future__ import annotations

from pathlib import Path

from vpp_1.simulation.scenario_config import create_default_scenario_config
from vpp_1.simulation.vpp_1_runner import VPP1Runner


def test_run_multiple_returns_ten_results() -> None:
    runner = VPP1Runner(create_default_scenario_config())
    results = runner.run_multiple(rounds=10)

    assert len(results) == 10
    for item in results:
        assert "task" in item
        assert "query" in item
        assert item["query"]["query_type"] == "capacity_assessment"


def test_runner_can_save_outputs(tmp_path: Path) -> None:
    runner = VPP1Runner(create_default_scenario_config())
    results = runner.run_multiple(rounds=2)
    output_path = tmp_path / "vpp_1_demo_results.json"

    runner.save_results(results, output_path)

    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8").startswith("[")
