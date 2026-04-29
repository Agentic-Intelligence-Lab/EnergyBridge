# :::
# 本文件说明：
# 本文件用于生成 VPP-1 接收到的上游市场/调度任务。
# 输入是 TaskGenerationConfig，输出是 MarketDispatchTask。
# 当前只支持邀约型需求响应 invitation 和紧急型需求响应 emergency。
# 本文件不生成随机 fallback 模式，不生成建筑侧查询，也不生成控制命令。
# :::
"""City-level upstream task generators for VPP-1."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from vpp_1.core.enums import (
    MarketTaskSource,
    MarketTaskType,
    ResponseTimeScale,
    TriggerReason,
)
from vpp_1.core.schemas import MarketDispatchTask


@dataclass(slots=True)
class TaskGenerationConfig:
    """Configuration for deterministic or random task generation."""

    city: str = "广州"
    random_seed: int | None = None
    base_datetime: datetime | None = None
    invitation_capacity_range_kw: tuple[float, float] = (10000.0, 80000.0)
    emergency_capacity_range_kw: tuple[float, float] = (20000.0, 200000.0)
    invitation_safety_margin_range: tuple[float, float] = (1.2, 1.5)
    emergency_safety_margin_range: tuple[float, float] = (1.5, 2.0)


def generate_invitation_dr_task(config: TaskGenerationConfig | None = None) -> MarketDispatchTask:
    """Generate one invitation demand-response task."""

    config = config or TaskGenerationConfig()
    rng = random.Random(config.random_seed)
    base_dt = _base_datetime(config).replace(second=0, microsecond=0)
    response_date = base_dt.date() + timedelta(days=1)
    start_hour, start_minute = rng.choice([(10, 0), (14, 0), (18, 0), (19, 0)])
    start_dt = datetime.combine(response_date, datetime.min.time()).replace(
        hour=start_hour,
        minute=start_minute,
    )
    duration_minutes = rng.choice([30, 60, 90, 120, 180, 240])
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    time_scale = rng.choice([ResponseTimeScale.DAY_AHEAD, ResponseTimeScale.INTRADAY])
    if time_scale == ResponseTimeScale.DAY_AHEAD:
        publish_dt = datetime.combine(base_dt.date(), datetime.min.time()).replace(
            hour=rng.choice([9, 10]),
            minute=0,
        )
    else:
        publish_dt = start_dt - timedelta(hours=4)

    required_capacity = _random_float(rng, config.invitation_capacity_range_kw, 2)
    safety_margin = _random_float(rng, config.invitation_safety_margin_range, 3)
    target_query_capacity = required_capacity * safety_margin

    return MarketDispatchTask(
        task_id=f"INV_{publish_dt:%Y%m%d}_{rng.randint(1000, 9999)}",
        city=config.city,
        source=MarketTaskSource.VPP_MANAGEMENT_PLATFORM,
        task_type=MarketTaskType.INVITATION_DEMAND_RESPONSE,
        time_scale=time_scale,
        trigger_reason=rng.choice(
            [
                TriggerReason.REGIONAL_PEAK_LOAD,
                TriggerReason.POWER_SHORTAGE,
                TriggerReason.LOCAL_OVERLOAD,
                TriggerReason.PRICE_SIGNAL,
            ]
        ),
        publish_time=_format_dt(publish_dt),
        start_time=_format_dt(start_dt),
        end_time=_format_dt(end_dt),
        notice_minutes=_minutes_between(publish_dt, start_dt),
        duration_minutes=duration_minutes,
        required_capacity_kw=required_capacity,
        safety_margin_factor=safety_margin,
        target_query_capacity_kw=target_query_capacity,
        declaration_deadline=_format_dt(min(publish_dt + timedelta(hours=1), start_dt)),
        baseline_method="邀约型需求响应基线，VPP-1 仅保留任务说明。",
        performance_rule="VPP-1 只生成能力查询，不做绩效结算。",
        reward_description="邀约型需求响应补偿说明，VPP-1 不计算收益。",
        description="邀约型削峰需求响应任务，VPP 需要查询建筑侧资源在目标时段内可提供的负荷削减能力。",
    )


def generate_emergency_dr_task(config: TaskGenerationConfig | None = None) -> MarketDispatchTask:
    """Generate one emergency demand-response task."""

    config = config or TaskGenerationConfig(city="深圳")
    rng = random.Random(config.random_seed)
    publish_dt = _base_datetime(config).replace(second=0, microsecond=0)
    start_dt = publish_dt + timedelta(minutes=30)
    duration_minutes = rng.choice([30, 60, 90])
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    required_capacity = _random_float(rng, config.emergency_capacity_range_kw, 2)
    safety_margin = _random_float(rng, config.emergency_safety_margin_range, 3)
    target_query_capacity = required_capacity * safety_margin

    return MarketDispatchTask(
        task_id=f"EMG_{publish_dt:%Y%m%d_%H%M}_{rng.randint(1000, 9999)}",
        city=config.city,
        source=MarketTaskSource.GRID_DISPATCH_CENTER,
        task_type=MarketTaskType.EMERGENCY_DEMAND_RESPONSE,
        time_scale=ResponseTimeScale.REAL_TIME,
        trigger_reason=rng.choice(
            [
                TriggerReason.REGIONAL_PEAK_LOAD,
                TriggerReason.LOCAL_OVERLOAD,
                TriggerReason.POWER_SHORTAGE,
            ]
        ),
        publish_time=_format_dt(publish_dt),
        start_time=_format_dt(start_dt),
        end_time=_format_dt(end_dt),
        notice_minutes=30,
        duration_minutes=duration_minutes,
        required_capacity_kw=required_capacity,
        safety_margin_factor=safety_margin,
        target_query_capacity_kw=target_query_capacity,
        declaration_deadline=_format_dt(publish_dt + timedelta(minutes=15)),
        baseline_method="紧急需求响应基线，VPP-1 仅保留任务说明。",
        performance_rule="VPP-1 只生成能力查询，不做绩效结算。",
        reward_description="紧急需求响应补偿说明，VPP-1 不计算收益。",
        description="紧急削峰需求响应任务，VPP 需要快速查询建筑侧资源在目标时段内可提供的负荷削减能力。",
    )


def _base_datetime(config: TaskGenerationConfig) -> datetime:
    return config.base_datetime or datetime(2026, 7, 15, 9, 0)


def _random_float(rng: random.Random, value_range: tuple[float, float], digits: int) -> float:
    low, high = value_range
    if low == high:
        return float(low)
    return round(rng.uniform(low, high), digits)


def _minutes_between(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def _format_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")
