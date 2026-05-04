# coding: utf-8
import pickle
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import pandas as pd

from ..energy_system import EnergySystem, Scenario
from ..energy_system.units import Unit
from .reporting import write_html_report
from ..plot.solution_plotter import plot_grid


@dataclass
class Results:
    unit: Unit  # Unit in which all numeric values below are expressed (matches EnergySystem.units).

    opex: float
    capex: float
    totex: float

    emissions_by_year: pd.DataFrame  # Columns: year, amount

    active_capacities_decentral_technologies_per_demand: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id, capacity
    yearly_energy_outputs_decentral_technologies_per_demand: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id, energy_output
    new_capacities_decentral_technologies_per_demand: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id, new_capacity

    active_capacities_central_technologies_per_commodity_out: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id, capacity
    yearly_energy_outputs_central_technologies_per_commodity_out: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id, energy_output
    new_capacities_central_technologies_per_commodity_out: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id, new_capacity

    active_capacities_grids_per_commodity_in: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id, capacity
    yearly_energy_outputs_grids_per_commodity_in: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id, energy_output
    new_capacities_grids_per_commodity_in: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id, new_capacity

    active_capacities_pipes_per_commodity_out: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id_from, region_id_to, capacity
    yearly_energy_outputs_pipes_per_commodity_out: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id_from, region_id_to, energy_output
    new_capacities_pipes_per_commodity_out: dict[str, pd.DataFrame]  #DF Columns: year, technology, region_id_from, region_id_to, new_capacity


@dataclass
class Solution:
    energy_system: Optional[EnergySystem] = field(default=None)
    scenario: Optional[Scenario] = field(default=None)
    results: Optional[Results] = field(default=None)

    @property
    def file_name(self) -> str:
        return f"{self.energy_system.name}_{self.scenario.name}.pkl"

    def save(self, path: Path | str, file_name: Optional[str] = None) -> None:
        if file_name is None:
            file_name = self.file_name
        path = Path(path) / file_name
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: Path | str, file_name: str) -> "Solution":
        path = Path(path) / file_name
        with open(path, "rb") as f:
            return pickle.load(f)

    def write_html_report(
        self,
        output_path: str | Path,
    ) -> Path:
        return write_html_report(
            self,
            output_path=Path(output_path)
        )

    def plot_grid(self, grid_name: str, year: int) -> Any:
        return plot_grid(self, grid_name=grid_name, year=year)


class OptimizationBackend(ABC):
    @abstractmethod
    def solve(self, energy_system: EnergySystem, scenario: Scenario) -> Solution:
        """Run the optimization and return a Solution."""
        ...