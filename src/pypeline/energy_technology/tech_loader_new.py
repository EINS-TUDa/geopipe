"""Loader for the new categorized technology YAML (migration step a).

Replaces ``tech_loader.py`` for the new type hierarchy. Unlike the old
loader, this one does not infer commodities/stages from names — every
required field must be declared in YAML. Unknown fields are rejected so
typos surface immediately.

Entry points:
- ``load_technology_registry(path)``: read one YAML file into a registry
- ``load_default_technology_registry()``: read the bundled technologies_new.yaml
"""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

import yaml

from .technology_new import (
    CentralTechType,
    CHPType,
    DecentralTechType,
    GridType,
    PipeType,
    TechnologyType,
)
from .technology_registry_new import TechnologyRegistry


# Section key → Type class
_SECTION_CLASS: dict[str, type[TechnologyType]] = {
    "decentralized": DecentralTechType,
    "central": CentralTechType,
    "chp": CHPType,
    "grids": GridType,
    "pipes": PipeType,
}


def load_technology_registry(path: str | Path) -> TechnologyRegistry:
    """Read a technology YAML file into a ``TechnologyRegistry``.

    Strict: unknown top-level sections and unknown per-entry fields raise.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Technology catalog not found: {file_path}")

    raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    if raw is None:
        return TechnologyRegistry()
    if not isinstance(raw, dict):
        raise ValueError(
            f"{file_path}: top-level YAML must be a mapping of sections, got {type(raw).__name__}"
        )

    unknown_sections = set(raw) - set(_SECTION_CLASS)
    if unknown_sections:
        raise ValueError(
            f"{file_path}: unknown section(s) {sorted(unknown_sections)}. "
            f"Valid sections: {sorted(_SECTION_CLASS)}"
        )

    registry = TechnologyRegistry()
    seen_names: set[str] = set()

    for section_name, section_cls in _SECTION_CLASS.items():
        entries = raw.get(section_name)
        if entries is None:
            continue
        if not isinstance(entries, dict):
            raise ValueError(
                f"{file_path}: section '{section_name}' must be a mapping, "
                f"got {type(entries).__name__}"
            )
        target = getattr(registry, section_name)
        for tech_name, payload in entries.items():
            if not isinstance(tech_name, str) or not tech_name:
                raise ValueError(
                    f"{file_path}: section '{section_name}' has invalid key {tech_name!r}"
                )
            if tech_name in seen_names:
                raise ValueError(
                    f"{file_path}: duplicate technology name '{tech_name}'"
                )
            seen_names.add(tech_name)

            if not isinstance(payload, dict):
                raise ValueError(
                    f"{file_path}: '{tech_name}' under '{section_name}' must be a mapping, "
                    f"got {type(payload).__name__}"
                )

            target[tech_name] = _build_type(
                section_cls, tech_name, payload, section_name, file_path
            )

    return registry


def _build_type(
    cls: type[TechnologyType],
    name: str,
    payload: dict[str, Any],
    section: str,
    file_path: Path,
) -> TechnologyType:
    """Build a Type instance, validating fields and wrapping errors with context."""
    allowed = {f.name for f in fields(cls)} - {"name"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(
            f"{file_path}: '{name}' under '{section}' has unknown field(s) "
            f"{sorted(unknown)}. Allowed fields for {cls.__name__}: {sorted(allowed)}"
        )

    try:
        return cls(name=name, **payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{file_path}: failed to build {cls.__name__} '{name}' under '{section}': {exc}"
        ) from exc


def load_default_technology_registry() -> TechnologyRegistry:
    """Load the bundled ``technologies_new.yaml`` from the package."""
    bundled = Path(__file__).resolve().parent / "configs" / "technologies_new.yaml"
    return load_technology_registry(bundled)


__all__ = [
    "load_technology_registry",
    "load_default_technology_registry",
]
