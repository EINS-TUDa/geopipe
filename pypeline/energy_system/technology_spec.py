from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(slots=True)
class TechnologySpec:
    """Declarative specification for a Technology loaded from static data (e.g. JSON).

    This is the canonical source for default technologies. Conversion to a runtime
    `Technology` instance is handled by a factory to decouple pure data from behavior.
    """
    name: str
    commodity_in: str
    commodity_out: str
    efficiency: float = 1.0
    technical_lifetime: int = 30
    opex_cost_energy: float = 0.0
    opex_cost_power: float = 0.0
    capex_cost_power: float = 0.0

    def to_dict(self) -> dict[str, Any]:  # convenience for debugging / exporting
        return {
            "name": self.name,
            "commodity_in": self.commodity_in,
            "commodity_out": self.commodity_out,
            "efficiency": self.efficiency,
            "technical_lifetime": self.technical_lifetime,
            "opex_cost_energy": self.opex_cost_energy,
            "opex_cost_power": self.opex_cost_power,
            "capex_cost_power": self.capex_cost_power,
        }


REQUIRED_SPEC_FIELDS: tuple[str, ...] = (
    "name",
    "commodity_in",
    "commodity_out",
)


def validate_spec_dict(d: dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_SPEC_FIELDS if k not in d]
    if missing:
        raise ValueError(f"Missing required technology spec fields: {missing} in {d}")
    # Basic type shape checks (lightweight)
    if not isinstance(d.get("name"), str):
        raise TypeError("TechnologySpec.name must be str")
    for f in ("commodity_in", "commodity_out"):
        if not isinstance(d.get(f), str):
            raise TypeError(f"TechnologySpec.{f} must be str")
    # Numeric optional fields if present
    for nf in ("efficiency", "technical_lifetime", "opex_cost_energy", "opex_cost_power", "capex_cost_power"):
        if nf in d and not isinstance(d[nf], (int, float)):
            raise TypeError(f"TechnologySpec.{nf} must be numeric if provided")


def validate_specs(specs: Iterable[TechnologySpec]) -> None:
    seen: set[str] = set()
    for s in specs:
        if s.name in seen:
            raise ValueError(f"Duplicate TechnologySpec name detected: {s.name}")
        seen.add(s.name)
        if s.efficiency <= 0:
            raise ValueError(f"Efficiency must be > 0 for {s.name}")
        if s.technical_lifetime <= 0:
            raise ValueError(f"technical_lifetime must be > 0 for {s.name}")