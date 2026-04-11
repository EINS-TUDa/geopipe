from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScenarioCaseConfig:
    project_root: Path
    scenario_file: Path
    streets_file: Path
    heat_demand_file: Path
    heating_shares_file: Path
    model_name: str
    scenario_name: str
    tss_name: str
    demand_name: str
    start_year: int
    end_year: int
    year_gap: int
    retain_existing_output_drop_per_year: float
    lockout_years: int
    apply_injections: bool
    output_plots_dir: Path
    region_builder_config_overrides: dict[str, Any] | None = None
    expected_region_ids: tuple[int, ...] | None = None


@dataclass(frozen=True)
class DijkstraScenarioConfig:
    project_root: Path
    input_dir: Path
    output_dir: Path
    buildings_file: str
    streets_file: str
    heat_demand_file: Path
    heating_shares_file: Path
    model_name: str
    scenario_name: str
    tss_name: str
    demand_name: str
    start_year: int
    end_year: int
    year_gap: int
    retain_existing_output_drop_per_year: float
    lockout_years: int
    max_demand_mwh: float
    max_street_length_km: float
    demand_share_pct: float
    polynesia: bool
    city_column: str
    output_plots_dir: Path
    region_builder_config_overrides: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScenarioRunOutput(Mapping[str, Any]):
    regions: int
    network_edges: int
    network_edges_assigned: int
    injected_demand: float
    injected_tech_count: int
    demand_name: str
    results_obj: Any
    results_raw: Any
    results_report_html: Path
    years: list[int]
    plotter: Any
    plots_dir: Path
    project_root: Path
    streets_with_region: Any
    topology_plot_polygons: Any
    region_id_column: str
    topology_plot_title: str
    topology_plot_filename: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "regions": self.regions,
            "network_edges": self.network_edges,
            "network_edges_assigned": self.network_edges_assigned,
            "injected_demand": self.injected_demand,
            "injected_tech_count": self.injected_tech_count,
            "demand_name": self.demand_name,
            "results_obj": self.results_obj,
            "results_raw": self.results_raw,
            "results_report_html": self.results_report_html,
            "years": self.years,
            "plotter": self.plotter,
            "plots_dir": self.plots_dir,
            "project_root": self.project_root,
            "streets_with_region": self.streets_with_region,
            "topology_plot_polygons": self.topology_plot_polygons,
            "region_id_column": self.region_id_column,
            "topology_plot_title": self.topology_plot_title,
            "topology_plot_filename": self.topology_plot_filename,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())
