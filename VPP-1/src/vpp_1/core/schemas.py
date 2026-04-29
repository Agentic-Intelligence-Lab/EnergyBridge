# :::
# 本文件说明：
# 本文件定义 VPP-1 的核心 dataclass 数据结构。
# 输入包括上游市场/调度任务和默认小型办公建筑目标组。
# 输出包括可序列化的 MarketDispatchTask、TargetBuildingGroup 和 FlexibilityQuery。
# 本文件不做随机生成、不做任务解释、不做物理仿真。
# :::
"""Dataclass schemas for VPP-1."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from vpp_1.core.enums import (
    BuildingArchetype,
    MarketTaskSource,
    MarketTaskType,
    ParticipationMode,
    ResponseTimeScale,
    TriggerReason,
)


@dataclass(slots=True)
class MarketDispatchTask:
    """Upstream market or dispatch task received by VPP-1."""

    task_id: str
    city: str
    source: MarketTaskSource
    task_type: MarketTaskType
    time_scale: ResponseTimeScale
    trigger_reason: TriggerReason
    publish_time: str
    start_time: str
    end_time: str
    notice_minutes: int
    duration_minutes: int
    required_capacity_kw: float
    safety_margin_factor: float
    target_query_capacity_kw: float
    declaration_deadline: str | None
    baseline_method: str | None
    performance_rule: str | None
    reward_description: str | None
    description: str


@dataclass(slots=True)
class TargetBuildingGroup:
    """Small-office representative target group queried by VPP-1."""

    group_id: str
    building_archetype: BuildingArchetype
    representative_mode: bool
    estimated_user_count: int
    main_flexible_asset: str
    participation_mode: ParticipationMode


@dataclass(slots=True)
class FlexibilityQuery:
    """JSON-style building-side capability query command."""

    query_id: str
    query_type: str
    source_task: dict[str, Any]
    target_building_group: dict[str, Any]
    query_window: dict[str, Any]
    requested_assessment: dict[str, Any]
    query_constraints: dict[str, Any]
    local_evaluation_instruction: dict[str, Any]
    response_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a standard Python dict suitable for JSON serialization."""

        return {
            "query_id": self.query_id,
            "query_type": self.query_type,
            "source_task": deepcopy(self.source_task),
            "target_building_group": deepcopy(self.target_building_group),
            "query_window": deepcopy(self.query_window),
            "requested_assessment": deepcopy(self.requested_assessment),
            "query_constraints": deepcopy(self.query_constraints),
            "local_evaluation_instruction": deepcopy(self.local_evaluation_instruction),
            "response_schema": deepcopy(self.response_schema),
        }
