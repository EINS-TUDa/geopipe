from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator
from ._year_dep import YearDep



class Import(BaseModel):
    commodity_out: str
    price_eur_per_mwh: float | dict[int, float]
    co2_emissions_ton_per_mwh: YearDep = None
    max_cap_per_year: YearDep = None
    max_energy_out_per_year: YearDep = None

    @field_validator("price_eur_per_mwh","co2_emissions_ton_per_mwh", "max_cap_per_year",
        "max_energy_out_per_year")
    @classmethod
    def _years_relative(cls, v: YearDep) -> YearDep:
        if isinstance(v, dict):
            bad = [y for y in v if y > 100]
            if bad:
                raise ValueError(
                    f"year {bad[0]} > 100; years must be relative to the model start year"
                )
        return v

    @property
    def name(self) -> str:
        return f"Import{self.commodity_out.capitalize()}"

class Export(BaseModel):
    commodity_in: str
    price_eur_per_mwh: float | dict[int, float]
    co2_emissions_ton_per_mwh: YearDep = None
    max_cap_per_year: YearDep = None
    max_energy_in_per_year: YearDep = None

    @field_validator("price_eur_per_mwh","co2_emissions_ton_per_mwh", "max_cap_per_year",
        "max_energy_in_per_year")
    @classmethod
    def _years_relative(cls, v: YearDep) -> YearDep:
        if isinstance(v, dict):
            bad = [y for y in v if y > 100]
            if bad:
                raise ValueError(
                    f"year {bad[0]} > 100; years must be relative to the model start year"
                )
        return v

    @property
    def name(self) -> str:
        return f"Export{self.commodity_in.capitalize()}"


class _ImportsFile(BaseModel):
    imports: list[Import] = []

class _ExportsFile(BaseModel):
    exports: list[Export] = []

def load_imports_exports_from_yaml(path: str | Path) -> tuple[list[Import], list[Export]]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _ImportsFile.model_validate(data).imports, _ExportsFile.model_validate(data).exports