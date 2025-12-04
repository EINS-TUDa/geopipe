from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List
import yaml

from .technology import Technology
from .technology_spec import (
    TechnologySpec,
    validate_spec_dict,
    validate_specs,
)
from .technology_stage import TechnologyStage, TechnologyCategory

_PREFIX_INFERENCES: tuple[tuple[str, Dict[str, Any]], ...] = (
    ("ind_", {"stage": TechnologyStage.STAGE1.value, "commodity_out": "residential_heat"}),
    ("cen_", {"stage": TechnologyStage.STAGE3.value, "commodity_out": "district_heat_in"}),
    ("heat_pipe", {"stage": TechnologyStage.STAGE2.value, "category": TechnologyCategory.GRID.value}),
)

_TOKEN_COMMODITY_MAP: Dict[str, str] = {
    "hydrogen": "hydrogen",
    "h2": "hydrogen",
    "gas": "gas",
    "biogas": "gas",
    "natural_gas": "gas",
    "lng": "gas",
    "cng": "gas",
    "petroleum": "gas",
    "oil": "oil",
    "diesel": "oil",
    "electric": "electricity",
    "electricity": "electricity",
    "biomass": "biomass",
    "wood": "biomass",
    "pellet": "biomass",
    "pellets": "biomass",
    "chip": "biomass",
    "chips": "biomass",
}

class _SpecInference:
    def __init__(self, prefix_inferences: tuple[tuple[str, Dict[str, Any]], ...]):
        self._prefix_inferences = prefix_inferences

    def apply(self, item: Dict[str, Any], provided_keys: set[str] | None = None) -> Dict[str, Any]:
        name = item.get("name")
        if not isinstance(name, str):
            return item
        lowered = name.lower()
        parts = [segment for segment in lowered.split("_") if segment]
        self._apply_prefix_defaults(item, lowered, parts, provided_keys)
        if (not provided_keys or "commodity_in" not in provided_keys) and "commodity_in" not in item:
            if lowered.startswith("ind_") and "_district_" in lowered:
                item["commodity_in"] = "district_heat_out"
        if not provided_keys or "category" not in provided_keys:
            if lowered.startswith("ind_") and "_connection" in lowered:
                item["category"] = TechnologyCategory.DEMAND_LINK.value
        if not provided_keys or "category" not in provided_keys:
            if "boiler" in parts:
                item["category"] = TechnologyCategory.SUPPLY.value
        self._apply_boiler_inference(item, parts, provided_keys)
        self._apply_heat_pump_inference(item, lowered, provided_keys)
        self._apply_grid_inference(item, lowered, parts, provided_keys)
        self._apply_pipe_inference(item, parts, lowered, provided_keys)
        self._apply_heat_exchanger_inference(item, lowered, provided_keys)
        self._apply_commodity_pair_inference(item, provided_keys)
        return item

    def _apply_prefix_defaults(
        self,
        item: Dict[str, Any],
        lowered: str,
        parts: List[str],
        provided_keys: set[str] | None,
    ) -> None:
        for prefix, defaults in self._prefix_inferences:
            if lowered.startswith(prefix):
                for key, value in defaults.items():
                    if provided_keys and key in provided_keys:
                        continue
                    item[key] = value
                break

    def _apply_boiler_inference(
        self,
        item: Dict[str, Any],
        parts: List[str],
        provided_keys: set[str] | None,
    ) -> None:
        if (not provided_keys or "commodity_in" not in provided_keys) and "commodity_in" not in item:
            fuel = self._infer_boiler_commodity(parts)
            if fuel:
                item["commodity_in"] = fuel
        if (not provided_keys or "commodity_in" not in provided_keys) and "commodity_in" not in item:
            for token in parts:
                commodity = _TOKEN_COMMODITY_MAP.get(token)
                if commodity:
                    item["commodity_in"] = commodity
                    break

    def _apply_heat_pump_inference(
        self,
        item: Dict[str, Any],
        lowered: str,
        provided_keys: set[str] | None,
    ) -> None:
        if not provided_keys or "category" not in provided_keys:
            if "heatpump" in lowered or "heat_pump" in lowered:
                item["category"] = TechnologyCategory.SUPPLY.value
        if (not provided_keys or "commodity_in" not in provided_keys) and "commodity_in" not in item:
            if "heatpump" in lowered or "heat_pump" in lowered:
                item["commodity_in"] = "electricity"

    def _apply_grid_inference(
        self,
        item: Dict[str, Any],
        lowered: str,
        parts: List[str],
        provided_keys: set[str] | None,
    ) -> None:
        name = item.get("name")
        if not isinstance(name, str):
            return
        tokens = [part for part in lowered.replace("-", "_").split("_") if part]
        base: str | None = None
        if lowered.startswith("grid_") and len(lowered) > 5:
            base = lowered[5:]
        elif lowered.endswith("_grid") and len(lowered) > 5:
            base = lowered[:-5]
        elif "heat_grid" in lowered or "grid_heat" in lowered:
            base = "heat"
        elif "grid" in tokens:
            for idx, token in enumerate(tokens):
                if token != "grid":
                    continue
                if idx > 0:
                    base = tokens[idx - 1]
                    break
                if idx + 1 < len(tokens):
                    base = tokens[idx + 1]
                    break
        is_grid_like = base is not None or ("grid" in tokens and len(tokens) > 1)
        if not is_grid_like:
            return
        if base is not None:
            cin_default, cout_default = _grid_defaults_for_base(base)
        else:
            cin_default, cout_default = None, None
        if not provided_keys or "category" not in provided_keys:
            item["category"] = TechnologyCategory.GRID.value
        if not provided_keys or "stage" not in provided_keys:
            item["stage"] = TechnologyStage.STAGE2.value
        if cout_default is not None:
            if (not provided_keys or "commodity_out" not in provided_keys) and "commodity_out" not in item:
                item["commodity_out"] = cout_default
        if cin_default is not None:
            if (not provided_keys or "commodity_in" not in provided_keys) and "commodity_in" not in item:
                item["commodity_in"] = cin_default

    def _apply_pipe_inference(
        self,
        item: Dict[str, Any],
        parts: List[str],
        lowered: str,
        provided_keys: set[str] | None,
    ) -> None:
        is_pipe = "pipe" in parts or lowered.endswith("pipe")
        if not is_pipe:
            return
        if not provided_keys or "category" not in provided_keys:
            item["category"] = TechnologyCategory.GRID.value
        if not provided_keys or "stage" not in provided_keys:
            item["stage"] = TechnologyStage.STAGE2.value
        if (not provided_keys or "commodity_in" not in provided_keys) and "commodity_in" not in item:
            if "heat" in parts or "district" in parts:
                item["commodity_in"] = "district_heat_out"
        if (not provided_keys or "commodity_out" not in provided_keys) and "commodity_out" not in item:
            if "heat" in parts or "district" in parts:
                item["commodity_out"] = "district_heat_in"

    def _apply_heat_exchanger_inference(
        self,
        item: Dict[str, Any],
        lowered: str,
        provided_keys: set[str] | None,
    ) -> None:
        if "heat_exchanger" not in lowered and not lowered.startswith("ind_district_heating_connection"):
            return
        if not provided_keys or "stage" not in provided_keys:
            item["stage"] = TechnologyStage.STAGE1.value
        if not provided_keys or "category" not in provided_keys:
            item["category"] = TechnologyCategory.DEMAND_LINK.value
        if (not provided_keys or "commodity_in" not in provided_keys) and "commodity_in" not in item:
            item["commodity_in"] = "district_heat_out"
        if (not provided_keys or "commodity_out" not in provided_keys) and "commodity_out" not in item:
            item["commodity_out"] = "residential_heat"

    def _apply_commodity_pair_inference(
        self,
        item: Dict[str, Any],
        provided_keys: set[str] | None,
    ) -> None:
        cin = item.get("commodity_in")
        cout = item.get("commodity_out")
        if isinstance(cin, str) and isinstance(cout, str):
            if cin.startswith("district_heat_out") and cout.startswith("district_heat_in"):
                category_value = item.get("category")
                stage_value = item.get("stage")
                if not provided_keys or "category" not in provided_keys:
                    if category_value is None:
                        item["category"] = TechnologyCategory.GRID.value
                if not provided_keys or "stage" not in provided_keys:
                    if stage_value is None:
                        item["stage"] = TechnologyStage.STAGE2.value

    @staticmethod
    def _infer_boiler_commodity(parts: List[str]) -> str | None:
        if not parts:
            return None
        prefixes = {"ind", "cen", "grid"}
        for idx, token in enumerate(parts):
            if token != "boiler":
                continue
            for pos in range(idx - 1, -1, -1):
                candidate = parts[pos]
                if candidate and candidate not in prefixes:
                    return candidate
        return None


_SPEC_INFERENCE = _SpecInference(_PREFIX_INFERENCES)

@dataclass(frozen=True)
class _DefaultsBundle:
    base: Dict[str, Any]
    categories: Dict[str, Dict[str, Any]]
    stages: Dict[str, Dict[str, Any]]

    def merge(self, item: Dict[str, Any]) -> Dict[str, Any]:
        payload: Dict[str, Any] = dict(self.base)
        candidate_category = item.get("category", payload.get("category"))
        if candidate_category and candidate_category in self.categories:
            payload.update(self.categories[candidate_category])
        candidate_stage = item.get("stage") or payload.get("stage")
        if candidate_stage and candidate_stage in self.stages:
            payload.update(self.stages[candidate_stage])
        payload.update(item)
        return payload

    def apply_stage_and_category_defaults(
        self,
        item: Dict[str, Any],
        provided_keys: set[str] | None,
    ) -> Dict[str, Any]:
        def _should_assign(key: str) -> bool:
            if provided_keys and key in provided_keys:
                return False
            if key in item and item[key] is not None:
                return False
            return True

        stage_value = item.get("stage")
        if stage_value is not None:
            stage_key = stage_value.value if isinstance(stage_value, TechnologyStage) else str(stage_value)
            stage_defaults = self.stages.get(stage_key)
            if stage_defaults:
                for key, value in stage_defaults.items():
                    if _should_assign(key):
                        item[key] = value

        category_value = item.get("category")
        if category_value is not None:
            category_key = category_value.value if isinstance(category_value, TechnologyCategory) else str(category_value)
            category_defaults = self.categories.get(category_key)
            if category_defaults:
                for key, value in category_defaults.items():
                    if _should_assign(key):
                        item[key] = value
        return item


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
        capex_cost_base=float(data.get("capex_cost_base", 0.0)),
        cap_min=float(data["cap_min"]) if data.get("cap_min") is not None else None,
        cap_max=float(data["cap_max"]) if data.get("cap_max") is not None else None,
        max_units=int(data["max_units"]) if data.get("max_units") is not None else None,
        stage=TechnologyStage(stage) if stage else TechnologyStage.STAGE1,
        category=TechnologyCategory(category) if category else TechnologyCategory.DEMAND_LINK,
    )
def _grid_defaults_for_base(base: str) -> tuple[str | None, str | None]:
    if base == "heat":
        return "district_heat_in", "district_heat_out"
    return None, base
def _load_specs_from_yaml(path: Path, defaults: _DefaultsBundle | None = None) -> List[TechnologySpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not raw:
        return []
    entries: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        entries = [dict(item) for item in raw if isinstance(item, dict)]
    elif isinstance(raw, dict):
        tech_list = raw.get("technologies") if isinstance(raw.get("technologies"), list) else None
        if tech_list:
            entries.extend(dict(item) for item in tech_list if isinstance(item, dict))
        for name, value in raw.items():
            if name == "technologies":
                continue
            if not isinstance(value, dict):
                continue
            entry = dict(value)
            entry.setdefault("name", name)
            entries.append(entry)
    specs: List[TechnologySpec] = []
    for idx, item in enumerate(entries):
        if not isinstance(item, dict):
            continue
        provided_keys = set(item.keys())
        try:
            payload = defaults.merge(item) if defaults is not None else dict(item)
            payload = _SPEC_INFERENCE.apply(payload, provided_keys)
            if defaults is not None:
                payload = defaults.apply_stage_and_category_defaults(payload, provided_keys)
            specs.append(_spec_from_dict(payload))
        except Exception as exc:
            name = item.get("name") if isinstance(item.get("name"), str) else None
            ident = name or f"<entry {idx}>"
            raise ValueError(f"Failed to load technology '{ident}' from {path}: {exc}") from exc
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
