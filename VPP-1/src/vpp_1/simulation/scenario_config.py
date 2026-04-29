# :::
# 本文件说明：
# 本文件定义 VPP-1 默认仿真场景配置。
# 输入是默认参数，输出是 ScenarioConfig 和 TargetBuildingGroup。
# 当前场景是小型办公建筑、HVAC、人工确认、单体代表性仿真。
# 本文件不生成市场任务，也不生成 JSON 查询。
# :::
"""Scenario configuration for VPP-1."""

from __future__ import annotations

from dataclasses import dataclass, field

from vpp_1.core.enums import BuildingArchetype, ParticipationMode
from vpp_1.core.schemas import TargetBuildingGroup


@dataclass(slots=True)
class ScenarioConfig:
    """Default VPP-1 small-office scenario."""

    scenario_name: str = "vpp_1_small_office_demo"
    default_task_mode: str = "invitation"
    allowed_task_modes: list[str] = field(default_factory=lambda: ["invitation", "emergency"])
    target_group_id: str = "small_office_group_A"
    building_archetype: BuildingArchetype = BuildingArchetype.SMALL_COMMERCIAL_HVAC
    representative_mode: bool = True
    estimated_user_count: int = 1
    main_flexible_asset: str = "HVAC"
    participation_mode: ParticipationMode = ParticipationMode.MANUAL_CONFIRM
    random_seed: int = 42

    def create_target_group(self) -> TargetBuildingGroup:
        """Create the default small-office target group."""

        return TargetBuildingGroup(
            group_id=self.target_group_id,
            building_archetype=self.building_archetype,
            representative_mode=self.representative_mode,
            estimated_user_count=self.estimated_user_count,
            main_flexible_asset=self.main_flexible_asset,
            participation_mode=self.participation_mode,
        )


def create_default_scenario_config() -> ScenarioConfig:
    """Create the default VPP-1 scenario configuration."""

    return ScenarioConfig()
