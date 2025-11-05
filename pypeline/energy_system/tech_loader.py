from __future__ import annotations
from pathlib import Path
from typing import Dict, List

import yaml

from pypeline.energy_system.technology import Technology
from pypeline.energy_system.technology_spec import (
    TechnologySpec,
    validate_spec_dict,
    validate_specs,
)
from pypeline.energy_system.technology_stage import TechnologyStage, TechnologyCategory


def _spec_from_dict(data: Dict) -> TechnologySpec:
    validate_spec_dict(data)
    stage = data.get("stage")
    category = data.get("category")
    return TechnologySpec(
        name=data["name"],
        commodity_in=data["commodity_in"],
        commodity_out=data["commodity_out"],
        efficiency=float(data.get("efficiency", 1.0)),
        technical_lifetime=int(data.get("technical_lifetime", 30)),
        opex_cost_energy=float(data.get("opex_cost_energy", 0.0)),
        opex_cost_power=float(data.get("opex_cost_power", 0.0)),
        capex_cost_power=float(data.get("capex_cost_power", 0.0)),
        stage=TechnologyStage(stage) if stage else TechnologyStage.STAGE1,
        category=TechnologyCategory(category) if category else TechnologyCategory.DEMAND_LINK,
    )


def _load_specs_from_yaml(path: Path) -> List[TechnologySpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not raw:
        return []
    entries = raw
    if isinstance(raw, dict):
        entries = raw.get("technologies") or list(raw.values())
    specs: List[TechnologySpec] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        try:
            specs.append(_spec_from_dict(item))
        except Exception:
            continue
    validate_specs(specs)
    return specs

def load_specs_from_package() -> List[TechnologySpec]:
    base = Path(__file__).resolve().parent
    data_dir = base / "configs"
    if not data_dir.exists():
        return []
    yaml_path = data_dir / "technologies.yaml"
    if not yaml_path.exists():
        return []
    return _load_specs_from_yaml(yaml_path)

def instantiate(spec: TechnologySpec) -> Technology:
    return spec.to_technology()

def instantiate_all(specs: List[TechnologySpec]) -> List[Technology]:
    return [instantiate(s) for s in specs]
