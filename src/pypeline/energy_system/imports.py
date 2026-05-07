from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Import:
    commodity_out: str
    price_eur_per_mwh: float | dict[int, float] | None = None
    co2_emissions_ton_per_mwh: float | dict[int, float] | None = None
    max_cap_per_year: float | dict[int, float] | None = None
    max_energy_out_per_year: float | dict[int, float] | None = None

    @property
    def name(self) -> str:
        return  f"Import{self.commodity_out.capitalize()}"


def _parse_year_dep(raw: object, field_name: str, commodity_out: str) -> float | dict[int, float] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        parsed = {int(k): float(v) for k, v in raw.items()}
        for year in parsed:
            if year > 100:
                raise ValueError(
                    f"imports ({commodity_out}).{field_name}: year {year} > {100}. "
                    f"Years must be relative to the model start year."
                )
        return parsed
    return float(raw)


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
        if entry.get("price_eur_per_mwh") is None:
            raise ValueError(f"imports[{i}] ({commodity_out}) is missing required field 'price_eur_per_mwh'")
        result.append(Import(
            commodity_out=str(commodity_out),
            price_eur_per_mwh=_parse_year_dep(entry["price_eur_per_mwh"], "price_eur_per_mwh", commodity_out),
            co2_emissions_ton_per_mwh=_parse_year_dep(entry.get("co2_emissions_ton_per_mwh"), "co2_emissions_ton_per_mwh", commodity_out),
            max_cap_per_year=_parse_year_dep(entry.get("max_cap_per_year"), "max_cap_per_year", commodity_out),
            max_energy_out_per_year=_parse_year_dep(entry.get("max_energy_out_per_year"), "max_energy_out_per_year", commodity_out),
        ))
    return result