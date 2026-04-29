from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Import:
    commodity_out: str
    price_eur_per_mwh: float | dict[int, float] | None = None
    co2_emissions_ton_per_mwh: float | dict[int, float] | None = None
    max_capacity_mw: float | dict[int, float] | None = None

    @property
    def name(self) -> str:
        return  f"Import{self.commodity_out.capitalize()}"


def load_imports_from_yaml(path: str | Path) -> list[Import]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    entries = data.get("imports", [])
    if not isinstance(entries, list):
        raise ValueError(f"imports.yaml must have a top-level 'imports' list, got {type(entries).__name__}")
    result: list[Import] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"imports[{i}] must be a mapping, got {type(entry).__name__}")
        commodity_out = entry.get("commodity_out")
        if not commodity_out:
            raise ValueError(f"imports[{i}] is missing required field 'commodity_out'")
        price_raw = entry.get("price_eur_per_mwh")
        if price_raw is None:
            raise ValueError(f"imports[{i}] ({commodity_out}) is missing required field 'price_eur_per_mwh'")
        if isinstance(price_raw, dict):
            price: float | dict[int, float] = {int(k): float(v) for k, v in price_raw.items()}
        else:
            price = float(price_raw)
        result.append(Import(
            commodity_out=str(commodity_out),
            price_eur_per_mwh=price,
            co2_emissions_ton_per_mwh=(
                {int(k): float(v) for k, v in entry["co2_emissions_ton_per_mwh"].items()}
                if isinstance(entry.get("co2_emissions_ton_per_mwh"), dict)
                else float(entry["co2_emissions_ton_per_mwh"]) if "co2_emissions_ton_per_mwh" in entry else None
            ),
            max_capacity_mw=(
                {int(k): float(v) for k, v in entry["max_capacity_mw"].items()}
                if isinstance(entry.get("max_capacity_mw"), dict)
                else float(entry["max_capacity_mw"]) if "max_capacity_mw" in entry else None
            ),
        ))
    return result
