"""Thin shim: persona loading for the family benchmark.

All persona data now lives in energybridge/roleplay/personas/*.json.
This file exists for backward compatibility with family_runner.py imports.
"""
from __future__ import annotations
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from energybridge.roleplay.loader import load_persona, load_personas, list_personas
from energybridge.roleplay.schema import to_legacy_dict, persona_user_pref_string


def get_persona(name: str) -> dict:
    """Load persona by ID and return legacy flat dict. Raises KeyError if not found."""
    try:
        json_persona = load_persona(name)
    except FileNotFoundError as exc:
        raise KeyError(str(exc)) from exc
    return to_legacy_dict(json_persona)


__all__ = [
    "get_persona", "persona_user_pref_string",
    "load_persona", "load_personas", "list_personas",
]
