"""Default technology catalog helpers.

Specification data lives in technologies.yaml while field defaults live in
defaults.yaml. Consumers must call
register_default_technologies or TechnologyRegistry.load_from_default
to populate a registry.
"""
from __future__ import annotations
from ..technology_catalog import (DEFAULT_TECHNOLOGY_CATALOG, TechnologyCatalog)
from ..technology_registry import TechnologyRegistry

_PRIMARY_HEAT_EXCHANGER = "heat_exchanger"


def register_default_technologies(registry: TechnologyRegistry | None = None) -> int:
    """Load packaged technology specs and register them."""
    if registry is None:
        from ..technology_registry import get_default_technology_registry
        registry = get_default_technology_registry()
        
    count = DEFAULT_TECHNOLOGY_CATALOG.register_defaults(registry)
    
    if registry.has_technology(_PRIMARY_HEAT_EXCHANGER):
        registry.get_by_name(_PRIMARY_HEAT_EXCHANGER)
        
    return count


__all__ = [
    "DEFAULT_TECHNOLOGY_CATALOG",
    "TechnologyCatalog",
    "get_default_technology_registry",
    "register_default_technologies",
]
