from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Imports:
    commodity_out: str
    price_eur_per_mwh: float
    co2_emissions_ton_per_mwh: float = 0.0
    max_capacity_mw: float | None = None

    @property
    def name(self) -> str:
        return  f"{self.commodity_out.capitalize()}Supply"


def load_imports_from_yaml(path: str | Path) -> list[Imports]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    entries = data.get("imports", [])
    if not isinstance(entries, list):
        raise ValueError(f"imports.yaml must have a top-level 'imports' list, got {type(entries).__name__}")
    result: list[Imports] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"imports[{i}] must be a mapping, got {type(entry).__name__}")
        commodity_out = entry.get("commodity_out")
        if not commodity_out:
            raise ValueError(f"imports[{i}] is missing required field 'commodity_out'")
        price = entry.get("price_eur_per_mwh")
        if price is None:
            raise ValueError(f"imports[{i}] ({commodity_out}) is missing required field 'price_eur_per_mwh'")
        result.append(Imports(
            commodity_out=str(commodity_out),
            price_eur_per_mwh=float(price),
            co2_emissions_ton_per_mwh=float(entry.get("co2_emissions_ton_per_mwh", 0.0)),
            max_capacity_mw=float(entry["max_capacity_mw"]) if "max_capacity_mw" in entry else None,
        ))
    return result
