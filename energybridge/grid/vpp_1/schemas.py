"""Schemas used at the VPP-1 integration boundary."""

from typing import TypedDict


class VPP1RawSignal(TypedDict, total=False):
    eventCode: str
    windowStart: str
    windowEnd: str
    reductionTargetKw: float
    tariff: str


class EnergyBridgeGridDemand(TypedDict):
    type: str
    start_time: str
    end_time: str
    duration_minutes: int
    price_level: str
    control_intent: str
    urgency: str
    strictness: str
    total_required_capacity_kw: float
    response_deadline: str
    capacity_scope: str


class VPPContext(TypedDict):
    vpp_task_id: str
    vpp_query_id: str
    vpp_task_type: str
    vpp_time_scale: str
    vpp_trigger_reason: str
    vpp_start_time: str
    vpp_end_time: str
    vpp_notice_minutes: int
    vpp_duration_minutes: int
    vpp_required_capacity_kw: float
    vpp_declaration_deadline: str
    vpp_response_direction: str
    vpp_capacity_scope: str
