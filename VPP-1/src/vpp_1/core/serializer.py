# :::
# 本文件说明：
# 本文件提供 VPP-1 中 dataclass 和枚举对象的 JSON 序列化辅助函数。
# 输入是 MarketDispatchTask、FlexibilityQuery 或普通嵌套字典。
# 输出是标准 Python dict 或 JSON 字符串。
# 本文件不负责生成任务、不负责解释任务，也不写文件。
# :::
"""Serialization helpers for VPP-1."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any


def to_plain_dict(value: Any) -> Any:
    """Convert dataclasses and enums into JSON-friendly Python objects."""

    if hasattr(value, "to_dict"):
        return to_plain_dict(value.to_dict())
    if is_dataclass(value):
        return to_plain_dict(asdict(value))
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, dict):
        return {key: to_plain_dict(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_plain_dict(item) for item in value]
    return value


def to_json(value: Any, *, indent: int = 2) -> str:
    """Serialize a value into JSON text."""

    return json.dumps(to_plain_dict(value), ensure_ascii=False, indent=indent)
