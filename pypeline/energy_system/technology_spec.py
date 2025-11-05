from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pypeline.energy_system.technology import (
	Technology,
	TechnologyStage,
	TechnologyCategory,
	DEFAULT_STAGE,
	DEFAULT_CATEGORY,
)


REQUIRED_SPEC_FIELDS: tuple[str, ...] = (
	"name",
	"commodity_in",
	"commodity_out",
)



@dataclass(slots=True)
class TechnologySpec:
	"""Declarative specification for a Technology loaded from static data.

	This acts as the canonical data container. Conversion to runtime Technology
	objects happens via "to_technology", keeping the data layer and runtime
	behavior loosely coupled.
	"""

	name: str
	commodity_in: str
	commodity_out: str
	efficiency: float = 1.0
	technical_lifetime: int = 30
	opex_cost_energy: float = 0.0
	opex_cost_power: float = 0.0
	capex_cost_power: float = 0.0
	stage: TechnologyStage | str = DEFAULT_STAGE
	category: TechnologyCategory | str = DEFAULT_CATEGORY

	def to_dict(self) -> dict[str, Any]:
		return {
			"name": self.name,
			"commodity_in": self.commodity_in,
			"commodity_out": self.commodity_out,
			"efficiency": self.efficiency,
			"technical_lifetime": self.technical_lifetime,
			"opex_cost_energy": self.opex_cost_energy,
			"opex_cost_power": self.opex_cost_power,
			"capex_cost_power": self.capex_cost_power,
			"stage": self.stage.value if isinstance(self.stage, TechnologyStage) else str(self.stage),
			"category": self.category.value if isinstance(self.category, TechnologyCategory) else str(self.category),
		}

	def to_technology(self) -> Technology:
		return Technology(
			name=self.name,
			commodity_in=self.commodity_in,
			commodity_out=self.commodity_out,
			efficiency=self.efficiency,
			technical_lifetime=self.technical_lifetime,
			opex_cost_energy=self.opex_cost_energy,
			opex_cost_power=self.opex_cost_power,
			capex_cost_power=self.capex_cost_power,
			stage=self.stage,
			category=self.category,
		)


def validate_spec_dict(data: dict[str, Any]) -> None:
	missing = [field for field in REQUIRED_SPEC_FIELDS if field not in data]
	if missing:
		raise ValueError(f"Missing required technology spec fields: {missing} in {data}")

	if not isinstance(data.get("name"), str):
		raise TypeError("TechnologySpec.name must be str")
	for attr in ("commodity_in", "commodity_out"):
		if not isinstance(data.get(attr), str):
			raise TypeError(f"TechnologySpec.{attr} must be str")

	numeric_fields = (
		"efficiency",
		"technical_lifetime",
		"opex_cost_energy",
		"opex_cost_power",
		"capex_cost_power",
	)
	for attr in numeric_fields:
		if attr in data and not isinstance(data[attr], (int, float)):
			raise TypeError(f"TechnologySpec.{attr} must be numeric if provided")

	if "stage" in data and data["stage"] is not None:
		try:
			TechnologyStage(data["stage"])
		except Exception as exc:
			raise ValueError(f"Invalid technology stage '{data['stage']}'") from exc

	if "category" in data and data["category"] is not None:
		try:
			TechnologyCategory(data["category"])
		except Exception as exc:
			raise ValueError(f"Invalid technology category '{data['category']}'") from exc


def validate_specs(specs: Iterable[TechnologySpec]) -> None:
	seen: set[str] = set()
	for spec in specs:
		if spec.name in seen:
			raise ValueError(f"Duplicate TechnologySpec name detected: {spec.name}")
		seen.add(spec.name)
		if spec.efficiency <= 0:
			raise ValueError(f"Efficiency must be > 0 for {spec.name}")
		if spec.technical_lifetime <= 0:
			raise ValueError(f"technical_lifetime must be > 0 for {spec.name}")


__all__ = [
	"TechnologySpec",
	"validate_spec_dict",
	"validate_specs",
]
