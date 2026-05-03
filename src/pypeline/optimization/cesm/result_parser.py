# coding: utf-8
import logging
import sqlite3
from dataclasses import field, dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from pypeline.energy_system import EnergySystem, Scenario
from pypeline.optimization.cesm.units import scale_factors
from pypeline.optimization.solver import Results, Solution

logger = logging.getLogger(__name__)

@dataclass
class CESMSolution(Solution):
    db_path: Optional[Path] = field(default=None)


class CESMResultsParser:
    """Parse a CESM SQLite output DB into a :class:`CESMSolution`."""

    def __init__(self, db_path: Path | str, energy_system: EnergySystem, scenario: Scenario):
        self.db_path = Path(db_path)
        self.energy_system = energy_system
        self.scenario = scenario

    def parse(self) -> CESMSolution:
        with sqlite3.connect(self.db_path) as con:
            output_by_name_year = self._fetch_subprocess_outputs(con)
            opex, capex, totex = self._fetch_global_totals(con)
            emissions_df = self._fetch_emissions(con)

        unit = self.energy_system.units
        factors = scale_factors(unit)
        for year_map in output_by_name_year.values():
            for vals in year_map.values():
                vals["cap_active"] /= factors["power"]
                vals["cap_new"] /= factors["power"]
                vals["eouttot"] /= factors["energy"]
        opex /= factors["money"]
        capex /= factors["money"]
        totex /= factors["money"]
        if not emissions_df.empty:
            emissions_df = emissions_df.assign(amount=emissions_df["amount"] / factors["co2_emissions"])

        decentral_active, decentral_energy, decentral_new = self._build_decentral(output_by_name_year)
        central_active, central_energy, central_new = self._build_central(output_by_name_year)
        grids_active, grids_energy, grids_new = self._build_grids(output_by_name_year)
        pipes_active, pipes_energy, pipes_new = self._build_pipes(output_by_name_year)

        results = Results(
            unit=unit,
            opex=opex,
            capex=capex,
            totex=totex,
            emissions_by_year=emissions_df,
            active_capacities_decentral_technologies_per_demand=decentral_active,
            yearly_energy_outputs_decentral_technologies_per_demand=decentral_energy,
            new_capacities_decentral_technologies_per_demand=decentral_new,
            active_capacities_central_technologies_per_commodity_out=central_active,
            yearly_energy_outputs_central_technologies_per_commodity_out=central_energy,
            new_capacities_central_technologies_per_commodity_out=central_new,
            active_capacities_grids_per_commodity_in=grids_active,
            yearly_energy_outputs_grids_per_commodity_in=grids_energy,
            new_capacities_grids_per_commodity_in=grids_new,
            active_capacities_pipes_per_commodity_out=pipes_active,
            yearly_energy_outputs_pipes_per_commodity_out=pipes_energy,
            new_capacities_pipes_per_commodity_out=pipes_new,
        )

        return CESMSolution(
            energy_system=self.energy_system,
            scenario=self.scenario,
            results=results,
            db_path=self.db_path,
        )

    @staticmethod
    def _fetch_subprocess_outputs(con: sqlite3.Connection) -> dict[str, dict[int, dict[str, float]]]:
        """Return ``{conversion_process_name: {year: {cap_active, cap_new, eouttot}}}``."""
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        if not {"output_cs_y", "conversion_subprocess", "conversion_process", "year"}.issubset(tables):
            return {}
        cur.execute(
            """
            SELECT cp.name,
                   y.value AS year,
                   COALESCE(SUM(o.cap_active), 0.0) AS cap_active,
                   COALESCE(SUM(o.cap_new), 0.0) AS cap_new,
                   COALESCE(SUM(o.eouttot), 0.0) AS eouttot
            FROM output_cs_y AS o
            JOIN conversion_subprocess AS cs ON cs.id = o.cs_id
            JOIN conversion_process AS cp ON cp.id = cs.cp_id
            JOIN year AS y ON y.id = o.y_id
            GROUP BY cp.name, y.value
            """
        )
        result: dict[str, dict[int, dict[str, float]]] = {}
        for name, year, cap_active, cap_new, eouttot in cur.fetchall():
            result.setdefault(str(name), {})[int(year)] = {
                "cap_active": cap_active,
                "cap_new": cap_new,
                "eouttot": eouttot,
            }
        return result

    @staticmethod
    def _fetch_global_totals(con: sqlite3.Connection) -> tuple[float, float, float]:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        if "output_global" not in tables:
            return 0.0, 0.0, 0.0
        cur.execute("SELECT OPEX, CAPEX, TOTEX FROM output_global LIMIT 1")
        row = cur.fetchone()
        if row is None:
            return 0.0, 0.0, 0.0
        return row[0], row[1], row[2]

    @staticmethod
    def _fetch_emissions(con: sqlite3.Connection) -> pd.DataFrame:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cur.fetchall()}
        if "output_y" not in tables or "year" not in tables:
            return pd.DataFrame(columns=["year", "amount"])
        cur.execute(
            """
            SELECT y.value AS year, SUM(o.total_annual_co2_emission) AS amount
            FROM output_y AS o
            JOIN year AS y ON y.id = o.y_id
            GROUP BY y.value
            ORDER BY y.value
            """
        )
        rows = [(int(year), amount) for year, amount in cur.fetchall()]
        return pd.DataFrame(rows, columns=["year", "amount"])

    def _rows_for(
        self, output_by_name_year: dict[str, dict[int, dict[str, float]]], cesm_name: str
    ) -> dict[int, dict[str, float]]:
        return output_by_name_year.get(cesm_name, {})

    def _build_decentral(
        self, output_by_name_year: dict[str, dict[int, dict[str, float]]]
    ) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
        active_rows: dict[str, list[dict[str, Any]]] = {}
        energy_rows: dict[str, list[dict[str, Any]]] = {}
        new_rows: dict[str, list[dict[str, Any]]] = {}

        for region in self.energy_system.regions:
            demand_by_commodity_in: dict[str, str] = {
                d.demand_type.commodity_in: d.name for d in region.demands
            }
            for tech in region.decentral_techs:
                demand_name = demand_by_commodity_in.get(tech.commodity_out)
                if demand_name is None:
                    logger.debug(
                        "Decentral tech '%s' (commodity_out=%s) in region %s has no matching demand; skipping",
                        tech.name, tech.commodity_out, region.id,
                    )
                    continue
                cesm_name = f"{tech.name}_D{region.id}"
                for year, vals in self._rows_for(output_by_name_year, cesm_name).items():
                    active_rows.setdefault(demand_name, []).append(
                        {"year": year, "technology": tech.name, "region_id": region.id, "capacity": vals["cap_active"]}
                    )
                    energy_rows.setdefault(demand_name, []).append(
                        {"year": year, "technology": tech.name, "region_id": region.id, "energy_output": vals["eouttot"]}
                    )
                    new_rows.setdefault(demand_name, []).append(
                        {"year": year, "technology": tech.name, "region_id": region.id, "new_capacity": vals["cap_new"]}
                    )

        return (
            _frames(active_rows, ["year", "technology", "region_id", "capacity"]),
            _frames(energy_rows, ["year", "technology", "region_id", "energy_output"]),
            _frames(new_rows, ["year", "technology", "region_id", "new_capacity"]),
        )

    def _build_central(
        self, output_by_name_year: dict[str, dict[int, dict[str, float]]]
    ) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
        active_rows: dict[str, list[dict[str, Any]]] = {}
        energy_rows: dict[str, list[dict[str, Any]]] = {}
        new_rows: dict[str, list[dict[str, Any]]] = {}

        for region in self.energy_system.regions:
            for tech in region.central_techs:
                key = tech.commodity_out
                cesm_name = f"{tech.name}_D{region.id}"
                for year, vals in self._rows_for(output_by_name_year, cesm_name).items():
                    active_rows.setdefault(key, []).append(
                        {"year": year, "technology": tech.name, "region_id": region.id, "capacity": vals["cap_active"]}
                    )
                    energy_rows.setdefault(key, []).append(
                        {"year": year, "technology": tech.name, "region_id": region.id, "energy_output": vals["eouttot"]}
                    )
                    new_rows.setdefault(key, []).append(
                        {"year": year, "technology": tech.name, "region_id": region.id, "new_capacity": vals["cap_new"]}
                    )

        return (
            _frames(active_rows, ["year", "technology", "region_id", "capacity"]),
            _frames(energy_rows, ["year", "technology", "region_id", "energy_output"]),
            _frames(new_rows, ["year", "technology", "region_id", "new_capacity"]),
        )

    def _build_grids(
        self, output_by_name_year: dict[str, dict[int, dict[str, float]]]
    ) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
        active_rows: dict[str, list[dict[str, Any]]] = {}
        energy_rows: dict[str, list[dict[str, Any]]] = {}
        new_rows: dict[str, list[dict[str, Any]]] = {}

        for region in self.energy_system.regions:
            for grid in region.grids:
                key = grid.commodity_in
                cesm_name = f"{grid.name}_D{region.id}"
                for year, vals in self._rows_for(output_by_name_year, cesm_name).items():
                    active_rows.setdefault(key, []).append(
                        {"year": year, "technology": grid.name, "region_id": region.id, "capacity": vals["cap_active"]}
                    )
                    energy_rows.setdefault(key, []).append(
                        {"year": year, "technology": grid.name, "region_id": region.id, "energy_output": vals["eouttot"]}
                    )
                    new_rows.setdefault(key, []).append(
                        {"year": year, "technology": grid.name, "region_id": region.id, "new_capacity": vals["cap_new"]}
                    )

        return (
            _frames(active_rows, ["year", "technology", "region_id", "capacity"]),
            _frames(energy_rows, ["year", "technology", "region_id", "energy_output"]),
            _frames(new_rows, ["year", "technology", "region_id", "new_capacity"]),
        )

    def _build_pipes(
        self, output_by_name_year: dict[str, dict[int, dict[str, float]]]
    ) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
        active_rows: dict[str, list[dict[str, Any]]] = {}
        energy_rows: dict[str, list[dict[str, Any]]] = {}
        new_rows: dict[str, list[dict[str, Any]]] = {}

        for pipe in self.energy_system.pipes:
            key = pipe.commodity_out
            cesm_name = f"{pipe.name}_D{pipe.region_id_in}_D{pipe.region_id_out}"
            for year, vals in self._rows_for(output_by_name_year, cesm_name).items():
                base = {
                    "year": year,
                    "technology": pipe.name,
                    "region_id_from": pipe.region_id_in,
                    "region_id_to": pipe.region_id_out,
                }
                active_rows.setdefault(key, []).append({**base, "capacity": vals["cap_active"]})
                energy_rows.setdefault(key, []).append({**base, "energy_output": vals["eouttot"]})
                new_rows.setdefault(key, []).append({**base, "new_capacity": vals["cap_new"]})

        return (
            _frames(active_rows, ["year", "technology", "region_id_from", "region_id_to", "capacity"]),
            _frames(energy_rows, ["year", "technology", "region_id_from", "region_id_to", "energy_output"]),
            _frames(new_rows, ["year", "technology", "region_id_from", "region_id_to", "new_capacity"]),
        )


def _frames(
    rows_by_key: dict[str, list[dict[str, Any]]],
    columns: list[str],
) -> dict[str, pd.DataFrame]:
    return {
        key: pd.DataFrame(rows, columns=columns).sort_values(["year", "technology"]).reset_index(drop=True)
        for key, rows in rows_by_key.items()
    }