"""
trinity/modules/__init__.py
Singletons para módulos auxiliares do Trinity.
"""
from __future__ import annotations

_deep_dive_instance = None


def get_deep_dive_trigger():
    """Singleton do DeepDiveTrigger — compartilhado entre traders e server."""
    global _deep_dive_instance
    if _deep_dive_instance is None:
        from trinity.modules.deep_dive_trigger import DeepDiveTrigger
        _deep_dive_instance = DeepDiveTrigger()
    return _deep_dive_instance
