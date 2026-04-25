"""Typed technology registry for the new type hierarchy (migration step a).

Replaces ``technology_registry.TechnologyRegistry`` for the new flow.
At step c, the old module is deleted and this file is renamed to
``technology_registry.py``.

Holds ``*Type`` instances keyed per kind. Names are globally unique —
a given name appears in exactly one section.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from .technology_new import (
    CentralTechType,
    CHPType,
    DecentralTechType,
    GridType,
    PipeType,
    TechnologyType,
)


@dataclass
class TechnologyRegistry:
    """Typed registry of technology templates keyed per kind.

    Use ``all_types()`` for a flat view, or iterate per kind for
    kind-specific handling (e.g. ``for ct in registry.central.values()``).
    """

    decentralized: dict[str, DecentralTechType] = field(default_factory=dict)
    central: dict[str, CentralTechType] = field(default_factory=dict)
    chp: dict[str, CHPType] = field(default_factory=dict)
    grids: dict[str, GridType] = field(default_factory=dict)
    pipes: dict[str, PipeType] = field(default_factory=dict)

    def all_types(self) -> dict[str, TechnologyType]:
        merged: dict[str, TechnologyType] = {}
        for section in (self.decentralized, self.central, self.chp, self.grids, self.pipes):
            for name, tech in section.items():
                if name in merged:
                    raise ValueError(
                        f"Duplicate technology name '{name}' appears in multiple sections"
                    )
                merged[name] = tech
        return merged

    def __getitem__(self, name: str) -> TechnologyType:
        try:
            return self.all_types()[name]
        except KeyError:
            raise KeyError(f"Technology '{name}' not found in registry") from None

    def __contains__(self, name: str) -> bool:
        return name in self.all_types()

    def __iter__(self) -> Iterator[str]:
        return iter(self.all_types())


__all__ = ["TechnologyRegistry"]
