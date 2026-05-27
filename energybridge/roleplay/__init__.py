"""EnergyBridge role-play evaluation package.

Public API:
    load_persona(name_or_path)       -> dict
    load_personas(names=None)        -> list[dict]
    list_personas()                  -> list[str]
    validate_persona(data)           -> dict
    to_legacy_dict(persona)          -> dict
    persona_user_pref_string(p)      -> str
    RoleplayUserSimulator(persona)
    run_roleplay_queue(personas, fn)
    generate_persona_seeds(n, dir)
"""
from energybridge.roleplay.schema import (
    VALID_TAGS, validate_persona, to_legacy_dict, persona_user_pref_string,
)
from energybridge.roleplay.loader import load_persona, load_personas, list_personas
from energybridge.roleplay.simulator import RoleplayUserSimulator
from energybridge.roleplay.runner import run_roleplay_queue
from energybridge.roleplay.generator import generate_persona_seeds

__all__ = [
    "VALID_TAGS", "validate_persona", "to_legacy_dict", "persona_user_pref_string",
    "load_persona", "load_personas", "list_personas",
    "RoleplayUserSimulator", "run_roleplay_queue", "generate_persona_seeds",
]
