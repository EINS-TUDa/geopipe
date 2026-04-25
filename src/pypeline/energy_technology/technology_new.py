"""New technology type hierarchy (migration step a).

Two layers per category:
- ``*Type``: catalog template loaded from YAML. Shared, reference data.
- Instance class: region-bound, extends its Type with existing_capacity,
  capacity_restriction, and profile fields.

Instance classes inherit from their Type so attribute access stays flat
(``tech.efficiency`` rather than ``tech.type.efficiency``). A user can
override any Type field per-instance by passing it to ``Type.instantiate(...)``.

Categories: Decentral, Central, CHP, Grid, Pipe. No storage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class TechnologyType:
    """Shared base for all catalog templates.

    ``existing_capacity_period`` indicates how long pre-existing capacity
    (capacity built before the modelled period) is still available. Linear
    decay from the first modelled year over this many years. ``None`` means
    "available throughout the full modelled period".
    """

    name: str
    commodity_in: str
    technical_lifetime: int
    existing_capacity_period: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("TechnologyType.name must be non-empty")
        if not self.commodity_in:
            raise ValueError(f"{self.name}: commodity_in must be non-empty")
        if self.technical_lifetime <= 0:
            raise ValueError(
                f"{self.name}: technical_lifetime must be > 0 (got {self.technical_lifetime})"
            )
        if self.existing_capacity_period is not None and self.existing_capacity_period <= 0:
            raise ValueError(
                f"{self.name}: existing_capacity_period must be > 0 if set "
                f"(got {self.existing_capacity_period})"
            )


# ---------------------------------------------------------------------------
# Conversion types (single input, single output)
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class _ConversionType(TechnologyType):
    """Shared fields for single-input / single-output conversion techs."""

    commodity_out: str
    efficiency: float = 1.0
    opex_cost_energy: float = 0.0
    opex_cost_power: float = 0.0
    capex_cost_power: float = 0.0
    capex_cost_base: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.commodity_out:
            raise ValueError(f"{self.name}: commodity_out must be non-empty")
        if self.efficiency <= 0:
            raise ValueError(f"{self.name}: efficiency must be > 0 (got {self.efficiency})")


@dataclass(kw_only=True)
class DecentralTechType(_ConversionType):
    """Catalog template for a decentralized conversion technology.

    Decentral techs sit at the demand side (e.g. per-building boilers, heat
    pumps, heat exchangers). They do not feed a grid.
    """

    def instantiate(self, **overrides: Any) -> "DecentralTech":
        return _make(DecentralTech, self, overrides)


@dataclass(kw_only=True)
class CentralTechType(_ConversionType):
    """Catalog template for a central conversion technology.

    Central techs feed into a grid (e.g. large heat pump feeding a DH grid).

    ``cap_max`` (MW) caps the capacity of a single unit. ``max_units`` caps
    the number of units per region. Both optional.
    """

    cap_max: Optional[float] = None
    max_units: Optional[int] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.cap_max is not None and self.cap_max <= 0:
            raise ValueError(
                f"{self.name}: cap_max must be > 0 if set (got {self.cap_max})"
            )
        if self.max_units is not None:
            if isinstance(self.max_units, bool) or not isinstance(self.max_units, int):
                raise TypeError(f"{self.name}: max_units must be an int if set")
            if self.max_units < 1:
                raise ValueError(
                    f"{self.name}: max_units must be >= 1 if set (got {self.max_units})"
                )

    def instantiate(self, **overrides: Any) -> "CentralTech":
        return _make(CentralTech, self, overrides)


# ---------------------------------------------------------------------------
# CHP — two outputs, does not share the _ConversionType contract
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class CHPType(TechnologyType):
    """Catalog template for a combined heat-and-power unit.

    CHP has two output commodities. ``max_efficiency_to_commodity_out_1`` is
    the maximum fraction of input energy routed to ``commodity_out_1``;
    ``min_loss`` is the minimum fraction of input energy lost as waste.
    """

    commodity_out_1: str
    commodity_out_2: str
    max_efficiency_to_commodity_out_1: float
    min_loss: float
    opex_cost_energy: float = 0.0
    opex_cost_power: float = 0.0
    capex_cost_power: float = 0.0
    capex_cost_base: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.commodity_out_1 or not self.commodity_out_2:
            raise ValueError(
                f"{self.name}: both commodity_out_1 and commodity_out_2 must be non-empty"
            )
        if self.commodity_out_1 == self.commodity_out_2:
            raise ValueError(
                f"{self.name}: commodity_out_1 and commodity_out_2 must differ"
            )
        if not (0.0 < self.max_efficiency_to_commodity_out_1 <= 1.0):
            raise ValueError(
                f"{self.name}: max_efficiency_to_commodity_out_1 must be in (0, 1] "
                f"(got {self.max_efficiency_to_commodity_out_1})"
            )
        if not (0.0 <= self.min_loss < 1.0):
            raise ValueError(
                f"{self.name}: min_loss must be in [0, 1) (got {self.min_loss})"
            )

    def instantiate(self, **overrides: Any) -> "CHP":
        return _make(CHP, self, overrides)


# ---------------------------------------------------------------------------
# Grid — intra-region distribution (e.g. DH network)
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class GridType(TechnologyType):
    """Catalog template for an intra-region distribution grid.

    A grid is scoped to one region; inter-region transport uses ``PipeType``.
    ``commodity_in`` and ``commodity_out`` typically refer to the network's
    internal transport commodity (e.g. ``district_heat_in`` /
    ``district_heat_out``).
    """

    commodity_out: str
    efficiency: float = 1.0
    capex_per_km: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.commodity_out:
            raise ValueError(f"{self.name}: commodity_out must be non-empty")
        if self.efficiency <= 0:
            raise ValueError(f"{self.name}: efficiency must be > 0 (got {self.efficiency})")
        if self.capex_per_km < 0:
            raise ValueError(
                f"{self.name}: capex_per_km must be >= 0 (got {self.capex_per_km})"
            )

    def instantiate(self, **overrides: Any) -> "Grid":
        return _make(Grid, self, overrides)


# ---------------------------------------------------------------------------
# Pipe — inter-region transport
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class PipeType(TechnologyType):
    """Catalog template for an inter-region transport pipe.

    ``loss_percent`` is the energy loss along the pipe as a fraction of
    input energy.
    """
    commodity_out: str
    loss_percent: float = 0.0
    capex_per_km: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.commodity_out:
            raise ValueError(f"{self.name}: commodity_out must be non-empty")
        if not (0.0 <= self.loss_percent < 1.0):
            raise ValueError(
                f"{self.name}: loss_percent must be in [0, 1) (got {self.loss_percent})"
            )
        if self.capex_per_km < 0:
            raise ValueError(
                f"{self.name}: capex_per_km must be >= 0 (got {self.capex_per_km})"
            )

    def instantiate(
        self,
        *,
        region_id_in: int,
        region_id_out: int,
        pipe_length_km: float,
        below_distance_threshold: bool = False,
        existing_capacity: float = 0.0,
        **overrides: Any,
    ) -> "Pipe":
        return _make(
            Pipe,
            self,
            overrides
            | {
                "region_id_in": region_id_in,
                "region_id_out": region_id_out,
                "pipe_length_km": pipe_length_km,
                "below_distance_threshold": below_distance_threshold,
                "existing_capacity": existing_capacity,
            },
        )


# ---------------------------------------------------------------------------
# Instances (region-bound)
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class DecentralTech(DecentralTechType):
    """A DecentralTechType instantiated in a specific region.

    If the owning demand has ``cooperation_of_technologies=True``, the
    RegionBuilder must copy the demand's profile and profile name onto this
    instance so constraints can be enforced.
    """

    existing_capacity: float = 0.0
    capacity_restriction: Optional[float | dict[int, float]] = None
    output_profile_name: Optional[str] = None
    output_profile: Optional[pd.Series] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_instance_fields(self)


@dataclass(kw_only=True)
class CentralTech(CentralTechType):
    """A CentralTechType instantiated in a specific region.

    Central techs can reference either an output profile (fixed dispatch)
    or an availability profile (max dispatch per hour), by name and/or by
    explicit series. Names are looked up in the data registry at build time.
    """

    existing_capacity: float = 0.0
    capacity_restriction: Optional[float | dict[int, float]] = None
    output_profile_name: Optional[str] = None
    output_profile: Optional[pd.Series] = None
    availability_profile_name: Optional[str] = None
    availability_profile: Optional[pd.Series] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_instance_fields(self)


@dataclass(kw_only=True)
class CHP(CHPType):
    """A CHPType instantiated in a specific region."""

    existing_capacity: float = 0.0
    capacity_restriction: Optional[float | dict[int, float]] = None
    output_profile_name: Optional[str] = None
    output_profile: Optional[pd.Series] = None
    availability_profile_name: Optional[str] = None
    availability_profile: Optional[pd.Series] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_instance_fields(self)


@dataclass(kw_only=True)
class Grid(GridType):
    """A GridType instantiated in a specific region."""

    existing_capacity: float = 0.0
    capacity_restriction: Optional[float | dict[int, float]] = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_instance_fields(self)


@dataclass(kw_only=True)
class Pipe(PipeType):
    """A PipeType instantiated between two regions.

    ``below_distance_threshold`` zeroes out construction cost: regions
    considered already connected pay no pipe capex.
    """

    region_id_in: int
    region_id_out: int
    pipe_length_km: float
    existing_capacity: float = 0.0
    below_distance_threshold: bool = False
    costs_eur: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        _validate_instance_fields(self)
        if self.region_id_in == self.region_id_out:
            raise ValueError(
                f"{self.name}: region_id_in and region_id_out must differ "
                f"(got {self.region_id_in})"
            )
        if self.pipe_length_km < 0:
            raise ValueError(
                f"{self.name}: pipe_length_km must be >= 0 (got {self.pipe_length_km})"
            )
        if self.below_distance_threshold:
            self.costs_eur = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make(instance_cls: type, type_obj: Any, overrides: dict[str, Any]):
    """Construct ``instance_cls`` by copying ``type_obj`` fields + overrides."""
    base = dict(asdict(type_obj))
    base.update(overrides)
    return instance_cls(**base)


def _validate_instance_fields(inst: Any) -> None:
    """Shared sanity checks for instance-level fields present on inst."""
    existing = getattr(inst, "existing_capacity", 0.0)
    if existing < 0:
        raise ValueError(
            f"{inst.name}: existing_capacity must be >= 0 (got {existing})"
        )

    restriction = getattr(inst, "capacity_restriction", None)
    if isinstance(restriction, (int, float)):
        if restriction < 0:
            raise ValueError(
                f"{inst.name}: capacity_restriction must be >= 0 (got {restriction})"
            )
    elif isinstance(restriction, dict):
        for year, cap in restriction.items():
            if not isinstance(year, int):
                raise TypeError(
                    f"{inst.name}: capacity_restriction keys must be int years"
                )
            if not isinstance(cap, (int, float)) or cap < 0:
                raise ValueError(
                    f"{inst.name}: capacity_restriction[{year}] must be a non-negative number"
                )
    elif restriction is not None:
        raise TypeError(
            f"{inst.name}: capacity_restriction must be None, number, or dict[int, float]"
        )

    out_name = getattr(inst, "output_profile_name", None)
    out_series = getattr(inst, "output_profile", None)
    if out_series is not None and not isinstance(out_series, pd.Series):
        raise TypeError(f"{inst.name}: output_profile must be a pandas Series")
    if out_name is not None and not isinstance(out_name, str):
        raise TypeError(f"{inst.name}: output_profile_name must be a str")

    av_name = getattr(inst, "availability_profile_name", None)
    av_series = getattr(inst, "availability_profile", None)
    if av_series is not None and not isinstance(av_series, pd.Series):
        raise TypeError(f"{inst.name}: availability_profile must be a pandas Series")
    if av_name is not None and not isinstance(av_name, str):
        raise TypeError(f"{inst.name}: availability_profile_name must be a str")


__all__ = [
    "TechnologyType",
    "DecentralTechType",
    "CentralTechType",
    "CHPType",
    "GridType",
    "PipeType",
    "DecentralTech",
    "CentralTech",
    "CHP",
    "Grid",
    "Pipe",
]
