"""HEMA baseline for EnergyBridge."""

from .path_utils import ensure_hema_imports


def get_hema_controller():
    ensure_hema_imports()
    from .hema_controller import HEMAControlBaseline
    return HEMAControlBaseline


__all__ = ["get_hema_controller"]
