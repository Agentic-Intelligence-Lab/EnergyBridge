# :::
# 本文件说明：
# 本文件定义 VPP-1 中跨模块共享的枚举类型。
# 输入是代码内部对任务、来源、时间尺度、触发原因和建筑类型的分类需求。
# 输出是标准化枚举，供任务生成器、任务翻译器和测试共同使用。
# 本文件不负责随机生成任务，也不负责生成 JSON 查询。
# :::
"""Enum definitions for VPP-1."""

from __future__ import annotations

from enum import Enum


class MarketTaskType(str, Enum):
    """Upstream task types supported by VPP-1."""

    INVITATION_DEMAND_RESPONSE = "invitation_demand_response"
    EMERGENCY_DEMAND_RESPONSE = "emergency_demand_response"


class MarketTaskSource(str, Enum):
    """Possible upstream task sources."""

    GRID_DISPATCH_CENTER = "grid_dispatch_center"
    LOAD_MANAGEMENT_CENTER = "load_management_center"
    VPP_MANAGEMENT_PLATFORM = "vpp_management_platform"
    MARKET_PLATFORM = "market_platform"


class ResponseTimeScale(str, Enum):
    """Time scale of the requested response."""

    DAY_AHEAD = "day_ahead"
    INTRADAY = "intraday"
    REAL_TIME = "real_time"


class TriggerReason(str, Enum):
    """Reason that triggers the upstream task."""

    REGIONAL_PEAK_LOAD = "regional_peak_load"
    POWER_SHORTAGE = "power_shortage"
    LOCAL_OVERLOAD = "local_overload"
    PRICE_SIGNAL = "price_signal"


class BuildingArchetype(str, Enum):
    """Building archetypes supported by VPP-1."""

    SMALL_COMMERCIAL_HVAC = "small_commercial_hvac"


class ParticipationMode(str, Enum):
    """Participation confirmation mode used in the query."""

    MANUAL_CONFIRM = "manual_confirm"
