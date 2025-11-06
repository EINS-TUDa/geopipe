"""Composable catalog for loading and analysing technology specifications.

The catalog coordinates one or more specification providers, validates their
output, and produces runtime Technology instances ready for registration.
It also offers lightweight analytic helpers so callers can understand how the
specs relate to commodities, categories, and stages before wiring them into the
energy system.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, MutableMapping, Protocol

from pypeline.energy_system.tech_loader import (
    instantiate_all,
    load_specs_from_package,
)
from pypeline.energy_system.technology_registry import (
    DEFAULT_TECHNOLOGY_REGISTRY,
    TechnologyRegistry,
)
from pypeline.energy_system.technology_spec import (
    TechnologySpec,
    validate_specs,
)
from pypeline.energy_system.technology_stage import TechnologyCategory, TechnologyStage


class SpecProvider(Protocol):
    """A source that can yield TechnologySpec instances."""

    def iter_specs(self) -> Iterable[TechnologySpec]:  # pragma: no cover - structural typing
        ...


class YamlPackageSpecProvider:
    """Provide specs by reading the bundled YAML file from the package."""

    def __init__(self, extra_spec_files: Iterable[str | Path] | None = None) -> None:
        self._extra_spec_files = list(extra_spec_files or [])

    def iter_specs(self) -> Iterable[TechnologySpec]:
        return load_specs_from_package(self._extra_spec_files)


@dataclass(slots=True)
class TechnologyCatalogSummary:
    """Lightweight view on catalog contents for downstream orchestration."""

    specs_by_stage: Mapping[TechnologyStage, List[TechnologySpec]]
    specs_by_category: Mapping[TechnologyCategory, List[TechnologySpec]]
    specs_by_input: Mapping[str, List[TechnologySpec]]
    specs_by_output: Mapping[str, List[TechnologySpec]]
    stage_graph: Mapping[TechnologyStage, List[TechnologyStage]]


class TechnologyCatalog:
    """Aggregate technologies from multiple spec providers with validation."""

    def __init__(
        self,
        providers: Iterable[SpecProvider] | None = None,
        *,
        on_duplicate: str = "error",
    ) -> None:
        self._providers: List[SpecProvider] = list(providers or [])
        self._on_duplicate = on_duplicate
        if on_duplicate not in {"error", "ignore", "override"}:
            raise ValueError("on_duplicate must be one of 'error', 'ignore', 'override'")

    def add_provider(self, provider: SpecProvider) -> None:
        self._providers.append(provider)

    def iter_specs(self) -> Iterator[TechnologySpec]:
        """Yield specs from all providers while applying duplicate policy."""
        seen: MutableMapping[str, TechnologySpec] = {}
        for provider in self._providers:
            for spec in provider.iter_specs():
                existing = seen.get(spec.name)
                if existing is None:
                    seen[spec.name] = spec
                    yield spec
                    continue
                if self._on_duplicate == "error":
                    raise ValueError(f"Duplicate technology spec '{spec.name}' detected")
                if self._on_duplicate == "override":
                    seen[spec.name] = spec
                    yield spec
                # if ignore, silently skip the new entry

    def all_specs(self) -> List[TechnologySpec]:
        specs = list(self.iter_specs())
        validate_specs(specs)
        return specs

    def register_defaults(self, registry: TechnologyRegistry | None = None) -> int:
        """Instantiate catalog specs and register them into the provided registry."""
        registry = registry or DEFAULT_TECHNOLOGY_REGISTRY
        count = 0
        for tech in instantiate_all(self.all_specs()):
            if registry.has_technology(tech.name):
                continue
            registry.register(tech)
            count += 1
        return count

    def summarise(self) -> TechnologyCatalogSummary:
        """Produce convenient groupings and inferred stage links for inspection."""
        specs = self.all_specs()
        by_stage: Dict[TechnologyStage, List[TechnologySpec]] = defaultdict(list)
        by_category: Dict[TechnologyCategory, List[TechnologySpec]] = defaultdict(list)
        by_input: Dict[str, List[TechnologySpec]] = defaultdict(list)
        by_output: Dict[str, List[TechnologySpec]] = defaultdict(list)
        stage_edges: Dict[TechnologyStage, set[TechnologyStage]] = defaultdict(set)

        specs_by_input: Dict[str, List[TechnologySpec]] = defaultdict(list)
        for spec in specs:
            stage = self._coerce_stage(spec.stage)
            category = self._coerce_category(spec.category)
            by_stage[stage].append(spec)
            by_category[category].append(spec)
            by_input[spec.commodity_in].append(spec)
            by_output[spec.commodity_out].append(spec)
            specs_by_input[spec.commodity_in].append(spec)

        # infer stage connections: if an output commodity feeds another spec's input,
        # connect the stages to hint at potential dependency wiring
        for spec in specs:
            src_stage = self._coerce_stage(spec.stage)
            downstream_specs = specs_by_input.get(spec.commodity_out, [])
            for downstream in downstream_specs:
                dst_stage = self._coerce_stage(downstream.stage)
                stage_edges[src_stage].add(dst_stage)

        return TechnologyCatalogSummary(
            specs_by_stage={stage: entries for stage, entries in by_stage.items()},
            specs_by_category={cat: entries for cat, entries in by_category.items()},
            specs_by_input={commodity: entries for commodity, entries in by_input.items()},
            specs_by_output={commodity: entries for commodity, entries in by_output.items()},
            stage_graph={stage: sorted(edges, key=lambda item: item.value) for stage, edges in stage_edges.items()},
        )

    @staticmethod
    def _coerce_stage(stage: TechnologyStage | str) -> TechnologyStage:
        return stage if isinstance(stage, TechnologyStage) else TechnologyStage(stage)

    @staticmethod
    def _coerce_category(category: TechnologyCategory | str) -> TechnologyCategory:
        return category if isinstance(category, TechnologyCategory) else TechnologyCategory(category)


DEFAULT_TECHNOLOGY_CATALOG = TechnologyCatalog([YamlPackageSpecProvider()])

__all__ = [
    "SpecProvider",
    "YamlPackageSpecProvider",
    "TechnologyCatalog",
    "TechnologyCatalogSummary",
    "DEFAULT_TECHNOLOGY_CATALOG",
]
