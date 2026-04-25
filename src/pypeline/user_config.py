"""User configuration for the new technology-resolution flow (migration step a).

Holds user-supplied knobs that steer existing-capacity derivation,
connected-groups computation, and DH-grid fallback behavior. Decoupled
from the technology catalog itself: the catalog says *what exists*, the
user config says *how to use it for this study*.

Two-stage validation:
- ``__post_init__``: intra-config checks (value ranges, types)
- ``validate_against_catalog(catalog)``: cross-reference technology names
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from pypeline.energy_technology.technology_new import (
    CentralTechType,
    CHPType,
    DecentralTechType,
    GridType,
)
from pypeline.energy_technology.technology_registry_new import TechnologyRegistry


@dataclass
class DemandConfig:
    """Per-demand configuration.

    ``default_supply_technology`` is the fallback supplier used when no
    technology_shares data is available for a region. If both are missing,
    ``RegionBuilder`` raises.
    """

    default_supply_technology: str | None = None

    def __post_init__(self) -> None:
        if self.default_supply_technology is not None and not isinstance(
            self.default_supply_technology, str
        ):
            raise TypeError(
                "DemandConfig.default_supply_technology must be a str or None"
            )


@dataclass
class GridConfig:
    """Per-grid-type configuration.

    ``default_central_technology`` is required if the region has any
    existing DH decentral capacity (i.e. heat exchangers with share
    > minimum_dhn_share). Its ``commodity_out`` must match the grid's
    ``commodity_in``.

    ``considered_connected_distance_m`` groups regions whose centroids
    (or nearest network points, depending on topology) sit within this
    distance; the group then shares one central-capacity location.

    ``grid_expansion_headroom_pct`` scales the derived grid+pipe capacity
    so the optimization can expand flow without paying new-build capex
    (e.g. 0.15 = allow 15% more flow than the shortest-path derivation
    gave).

    ``preferred_central_location_region_ids`` is a list of region IDs the
    user marks as preferred siting for central capacity. At build time,
    for each connected group: if exactly one preferred region is in it,
    that one wins; multiple preferred in one group raises; none falls
    back to the highest-demand region.

    ``minimum_dhn_share`` is the global threshold below which a region's
    heat-exchanger share is treated as zero (grid dropped, other shares
    renormalized, region excluded from connected-groups input).
    ``minimum_dhn_share_by_region`` overrides per region.
    """

    default_central_technology: str | None = None
    considered_connected_distance_m: float = 0.0
    grid_expansion_headroom_pct: float = 0.0
    preferred_central_location_region_ids: list[int] = field(default_factory=list)
    minimum_dhn_share: float = 0.0
    minimum_dhn_share_by_region: dict[int, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.default_central_technology is not None and not isinstance(
            self.default_central_technology, str
        ):
            raise TypeError("GridConfig.default_central_technology must be a str or None")
        if self.considered_connected_distance_m < 0:
            raise ValueError(
                f"GridConfig.considered_connected_distance_m must be >= 0 "
                f"(got {self.considered_connected_distance_m})"
            )
        if not (0.0 <= self.grid_expansion_headroom_pct <= 10.0):
            raise ValueError(
                f"GridConfig.grid_expansion_headroom_pct must be in [0, 10] "
                f"(got {self.grid_expansion_headroom_pct})"
            )
        if not (0.0 <= self.minimum_dhn_share <= 1.0):
            raise ValueError(
                f"GridConfig.minimum_dhn_share must be in [0, 1] "
                f"(got {self.minimum_dhn_share})"
            )
        seen: set[int] = set()
        for rid in self.preferred_central_location_region_ids:
            if not isinstance(rid, int) or isinstance(rid, bool):
                raise TypeError(
                    f"GridConfig.preferred_central_location_region_ids: ids must be int (got {rid!r})"
                )
            if rid in seen:
                raise ValueError(
                    f"GridConfig.preferred_central_location_region_ids: duplicate id {rid}"
                )
            seen.add(rid)
        for rid, share in self.minimum_dhn_share_by_region.items():
            if not isinstance(rid, int) or isinstance(rid, bool):
                raise TypeError(
                    f"GridConfig.minimum_dhn_share_by_region: keys must be int region ids (got {rid!r})"
                )
            if not (0.0 <= share <= 1.0):
                raise ValueError(
                    f"GridConfig.minimum_dhn_share_by_region[{rid}] must be in [0, 1] (got {share})"
                )

    def minimum_share_for(self, region_id: int) -> float:
        """Per-region minimum share, falling back to the global default."""
        return self.minimum_dhn_share_by_region.get(region_id, self.minimum_dhn_share)

    def preferred_location_in_group(self, region_ids: set[int]) -> int | None:
        """Return the preferred region within a connected group, if exactly one.

        Raises if multiple preferred regions fall in the same group. Returns
        ``None`` if none do — caller should fall back to highest-demand.
        """
        matches = [r for r in self.preferred_central_location_region_ids if r in region_ids]
        if len(matches) > 1:
            raise ValueError(
                f"GridConfig: multiple preferred central-location regions "
                f"{matches} fall in the same connected group {sorted(region_ids)}"
            )
        return matches[0] if matches else None


@dataclass
class UserConfig:
    """Top-level user config. Loaded once per study."""

    demands: dict[str, DemandConfig] = field(default_factory=dict)
    grids: dict[str, GridConfig] = field(default_factory=dict)

    def demand(self, demand_type: str) -> DemandConfig:
        """Return the config for ``demand_type``, defaulting to empty."""
        return self.demands.get(demand_type, DemandConfig())

    def grid(self, grid_name: str) -> GridConfig:
        """Return the config for ``grid_name``, defaulting to empty."""
        return self.grids.get(grid_name, GridConfig())

    def validate_against_registry(self, registry: TechnologyRegistry) -> None:
        """Cross-check every technology name referenced in the config.

        Catches typos in ``default_supply_technology`` /
        ``default_central_technology``, and enforces that a grid's
        ``default_central_technology`` actually outputs the grid's
        ``commodity_in``.
        """
        for demand_type, cfg in self.demands.items():
            tech_name = cfg.default_supply_technology
            if tech_name is None:
                continue
            if tech_name not in registry:
                raise ValueError(
                    f"demands['{demand_type}'].default_supply_technology "
                    f"references unknown technology '{tech_name}'"
                )
            tech = registry[tech_name]
            if not isinstance(tech, (DecentralTechType, CHPType)):
                raise ValueError(
                    f"demands['{demand_type}'].default_supply_technology "
                    f"must be a decentralized or CHP technology, "
                    f"got {type(tech).__name__} for '{tech_name}'"
                )

        for grid_name, cfg in self.grids.items():
            if grid_name not in registry.grids:
                raise ValueError(
                    f"grids['{grid_name}'] is not a GridType in the registry "
                    f"(known grids: {sorted(registry.grids)})"
                )
            grid_type: GridType = registry.grids[grid_name]

            tech_name = cfg.default_central_technology
            if tech_name is None:
                continue
            if tech_name not in registry:
                raise ValueError(
                    f"grids['{grid_name}'].default_central_technology "
                    f"references unknown technology '{tech_name}'"
                )
            tech = registry[tech_name]
            if not isinstance(tech, (CentralTechType, CHPType)):
                raise ValueError(
                    f"grids['{grid_name}'].default_central_technology "
                    f"must be a central or CHP technology, "
                    f"got {type(tech).__name__} for '{tech_name}'"
                )
            produced = _produced_commodities(tech)
            if grid_type.commodity_in not in produced:
                raise ValueError(
                    f"grids['{grid_name}'].default_central_technology '{tech_name}' "
                    f"outputs {sorted(produced)} but grid expects "
                    f"commodity_in='{grid_type.commodity_in}'"
                )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_user_config(path: str | Path) -> UserConfig:
    """Read a user config YAML file into a ``UserConfig``.

    Strict: unknown top-level keys and unknown per-section fields raise.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"User config not found: {file_path}")

    raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    if raw is None:
        return UserConfig()
    if not isinstance(raw, dict):
        raise ValueError(
            f"{file_path}: top-level YAML must be a mapping, got {type(raw).__name__}"
        )

    allowed_top = {"demands", "grids"}
    unknown = set(raw) - allowed_top
    if unknown:
        raise ValueError(
            f"{file_path}: unknown top-level key(s) {sorted(unknown)}. "
            f"Allowed: {sorted(allowed_top)}"
        )

    demands_raw = raw.get("demands") or {}
    grids_raw = raw.get("grids") or {}
    if not isinstance(demands_raw, dict):
        raise ValueError(
            f"{file_path}: 'demands' must be a mapping, got {type(demands_raw).__name__}"
        )
    if not isinstance(grids_raw, dict):
        raise ValueError(
            f"{file_path}: 'grids' must be a mapping, got {type(grids_raw).__name__}"
        )

    demands = {
        name: _build_from_mapping(DemandConfig, payload, f"demands.{name}", file_path)
        for name, payload in demands_raw.items()
    }
    grids = {
        name: _build_grid_config(payload, f"grids.{name}", file_path)
        for name, payload in grids_raw.items()
    }

    return UserConfig(demands=demands, grids=grids)


def _build_from_mapping(
    cls: type,
    payload: Any,
    path_key: str,
    file_path: Path,
) -> Any:
    if payload is None:
        return cls()
    if not isinstance(payload, dict):
        raise ValueError(
            f"{file_path}: '{path_key}' must be a mapping, got {type(payload).__name__}"
        )
    allowed = {f.name for f in fields(cls)}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(
            f"{file_path}: '{path_key}' has unknown field(s) {sorted(unknown)}. "
            f"Allowed: {sorted(allowed)}"
        )
    try:
        return cls(**payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{file_path}: failed to build {cls.__name__} at '{path_key}': {exc}"
        ) from exc


def _build_grid_config(payload: Any, path_key: str, file_path: Path) -> GridConfig:
    """Grid needs extra coercion for the region-id-keyed dict."""
    if payload is None:
        return GridConfig()
    if not isinstance(payload, dict):
        raise ValueError(
            f"{file_path}: '{path_key}' must be a mapping, got {type(payload).__name__}"
        )
    normalized: dict[str, Any] = dict(payload)
    by_region = normalized.get("minimum_dhn_share_by_region")
    if by_region is not None:
        if not isinstance(by_region, dict):
            raise ValueError(
                f"{file_path}: '{path_key}.minimum_dhn_share_by_region' must be a mapping"
            )
        try:
            normalized["minimum_dhn_share_by_region"] = {
                int(k): float(v) for k, v in by_region.items()
            }
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{file_path}: '{path_key}.minimum_dhn_share_by_region' keys must be int "
                f"and values must be numeric ({exc})"
            ) from exc
    return _build_from_mapping(GridConfig, normalized, path_key, file_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _produced_commodities(tech: Any) -> set[str]:
    """Return the set of commodity_out values a tech produces (handles CHP's two)."""
    if isinstance(tech, CHPType):
        return {tech.commodity_out_1, tech.commodity_out_2}
    return {getattr(tech, "commodity_out", None)} - {None}


__all__ = [
    "DemandConfig",
    "GridConfig",
    "UserConfig",
    "load_user_config",
]
