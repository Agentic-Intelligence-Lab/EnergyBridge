"""HEMA baseline for EnergyBridge."""
import sys
from pathlib import Path


def _ensure_hema_imports():
    """Fix 'agents' package name conflict between EB and HEMA."""
    hema_root = Path(__file__).resolve().parent.parent.parent.parent.parent / "HEMA"
    hema_str = str(hema_root)

    if hema_str not in sys.path:
        sys.path.insert(0, hema_str)

    if 'agents' in sys.modules:
        agents_file = str(getattr(sys.modules['agents'], '__file__', ''))
        if 'HEMA' not in agents_file.replace('\\', '/'):
            for k in list(sys.modules.keys()):
                if k == 'agents' or k.startswith('agents.'):
                    del sys.modules[k]


def get_hema_controller():
    _ensure_hema_imports()
    from .hema_controller import HEMAControlBaseline
    return HEMAControlBaseline


__all__ = ["get_hema_controller"]