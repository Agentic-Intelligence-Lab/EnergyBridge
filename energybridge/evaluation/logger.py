"""Trajectory logging utilities."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def build_trajectory_log_path(log_dir: str = "logs") -> str:
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(directory / f"trajectory_{timestamp}.json")


def save_trajectory(state: dict, log_dir: str = "logs", path: str | None = None) -> str:
    file_path = Path(path) if path else Path(build_trajectory_log_path(log_dir))
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    return str(file_path)
