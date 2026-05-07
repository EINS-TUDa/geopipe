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
from ..plot.solution_plotter import plot_grid, plot_decentral_shares

import logging

logger = logging.getLogger(__name__)


@dataclass
class Results:
    unit: Unit  # Unit in which all numeric values below are expressed (matches EnergySystem.units).

    opex: float
    capex: float
    totex: float

    emissions_by_year: pd.DataFrame  # Columns: year, amount

    decentral_technologies_per_demand: dict[str, pd.DataFrame]  # DF Columns: year, technology, region_id, capacity, energy_output, new_capacity
    central_technologies_per_commodity_out: dict[str, pd.DataFrame]  # DF Columns: year, technology, region_id, capacity, energy_output, new_capacity, installed_units
    grids_per_commodity_in: dict[str, pd.DataFrame]  # DF Columns: year, technology, region_id, capacity, energy_output, new_capacity
    pipes_per_commodity_out: dict[str, pd.DataFrame]  # DF Columns: year, technology, region_id_from, region_id_to, capacity, energy_output, new_capacity
    imports_per_commodity_out: dict[str, pd.DataFrame]  # DF Columns: year, capacity, energy_output, new_capacity

@dataclass
class Solution:
    energy_system: Optional[EnergySystem] = field(default=None)
    scenario: Optional[Scenario] = field(default=None)
    results: Optional[Results] = field(default=None)

    @property
    def file_name(self) -> str:
        return f"{self.energy_system.name}_{self.scenario.name}_Solution.pkl"

    def save(self, path: Path | str, file_name: Optional[str] = None) -> None:
        if file_name is None:
            file_name = self.file_name
        path = Path(path) / file_name
        with open(path, "wb") as f:
            pickle.dump(self, f)
        logger.info(f"Saved solution to {path}")

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

    def plot_grid(self, grid_name: str, year: int, output_path: Optional[Path | str] = None) -> Any:
        return plot_grid(self, grid_name=grid_name, year=year, output_path=output_path)

    def plot_decentral_shares(
        self,
        demand_name: str,
        year: int | list[int],
        metric: str = "energy_output",
        technology_style: Optional[dict[str, dict[str, Any]]] = None,
        output_path: Optional[Path | str] = None,
    ) -> Any:
        return plot_decentral_shares(
            self,
            demand_name=demand_name,
            year=year,
            metric=metric,
            technology_style=technology_style,
            output_path=output_path,
        )


class OptimizationBackend(ABC):
    @abstractmethod
    def solve(self, energy_system: EnergySystem, scenario: Scenario) -> Solution:
        """Run the optimization and return a Solution."""
        ...