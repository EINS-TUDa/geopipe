from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

from pypeline.energy_system.technology import Technology
from pypeline.energy_system.technology_spec import (
    TechnologySpec,
    validate_spec_dict,
    validate_specs,
)
from pypeline.energy_system.technology_stage import TechnologyStage, TechnologyCategory


@dataclass(frozen=True)
class _DefaultsBundle:
    base: Dict[str, Any]
    categories: Dict[str, Dict[str, Any]]
    stages: Dict[str, Dict[str, Any]]


def _load_defaults_from_yaml(path: Path) -> _DefaultsBundle:
    if not path.exists():
        return _DefaultsBundle({}, {}, {})
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not raw or not isinstance(raw, dict):
        return _DefaultsBundle({}, {}, {})
    base = dict(raw.get("defaults") or {})
    categories = {
        str(name): dict(values)
        for name, values in (raw.get("categories") or {}).items()
        if isinstance(values, dict)
    }
    stages = {
        str(name): dict(values)
        for name, values in (raw.get("stages") or {}).items()
        if isinstance(values, dict)
    }
    if not base and "defaults" not in raw:
        base = dict(raw)
    return _DefaultsBundle(base, categories, stages)


def _collect_additional_paths(extra_paths: Iterable[str | Path] | None) -> List[Path]:
    seen: set[Path] = set()
    resolved: List[Path] = []
    candidates: List[str | Path] = []
    if extra_paths:
        candidates.extend(extra_paths)
    env_value = os.getenv("PYPELINE_TECH_SPECS")
    if env_value:
        candidates.extend(filter(None, env_value.split(os.pathsep)))
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if not path.exists():
            continue
        if path in seen:
            continue
        seen.add(path)
        resolved.append(path)
    return resolved


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


def _apply_defaults(
    item: Dict[str, Any],
    bundle: _DefaultsBundle,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = dict(bundle.base)
    candidate_category = item.get("category", payload.get("category"))
    if candidate_category and candidate_category in bundle.categories:
        payload.update(bundle.categories[candidate_category])
    candidate_stage = item.get("stage") or payload.get("stage")
    if candidate_stage and candidate_stage in bundle.stages:
        payload.update(bundle.stages[candidate_stage])
    payload.update(item)
    return payload


def _load_specs_from_yaml(path: Path, defaults: _DefaultsBundle | None = None) -> List[TechnologySpec]:
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
            payload = (
                _apply_defaults(item, defaults)
                if defaults is not None
                else dict(item)
            )
            specs.append(_spec_from_dict(payload))
        except Exception:
            continue
    validate_specs(specs)
    return specs

def load_specs_from_package(extra_spec_files: Iterable[str | Path] | None = None) -> List[TechnologySpec]:
    base = Path(__file__).resolve().parent
    data_dir = base / "configs"
    if not data_dir.exists():
        return []
    defaults = _load_defaults_from_yaml(data_dir / "defaults.yaml")
    yaml_path = data_dir / "technologies.yaml"
    specs: List[TechnologySpec] = []
    if yaml_path.exists():
        specs.extend(_load_specs_from_yaml(yaml_path, defaults))
    for extra_path in _collect_additional_paths(extra_spec_files):
        specs.extend(_load_specs_from_yaml(extra_path, defaults))
    return specs

def instantiate(spec: TechnologySpec) -> Technology:
    return spec.to_technology()

def instantiate_all(specs: List[TechnologySpec]) -> List[Technology]:
    return [instantiate(s) for s in specs]
