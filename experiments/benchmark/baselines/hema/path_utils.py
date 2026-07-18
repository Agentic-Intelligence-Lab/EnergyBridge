"""Path helpers for the external HEMA reference checkout."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def hema_root() -> Path:
    configured = os.getenv("ENERGYBRIDGE_HEMA_ROOT") or os.getenv("HEMA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[5] / "reference" / "HEMA"


def ensure_hema_imports() -> Path:
    root = hema_root()
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    if "agents" in sys.modules:
        agents_file = str(getattr(sys.modules["agents"], "__file__", ""))
        if "HEMA" not in agents_file.replace("\\", "/"):
            for key in list(sys.modules.keys()):
                if key == "agents" or key.startswith("agents."):
                    del sys.modules[key]
    return root
