"""Compatibility layer for default technology registration.

The bundled technology specifications now reside under
``pypeline.energy_system.configs``. Importing this package delegates to the new
location so legacy imports keep working unchanged.
"""
from __future__ import annotations

from pypeline.energy_system.configs import *  # noqa: F401,F403

__all__: list[str] = []

