from __future__ import annotations

from vpp_1.core.enums import MarketTaskType, ResponseTimeScale
from vpp_1.market.city_task_templates import (
    TaskGenerationConfig,
    generate_emergency_dr_task,
    generate_invitation_dr_task,
)


def test_invitation_task_generation() -> None:
    task = generate_invitation_dr_task(TaskGenerationConfig(random_seed=1))

    assert task.task_type == MarketTaskType.INVITATION_DEMAND_RESPONSE
    assert task.time_scale in {ResponseTimeScale.DAY_AHEAD, ResponseTimeScale.INTRADAY}
    assert task.required_capacity_kw > 0
    assert task.target_query_capacity_kw == task.required_capacity_kw * task.safety_margin_factor


def test_emergency_task_generation() -> None:
    task = generate_emergency_dr_task(TaskGenerationConfig(city="深圳", random_seed=2))

    assert task.task_type == MarketTaskType.EMERGENCY_DEMAND_RESPONSE
    assert task.time_scale == ResponseTimeScale.REAL_TIME
    assert task.notice_minutes == 30
    assert task.target_query_capacity_kw == task.required_capacity_kw * task.safety_margin_factor
