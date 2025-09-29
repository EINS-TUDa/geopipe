"""Technology catalog utilities.

Provides a single entrypoint for loading default technology specifications from
packaged JSON files and registering them into the global registry.

Layering goal:
  - technology_spec / tech_loader: pure data definitions + parsing
  - technology / technology_registry: runtime objects and registry
  - catalog: orchestration that bridges specs to runtime registration
"""
from __future__ import annotations

from typing import Iterable

from pypeline.energy_system.technology_registry import TechnologyRegistry, DEFAULT_TECHNOLOGY_REGISTRY
from pypeline.energy_system.tech_loader import load_specs_from_package, instantiate_all


def register_default_technologies(registry: TechnologyRegistry | None = None) -> int:
    """Load packaged JSON technology specs and register them.

    Returns the number of technologies registered (new additions only).
    Existing names are skipped silently.
    """
    if registry is None:
        registry = DEFAULT_TECHNOLOGY_REGISTRY
    specs = load_specs_from_package()
    count = 0
    for tech in instantiate_all(specs):
        if not registry.has_technology(tech.name):
            registry.register(tech)
            count += 1
    return count


__all__ = ["register_default_technologies"]