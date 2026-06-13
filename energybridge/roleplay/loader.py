"""Persona loader — file-based loading of persona JSON files."""
from __future__ import annotations
import json, warnings
from pathlib import Path
from energybridge.roleplay.calendar import attach_calendar
from energybridge.roleplay.schema import validate_persona

PERSONAS_DIR = Path(__file__).parent / "personas"


def load_persona(name_or_path: str | Path) -> dict:
    """Load and validate a single persona by ID or file path."""
    p = Path(name_or_path)
    if p.is_absolute() or p.exists():
        path = p
    else:
        path = PERSONAS_DIR / f"{name_or_path}.json"
    if not path.exists():
        avail = sorted(f.stem for f in PERSONAS_DIR.glob("*.json"))
        raise FileNotFoundError(
            f"Persona '{name_or_path}' not found at {path}.\n"
            f"Available: {avail}"
        )
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    validate_persona(data)
    return attach_calendar(data, PERSONAS_DIR)


def load_personas(
    names_or_paths: list[str | Path] | None = None,
    *,
    approved_only: bool = True,
) -> list[dict]:
    """Load one or more personas. None = load all from PERSONAS_DIR."""
    if names_or_paths is not None:
        return [load_persona(n) for n in names_or_paths]
    personas = []
    for path in sorted(PERSONAS_DIR.glob("*.json")):
        try:
            p = load_persona(path)
        except (ValueError, json.JSONDecodeError) as exc:
            warnings.warn(f"Skipping {path.name}: {exc}")
            continue
        if approved_only and not p.get("meta", {}).get("approved", False):
            continue
        personas.append(p)
    return personas


def list_personas(*, approved_only: bool = True) -> list[str]:
    """Return sorted list of available persona IDs."""
    ids = []
    for path in sorted(PERSONAS_DIR.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        if approved_only and not data.get("meta", {}).get("approved", False):
            continue
        ids.append(data.get("id", path.stem))
    return ids
