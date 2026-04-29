"""Schemas used at the VPP-1 integration boundary."""

from typing import TypedDict


class VPP1RawSignal(TypedDict, total=False):
    eventCode: str
    windowStart: str
    windowEnd: str
    reductionTargetKw: float
    tariff: str


class EnergyBridgeGridSignal(TypedDict):
    type: str
    start_time: str
    end_time: str
    target_reduction_kw: float
    price_level: str
