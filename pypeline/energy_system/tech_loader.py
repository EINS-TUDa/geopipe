from __future__ import annotations
from pathlib import Path
import json
from typing import List

from pypeline.energy_system.technology import Technology
from pypeline.energy_system.technology_spec import TechnologySpec, validate_spec_dict, validate_specs

def _load_spec(path: Path) -> TechnologySpec:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_spec_dict(data)
    return TechnologySpec(
        name=data["name"],
        commodity_in=data["commodity_in"],
        commodity_out=data["commodity_out"],
        efficiency=float(data.get("efficiency", 1.0)),
        technical_lifetime=int(data.get("technical_lifetime", 30)),
        opex_cost_energy=float(data.get("opex_cost_energy", 0.0)),
        opex_cost_power=float(data.get("opex_cost_power", 0.0)),
        capex_cost_power=float(data.get("capex_cost_power", 0.0)),
    )

def load_specs_from_dir(directory: Path) -> List[TechnologySpec]:
    directory = Path(directory)
    if not directory.exists() or not directory.is_dir():
        return []
    specs: List[TechnologySpec] = []
    for p in sorted(directory.glob("*.json")):
        try:
            specs.append(_load_spec(p))
        except Exception:
            continue
    validate_specs(specs)
    return specs

def load_specs_from_package() -> List[TechnologySpec]:
    base = Path(__file__).resolve().parent
    data_dir = base / "technologies"
    if not data_dir.exists():
        return []
    preferred_order = [
        "ind_heat_pump",
        "ind_gas_boiler",
        "ind_oil_boiler",
        "ind_district_heating_connection",
        "cen_heat_pump",
        "heat_grid",
        "grid_electricity",
    ]
    files = {p.stem: p for p in data_dir.glob("*.json")}
    specs: List[TechnologySpec] = []
    for name in preferred_order:
        p = files.pop(name, None)
        if p is not None:
            try:
                specs.append(_load_spec(p))
            except Exception:
                continue
    for p in sorted(files.values()):
        try:
            specs.append(_load_spec(p))
        except Exception:
            continue
    validate_specs(specs)
    return specs

def instantiate(spec: TechnologySpec) -> Technology:
    return Technology(
        name=spec.name,
        commodity_in=spec.commodity_in,
        commodity_out=spec.commodity_out,
        efficiency=spec.efficiency,
        technical_lifetime=spec.technical_lifetime,
        opex_cost_energy=spec.opex_cost_energy,
        opex_cost_power=spec.opex_cost_power,
        capex_cost_power=spec.capex_cost_power,
    )

def instantiate_all(specs: List[TechnologySpec]) -> List[Technology]:
    return [instantiate(s) for s in specs]
