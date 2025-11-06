"""Default technology catalog.

Importing this package registers the bundled technology specifications into the
shared default registry. Specification data lives in `technologies.yaml` while
field defaults live in `defaults.yaml`; both are merged during loading.
"""
from __future__ import annotations

from pypeline.energy_system.technology_catalog import (
	DEFAULT_TECHNOLOGY_CATALOG,
	TechnologyCatalog,
)
from pypeline.energy_system.technology_registry import (
	DEFAULT_TECHNOLOGY_REGISTRY,
	TechnologyRegistry,
)


def register_default_technologies(registry: TechnologyRegistry | None = None) -> int:
	"""Load packaged technology specs and register them."""
	return DEFAULT_TECHNOLOGY_CATALOG.register_defaults(registry)


register_default_technologies()

__all__ = [
	"DEFAULT_TECHNOLOGY_REGISTRY",
	"DEFAULT_TECHNOLOGY_CATALOG",
	"TechnologyCatalog",
	"register_default_technologies",
]
