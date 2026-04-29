# :::
# 本文件说明：
# 本文件是 VPP-1 的上游任务生成入口。
# 输入是明确的任务模式 invitation 或 emergency。
# 输出是对应的 MarketDispatchTask。
# 本文件不支持 random 模式，不保留旧兼容接口，不生成建筑侧查询。
# :::
"""Market task factory for VPP-1."""

from __future__ import annotations

from vpp_1.core.schemas import MarketDispatchTask
from vpp_1.market.city_task_templates import (
    TaskGenerationConfig,
    generate_emergency_dr_task,
    generate_invitation_dr_task,
)


class MarketTaskFactory:
    """Factory for explicit invitation or emergency tasks."""

    def __init__(self, config: TaskGenerationConfig | None = None) -> None:
        self.config = config or TaskGenerationConfig()

    def create_invitation_task(self) -> MarketDispatchTask:
        """Create an invitation demand-response task."""

        return generate_invitation_dr_task(self.config)

    def create_emergency_task(self) -> MarketDispatchTask:
        """Create an emergency demand-response task."""

        emergency_config = TaskGenerationConfig(
            city=self.config.city,
            random_seed=self.config.random_seed,
            base_datetime=self.config.base_datetime,
            invitation_capacity_range_kw=self.config.invitation_capacity_range_kw,
            emergency_capacity_range_kw=self.config.emergency_capacity_range_kw,
            invitation_safety_margin_range=self.config.invitation_safety_margin_range,
            emergency_safety_margin_range=self.config.emergency_safety_margin_range,
        )
        return generate_emergency_dr_task(emergency_config)

    def create_task_by_mode(self, mode: str) -> MarketDispatchTask:
        """Create a task by explicit mode: invitation or emergency."""

        normalized = mode.strip().lower()
        if normalized == "invitation":
            return self.create_invitation_task()
        if normalized == "emergency":
            return self.create_emergency_task()
        raise ValueError("Unsupported task mode. Allowed modes: invitation, emergency.")
