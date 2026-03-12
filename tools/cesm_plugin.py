"""Unified CESM plugin. Functions are specific to use with CESM.b

Public API:
    - CESMBackend (Optimization Model adapter)
    - write_cesm_inputs_from_energy_system
"""
from __future__ import annotations
import argparse, json, math, sqlite3, subprocess, sys, logging, re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import pandas as pd
import numpy as np
import geopandas as gpd 

from pypeline.optimization.solver import OptimizationModel, Solution
from pypeline.optimization.om_adapter import build_om_from_es
from pypeline.validation import (ensure_tss_indices, normalize_profile_for_tss,
    require_finite, require_float,require_positive_int, sanitize_price_map, to_int_id)
from pypeline.energy_technology.technology import (Technology,
    extract_district_id_from_name as _extract_district_id_from_name,
    split_base_and_district as _split_base_and_district,
    is_central_heat_supply as _is_central_heat_supply,
)
from pypeline.energy_system.energy_system import EnergySystem, HEAT_EXCHANGER_NAMES
from pypeline.energy_system.scenario import Scenario
from pypeline.energy_system.dhn import (
    build_district_heat_grid_from_polygons,
    build_inter_dhn_pipes_from_street_segments,
)
from pypeline.energy_system.io_utils import (_canon_co, _convsubproc_dataframe, _load_numeric_txt,
    commodity_config_from_energy_system as _commodity_config_from_energy_system,
    resolve_retain_schedule as _resolve_retain_schedule,
    polygons_from_energy_system as _polygons_from_energy_system,
    _write_demand_profile, _write_techmap_workbook,
    _units_df, _scenario_df, _tss_df,)
from pypeline.energy_technology.technology_registry import (TechnologyRegistry, get_default_technology_registry)

NUMERIC_ERRORS = (TypeError, ValueError)
PathLike = Union[str, Path]
logger = logging.getLogger(__name__)
_require_float = require_float
_require_finite = require_finite
_require_positive_int = require_positive_int

UNBOUNDED_CAP = 1e9
UNBOUNDED_ENERGY = 1e12
@dataclass
class _CesmIOPaths:
    techmap_dir: Path
    timeseries_dir: Path
    xlsx_path: Path
    tss_file: Path

def _prepare_io_paths(workdir: Path, model_name: str, tss_name: str) -> _CesmIOPaths:
    techmap_dir = workdir / "Data" / "Techmap"
    timeseries_dir = workdir / "Data" / "TimeSeries"
    techmap_dir.mkdir(parents=True, exist_ok=True)
    timeseries_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = techmap_dir / f"{model_name}.xlsx"
    tss_file = timeseries_dir / f"{tss_name}.txt"
    return _CesmIOPaths(techmap_dir=techmap_dir, timeseries_dir=timeseries_dir, xlsx_path=xlsx_path, tss_file=tss_file)

def _log_techmap_stats(*, commodity_count: int, convproc_count: int, convsubproc_rows: int) -> None:
    logger.info(
        "Techmap stats: commodities=%d | conversion_processes=%d | conversion_subprocess_rows=%d",
        commodity_count,
        convproc_count,
        convsubproc_rows,
    )

HEAT_EXCHANGER_BASE_NAMES: tuple[str, ...] = HEAT_EXCHANGER_NAMES

class _ConversionRowsBuilder:
    def __init__(
        self,
        *,
        scenario_name: str,
        scenario_years: List[int],
        demand_commodity: str,
        districts: List[int],
        district_index: dict[int, int],
        heat_names: List[str],
        district_heat_in_names: dict[int, str],
        district_heat_out_names: dict[int, str],
        min_dhn_targets: dict[int, float],
        min_heat_grid_targets: dict[int, float],
        min_central_cap_targets: dict[str, float],
        min_central_cap_totals: dict[int, float],
        min_central_cap_totals_by_co: dict[str, dict[int, float]],
        district_to_region: dict[int, int],
        region_metrics: dict[str, Any],
        total_metrics: dict[str, dict[str, float]],
        retain_factor: float | None,
        retain_years_factor: float | None,
        retain_schedule: Optional[List[float]],
        lockout_years: int,
        elec_price_eur_per_mwh: float,
        local_dhn_capex_base_by_district: Optional[dict[int, float]] = None,
    ) -> None:
        self.scenario_name = scenario_name
        self.scenario_years = scenario_years
        self.demand_commodity = demand_commodity
        self.districts = districts
        self.district_index = district_index
        self.heat_names = heat_names
        self.district_heat_in_names = district_heat_in_names
        self.district_heat_out_names = district_heat_out_names
        self.min_dhn_targets = min_dhn_targets
        self.min_heat_grid_targets = min_heat_grid_targets
        self.min_central_cap_targets = min_central_cap_targets
        self.min_central_cap_totals = min_central_cap_totals
        self.min_central_cap_totals_by_co = min_central_cap_totals_by_co
        self.district_to_region = district_to_region
        self.region_metrics = region_metrics
        self.total_metrics = total_metrics
        self.retain_factor = retain_factor
        self.retain_years_factor = retain_years_factor
        self.retain_schedule = retain_schedule
        self.local_dhn_capex_base_by_district = local_dhn_capex_base_by_district or {}
        self.lockout_years = max(0, int(lockout_years))
        self.lockout_until_year = (
            self.scenario_years[0] + self.lockout_years if self.scenario_years else None
        )
        self.elec_price_eur_per_mwh = elec_price_eur_per_mwh
        self._tech_builders = {
            "ind_heat_pump": self._rows_residential,
            "ind_gas_boiler": self._rows_residential,
            "ind_oil_boiler": self._rows_residential,
            "heat_exchanger": self._rows_heat_exchanger,
            "ind_district_heating_connection": self._rows_heat_exchanger,
            "heat_grid": self._rows_default,
            "cen_heat_pump": self._rows_central_heat_supply,
            "cen_gas_boiler": self._rows_central_heat_supply,
            "cen_oil_boiler": self._rows_central_heat_supply,
            "grid_electricity": self._rows_grid_electricity,
        }

    # Public API -------------------------------------------------------------
    def build_rows(self, technologies: List[Technology]) -> List[dict[str, Any]]:
        self._allocate_min_central_caps(technologies)
        rows: List[dict[str, Any]] = []
        for tech in technologies:
            rows.extend(self.rows_for_technology(tech))
        return rows

    def _allocate_min_central_caps(self, technologies: List[Technology]) -> None:
        if not self.min_central_cap_totals and not self.min_central_cap_totals_by_co:
            return
        techs_by_district: dict[int, List[Technology]] = {}
        for tech in technologies:
            if not _is_central_heat_supply(tech.name):
                continue
            district = _extract_district_id_from_name(tech.name)
            if district is None:
                raise ValueError("Central heat supply technology is missing district suffix while min_central_cap_totals are set: "f"{tech.name}")
            techs_by_district.setdefault(district, []).append(tech)

        def _tech_commodity(t: Technology) -> str:
            return str(t.commodity_in).strip().lower()

        def _allocate_for_bucket(district: int, target: float, predicate: Callable[[Technology], bool]) -> None:
            techs = [t for t in techs_by_district.get(district, []) if predicate(t)]
            if not techs:
                raise ValueError(f"No central heat supply technologies found for district {district} matching constraint; target={target}")

            def _cp_key(t: Technology) -> str:
                if _extract_district_id_from_name(t.name) is not None:
                    return t.name
                if len(self.districts) == 1:
                    return t.name
                return f"{t.name}_D{district}"

            def _cap_max(tech: Technology) -> float:
                return _require_finite(f"cap_max for {tech.name}", tech.cap_max, gt_zero=True)

            def _max_units(tech: Technology) -> float:
                return float(_require_positive_int(f"max_units for {tech.name}", tech.max_units))

            def _total_cap(tech: Technology) -> float:
                cap = _cap_max(tech)
                units = _max_units(tech)
                return cap * units

            def _capex(tech: Technology) -> float:
                return _require_finite(f"capex_cost_power for {tech.name}", tech.capex_cost_power, allow_zero=True)

            # Sort cheapest-first; allocate across techs so total meets the district target without relying on a single unbounded tech swallowing the entire floor.
            sorted_techs = sorted(techs, key=lambda t: (_capex(t), _cap_max(t)))
            remaining = float(target)
            finite_bucket: list[tuple[Technology, float]] = []
            unbounded: list[Technology] = []
            for tech in sorted_techs:
                if tech.name in self.min_central_cap_targets:
                    continue
                cap = _total_cap(tech)
                if math.isinf(cap):
                    unbounded.append(tech)
                else:
                    finite_bucket.append((tech, cap))

            # Allocate to finite caps first.
            for tech, cap in finite_bucket:
                if remaining <= 1e-9:
                    break
                take = min(remaining, cap)
                if take > 0:
                    self.min_central_cap_targets[_cp_key(tech)] = take
                    remaining -= take

            if remaining > 1e-9 and unbounded:
                self.min_central_cap_targets[_cp_key(unbounded[0])] = remaining

        # First, allocate commodity-specific targets
        for commodity, per_district in self.min_central_cap_totals_by_co.items():
            for district, target in per_district.items():
                _allocate_for_bucket(district, target, lambda t, c=commodity: _tech_commodity(t) == c)

        # Then, allocate generic totals
        for district, target in self.min_central_cap_totals.items():
            _allocate_for_bucket(district, target, lambda t: True)

    def rows_for_technology(self, tech: Technology) -> List[dict[str, Any]]:
        builder = self._tech_builders.get(tech.name)
        if builder is None:
            base_name, _ = _split_base_and_district(tech.name)
            builder = self._tech_builders.get(base_name)
        if builder is None:
            if _canon_co(tech.commodity_out) == _canon_co(self.demand_commodity):
                builder = self._rows_residential
            else:
                builder = self._rows_default
        return builder(tech)

    # Core helpers -----------------------------------------------------------
    def _rows_residential(self, tech: Technology) -> List[dict[str, Any]]:
        rows: List[dict[str, Any]] = []
        tech_district = _extract_district_id_from_name(tech.name)
        if tech_district is not None and tech_district not in self.districts:
            return rows

        target_districts: List[int]
        if tech_district is not None:
            target_districts = [tech_district]
        elif len(self.districts) == 1:
            target_districts = [self.districts[0]]
        else:
            if not self.districts:
                raise ValueError(f"No districts configured for residential technology {tech.name}")
            target_districts = list(self.districts)

        for district in target_districts:
            overrides = self._min_eout_override(tech, district)
            heat_comm = self._heat_comm_for_district(district)
            cp_name = tech.name if tech_district is not None or len(self.districts) == 1 else f"{tech.name}_D{district}"
            rows.append(
                self._build_cs_row(
                    cp_name=cp_name,
                    cin=tech.commodity_in,
                    cout=heat_comm,
                    tech=tech,
                    overrides=overrides,
                    district=district,
                )
            )
        return rows

    def _rows_heat_exchanger(self, tech: Technology) -> List[dict[str, Any]]:
        rows: List[dict[str, Any]] = []
        target_district = _extract_district_id_from_name(tech.name)

        def _district_iter() -> List[int]:
            if target_district is not None:
                return [target_district]
            if not self.districts:
                raise ValueError(f"No districts configured for heat exchanger technology {tech.name}")
            return self.districts

        for district in _district_iter():
            if district not in self.district_index:
                raise ValueError(f"District {district} is not present in district_index for heat exchanger {tech.name}")
            overrides = self._min_eout_override(tech, district)
            heat_comm = self._heat_comm_for_district(district)
            cin = tech.commodity_in
            if target_district is not None:
                cin = self.district_heat_out_names.get(district, tech.commodity_in)
            cp_name = "HeatExchanger" if len(self.districts) == 1 else f"HeatExchanger_D{district}"
            rows.append(
                self._build_cs_row(
                    cp_name=cp_name,
                    cin=cin,
                    cout=heat_comm,
                    tech=tech,
                    overrides=overrides,
                    district=district,
                )
            )
        return rows

    def _rows_central_heat_supply(self, tech: Technology) -> List[dict[str, Any]]:
        rows: List[dict[str, Any]] = []
        tech_district = _extract_district_id_from_name(tech.name)
        if tech_district is not None and tech_district not in self.districts:
            return rows

        if tech_district is not None:
            target_districts = [tech_district]
        elif len(self.districts) == 1:
            target_districts = [self.districts[0]]
        else:
            target_districts = list(self.districts)

        for district in target_districts:
            cout = tech.commodity_out
            if cout is None or "_D" not in str(cout):
                cout = self.district_heat_in_names.get(district, cout or "")
            cp_name = tech.name if tech_district is not None or len(self.districts) == 1 else f"{tech.name}_D{district}"
            rows.append(
                self._build_cs_row(
                    cp_name=cp_name,
                    cin=tech.commodity_in,
                    cout=cout,
                    tech=tech,
                    overrides=None,
                    district=district,
                )
            )
        return rows

    def _rows_default(self, tech: Technology) -> List[dict[str, Any]]:
        base_name, tech_district = _split_base_and_district(tech.name)
        target_district: Optional[int] = None
        if base_name == "heat_grid":
            if tech_district is not None:
                target_district = tech_district
            elif len(self.districts) == 1:
                target_district = self.districts[0]

        overrides = self._min_eout_override(tech, target_district)
        if base_name == "heat_grid" and target_district is not None:
            local_capex_base = self.local_dhn_capex_base_by_district.get(target_district)
            if local_capex_base is not None and local_capex_base > 0.0:
                overrides = dict(overrides or {})
                overrides["capex_cost_base"] = float(local_capex_base)
        return [
            self._build_cs_row(
                cp_name=f"{tech.name}",
                cin=tech.commodity_in,
                cout=tech.commodity_out,
                tech=tech,
                overrides=overrides,
                district=target_district,
            )
        ]

    def _rows_grid_electricity(self, tech: Technology) -> List[dict[str, Any]]:
        overrides: dict[str, Any] = {}
        min_override = self._min_eout_override(tech, None)
        if min_override:
            overrides.update(min_override)
        overrides.setdefault("opex_cost_energy", float(self.elec_price_eur_per_mwh))
        return [
            self._build_cs_row(
                cp_name=f"{tech.name}",
                cin=tech.commodity_in,
                cout=tech.commodity_out,
                tech=tech,
                overrides=overrides,
                district=None,
            )
        ]

    # Row construction -------------------------------------------------------
    def _build_cs_row(
        self,
        *,
        cp_name: str,
        cin: str,
        cout: str,
        tech: Technology,
        overrides: Optional[dict[str, Any]] = None,
        district: Optional[int] = None,
    ) -> dict[str, Any]:
        row = tech_to_cesms_row(
            tech,
            cp_name=cp_name,
            cin=cin,
            cout=cout,
            scenario_name=self.scenario_name,
        )
        self._postprocess_cs_row(row=row, tech=tech, district=district, overrides=overrides)
        return row

    def _postprocess_cs_row(
        self,
        *,
        row: dict[str, Any],
        tech: Technology,
        district: Optional[int],
        overrides: Optional[dict[str, Any]],
    ) -> None:
        base_name, _ = _split_base_and_district(tech.name)
        if base_name == "heat_grid":
            row["max_units"] = 1
            row["cap_min"] = 0.0
        metrics = self._existing_metrics(tech, district)
        existing = self._existing_overrides(tech, district, metrics)
        merged = self._merge_overrides(existing, overrides)
        if merged:
            row.update({k: v for k, v in merged.items() if v is not None})
        self._apply_min_central_cap(row, tech)
        existing_cap = self._existing_capacity(metrics)
        self._ensure_unit_capacity(row, existing_cap)
        self._limit_first_year_capacity(row, existing_cap, tech, metrics=metrics)
        self._clamp_reserves_to_cap_max(row)

    def _apply_min_central_cap(self, row: dict[str, Any], tech: Technology) -> None:
        target = self.min_central_cap_targets.get(row.get("conversion_process_name"))
        if target is None:
            return
        target_total = float(target)

        cap_max_peak = self._profile_peak(row.get("cap_max"))
        max_units_raw = row.get("max_units")
        if max_units_raw is None:
            raise ValueError("max_units missing for central capacity application")
        max_units_val = _require_float("max_units", max_units_raw)
        if max_units_val < 1.0:
            raise ValueError("max_units must be >= 1")

        if cap_max_peak is None or not math.isfinite(cap_max_peak) or cap_max_peak <= 0.0:
            raise ValueError("cap_max must be positive and finite for central capacity application")

        enforce_min = target_total

        required_units = int(math.ceil(enforce_min / cap_max_peak))
        if required_units > max_units_val:
            raise ValueError(
                f"insufficient max_units for {tech.name}: need {required_units} to meet min central cap {enforce_min},"
                f" but got {max_units_val} (cap_max {cap_max_peak})"
            )

        # Keep per-unit minimum no larger than the unit size to avoid build_min_activation infeasibility.
        per_unit_min = enforce_min
        if cap_max_peak and cap_max_peak > 0.0:
            per_unit_min = min(enforce_min, cap_max_peak)

        if self.scenario_years and self.lockout_until_year is not None:
            cap_min_existing_map = self._profile_to_map(row.get("cap_min"))
            cap_res_min_existing_map = self._profile_to_map(row.get("cap_res_min"))

            cap_min_pairs: list[tuple[int, float]] = []
            cap_res_min_pairs: list[tuple[int, float]] = []

            for year in self.scenario_years:
                active = year >= self.lockout_until_year
                cap_min_floor = per_unit_min if active else 0.0
                cap_res_min_floor = 0.0

                cap_min_pairs.append((year, max(float(cap_min_existing_map.get(year, 0.0) or 0.0), cap_min_floor)))
                cap_res_min_pairs.append((year, max(float(cap_res_min_existing_map.get(year, 0.0) or 0.0), cap_res_min_floor)))

            cap_min_prof = self._format_profile(cap_min_pairs)
            cap_res_min_prof = self._format_profile(cap_res_min_pairs)
            if cap_min_prof:
                row["cap_min"] = cap_min_prof
            if cap_res_min_prof:
                row["cap_res_min"] = cap_res_min_prof
            return

        existing_cap_min = _require_float("cap_min", row.get("cap_min")) if row.get("cap_min") is not None else 0.0
        row["cap_min"] = max(existing_cap_min, per_unit_min)

        existing_cap_res_min = _require_float("cap_res_min", self._profile_peak(row.get("cap_res_min"))) if row.get("cap_res_min") is not None else 0.0
        row["cap_res_min"] = max(existing_cap_res_min, 0.0)

        existing_cap_res_max = _require_float("cap_res_max", self._profile_peak(row.get("cap_res_max"))) if row.get("cap_res_max") is not None else 0.0
        row["cap_res_max"] = existing_cap_res_max

    # Metric helpers ---------------------------------------------------------
    def _existing_metrics(self, tech: Technology, district: Optional[int]) -> Optional[dict[str, Any]]:
        metrics: dict[str, Any] | None = None
        if district is not None:
            rid = self.district_to_region.get(district)
            if rid is not None:
                region_map = self.region_metrics.get(rid)
                if isinstance(region_map, dict):
                    raw = region_map.get(tech.name)
                    if isinstance(raw, dict):
                        metrics = raw
        if metrics is None:
            raw_total = self.total_metrics.get(tech.name)
            if isinstance(raw_total, dict):
                metrics = raw_total
        return metrics

    @staticmethod
    def _existing_capacity(metrics: Optional[dict[str, Any]]) -> float:
        if metrics is None:
            return 0.0
        if not isinstance(metrics, dict):
            raise TypeError("metrics must be a mapping")
        val = _require_float("initial_capacity", metrics["initial_capacity"])
        return max(0.0, val)

    def _retain_factor_for_year(self, year_index: int) -> float:
        if self.retain_schedule:
            if year_index < len(self.retain_schedule):
                return self.retain_schedule[year_index]
            return 0.5
        if self.retain_factor is None:
            return 0.0
        val = _require_float("retain_factor", self.retain_factor)
        return max(0.0, val)

    @staticmethod
    def _ensure_unit_capacity(row: dict[str, Any], existing_capacity: float) -> None:
        unit_cap_val = _require_float("cap_max", row.get("cap_max"))
        if not math.isfinite(unit_cap_val) or unit_cap_val <= 0.0:
            raise ValueError("cap_max must be positive and finite")
        max_units_int = int(row.get("max_units"))
        if max_units_int < 1:
            raise ValueError("max_units must be >= 1")
        required_units = 0
        if existing_capacity and existing_capacity > 0.0:
            required_units = int(math.ceil(existing_capacity / unit_cap_val))
        if required_units > max_units_int:
            raise ValueError(
                f"Existing capacity {existing_capacity} exceeds cap_max * max_units"
                f" ({unit_cap_val} * {max_units_int}) for {row.get('conversion_process_name')}"
            )
        row["max_units"] = max_units_int

    def _existing_overrides(
        self,
        tech: Technology,
        district: Optional[int],
        metrics: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if metrics is None:
            metrics = self._existing_metrics(tech, district)

        overrides: dict[str, Any] = {}
        cap = 0.0
        if isinstance(metrics, dict):
            cap = _require_float("initial_capacity", metrics["initial_capacity"])
            output = _require_float("initial_energy_output", metrics["initial_energy_output"])
            base_name, _ = _split_base_and_district(tech.name)
            is_indirect = base_name.startswith("ind_")
            is_central = _is_central_heat_supply(tech.name)
            is_dhn_pass_through = base_name == "heat_grid" or base_name in HEAT_EXCHANGER_BASE_NAMES

            if is_central and output > 0.0 and cap <= 0.0:
                raise ValueError(f"Invalid central retention metrics for {tech.name}: initial_energy_output requires positive initial_capacity")

            lifetime_years = _require_float("technical_lifetime", tech.technical_lifetime)
            window_factor_raw = self.retain_years_factor if self.retain_years_factor is not None else 1.0
            window_factor = _require_float("retain_years_factor", window_factor_raw)
            window_factor = max(0.0, window_factor)
            remaining_years = max(0.0, lifetime_years * window_factor)

            if self.scenario_years:
                if cap > 0.0:
                    cap_profile = self._format_profile(
                        [
                            (year, cap if self._years_since_start(year) <= remaining_years + 1e-9 else 0.0)
                            for year in self.scenario_years
                        ]
                    )
                    if cap_profile:
                        overrides["cap_res_min"] = cap_profile
                        overrides["cap_res_max"] = cap_profile
                if output > 0.0 and not is_dhn_pass_through:
                    pairs = []
                    for idx, year in enumerate(self.scenario_years):
                        if self._years_since_start(year) > remaining_years + 1e-9:
                            pairs.append((year, 0.0))
                            continue
                        if (
                            self.lockout_until_year is not None
                            and year < self.lockout_until_year
                            and not is_indirect
                            and not _is_central_heat_supply(tech.name)
                        ):
                            pairs.append((year, 0.0))
                            continue
                        factor = self._retain_factor_for_year(idx)
                        if factor <= 0.0:
                            pairs.append((year, 0.0))
                            continue
                        pairs.append((year, output * factor))
                    min_profile = self._format_profile(pairs)
                    if min_profile:
                        overrides["min_eout"] = min_profile
            else:
                if cap > 0.0:
                    overrides["cap_res_min"] = cap
                    overrides["cap_res_max"] = cap
                if output > 0.0 and not is_dhn_pass_through:
                    factor = self._retain_factor_for_year(0)
                    if factor > 0.0:
                        overrides["min_eout"] = output * factor

        return overrides

    def _merge_overrides(
        self,
        primary: Optional[dict[str, Any]],
        secondary: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        if not primary and not secondary:
            return {}
        merged: dict[str, Any] = {}
        for key, value in (primary or {}).items():
            merged[key] = value
        for key, value in (secondary or {}).items():
            if value is None:
                continue
            if key == "min_eout" and key in merged:
                merged_value = self._merge_profile_values(merged[key], value)
                merged[key] = merged_value if merged_value is not None else value
                continue
            merged[key] = value
        return merged

    def _min_eout_override(self, tech: Technology, district: Optional[int] = None) -> Optional[dict[str, Any]]:
        base_name, tech_district = _split_base_and_district(tech.name)
        if district is None and tech_district is not None:
            district = tech_district
        if (base_name in HEAT_EXCHANGER_BASE_NAMES or base_name == "heat_grid") and district is not None:
            rid = self.district_to_region.get(district, district)
            target = max(
                self.min_dhn_targets.get(rid, 0.0),
                self.min_heat_grid_targets.get(rid, 0.0),
            )
            if target and target > 0:
                if self.scenario_years and self.lockout_until_year is not None:
                    pairs = [
                        (year, 0.0 if year < self.lockout_until_year else float(target))
                        for year in self.scenario_years
                    ]
                    prof = self._format_profile(pairs)
                    if prof:
                        return {"min_eout": prof}
                return {"min_eout": float(target)}
        return None

    # Profile utilities ------------------------------------------------------
    def _format_profile(self, pairs: List[Tuple[int, float]]) -> str | None:
        if not pairs:
            return None
        segments = []
        for year, value in pairs:
            year_i = int(year)
            val_f = float(value)
            segments.append(f"{year_i} {val_f:.10g}")
        return "[" + " ; ".join(segments) + "]"

    def _profile_to_map(self, value: Any) -> Dict[int, float]:
        mapping: Dict[int, float] = {}
        if not self.scenario_years:
            return mapping
        if isinstance(value, str):
            raw = value.strip()
            if raw.startswith("[") and raw.endswith("]"):
                body = raw[1:-1]
                for chunk in body.split(";"):
                    parts = chunk.strip().split()
                    if len(parts) < 2:
                        continue
                    year_i = int(float(parts[0]))
                    val_f = float(parts[1])
                    mapping[year_i] = val_f
                return mapping
        if value is None:
            return mapping
        scalar = float(value)
        for year in self.scenario_years:
            mapping[year] = scalar
        return mapping

    def _merge_profile_values(self, primary_value: Any, secondary_value: Any) -> str | None:
        if not self.scenario_years:
            return secondary_value if secondary_value is not None else primary_value
        primary_map = self._profile_to_map(primary_value)
        secondary_map = self._profile_to_map(secondary_value)
        if not primary_map and not secondary_map:
            return None
        combined: List[Tuple[int, float]] = []
        for year in self.scenario_years:
            val_primary = primary_map.get(year, 0.0)
            val_secondary = secondary_map.get(year, 0.0)
            combined.append((year, max(val_primary, val_secondary)))
        return self._format_profile(combined)

    def _profile_peak(self, value: Any) -> float | None:
        if value is None:
            return None
        mapping = self._profile_to_map(value)
        if mapping:
            return max(mapping.values())
        scalar = float(value)
        if math.isnan(scalar):
            return None
        return scalar

    def _clamp_reserves_to_cap_max(self, row: dict[str, Any]) -> None:
        cap_max_value = row.get("cap_max")
        cap_max_peak = self._profile_peak(cap_max_value)
        if cap_max_peak is None:
            return

        # Use total capacity (cap_max * max_units), so multi-unit builds are honored.
        max_units = _require_float("max_units", row.get("max_units"))
        cap_max_map = self._profile_to_map(cap_max_value)
        if not cap_max_map and self.scenario_years:
            cap_max_map = {year: cap_max_peak for year in self.scenario_years}

        cap_total_map = {year: val * max_units for year, val in cap_max_map.items()} if cap_max_map else None
        cap_total_peak = cap_max_peak * max_units

        for key in ("cap_res_min", "cap_res_max"):
            value = row.get(key)
            if value is None:
                continue
            reserve_map = self._profile_to_map(value)
            if reserve_map and self.scenario_years:
                clamped: List[Tuple[int, float]] = []
                for year in self.scenario_years:
                    reserve_val = reserve_map.get(year)
                    if reserve_val is None:
                        continue
                    limit = cap_total_map.get(year, cap_total_peak) if cap_total_map is not None else cap_total_peak
                    if reserve_val > limit + 1e-9:
                        cp_name = row.get("conversion_process_name", "<unknown>")
                        raise ValueError(
                            f"{key} {reserve_val} exceeds cap_max * max_units ({limit}) for {cp_name} in year {year}"
                        )
                    clamped.append((year, reserve_val))
                if clamped:
                    row[key] = self._format_profile(clamped)
                continue
            reserve_peak = self._profile_peak(value)
            if reserve_peak is None:
                continue
            if reserve_peak > cap_total_peak + 1e-9:
                cp_name = row.get("conversion_process_name", "<unknown>")
                raise ValueError(
                    f"{key} {reserve_peak} exceeds cap_max * max_units ({cap_total_peak}) for {cp_name}"
                )
            row[key] = reserve_peak

    # Capacity limiting ------------------------------------------------------
    def _limit_first_year_capacity(
        self,
        row: dict[str, Any],
        existing_capacity: float,
        tech: Technology,
        metrics: Optional[dict[str, Any]] = None,
    ) -> None:
        if not self.scenario_years:
            return
        skip_names = {"grid_electricity"}
        if tech.name in skip_names:
            return

        skip_prefixes = ("HeatDemand", "Dummy")
        for prefix in skip_prefixes:
            if tech.name.startswith(prefix):
                return

        name_lower = tech.name.lower()

        def _has_h2(value: Any) -> bool:
            return isinstance(value, str) and "hydrogen" in value.lower()

        cin = tech.commodity_in
        cout = tech.commodity_out
        hydrogen_related = "hydrogen" in name_lower or _has_h2(cin) or _has_h2(cout)

        cap_limit = max(0.0, float(existing_capacity or 0.0))
        base_name, _ = _split_base_and_district(tech.name)
        is_indirect = base_name.startswith("ind_")
        if cap_limit <= 0.0 and is_indirect and isinstance(metrics, dict):
            existing_output = float(metrics["initial_energy_output"] or 0.0)
            if existing_output > 0.0:
                cap_limit = max(cap_limit, existing_output / 8760.0)
        first_year = self.scenario_years[0]
        lockout_until_year = self.lockout_until_year if self.lockout_until_year is not None else first_year

        hydrogen_start_year = 2030

        def _coerce_default(value: Any) -> Optional[float]:
            if value is None:
                return None
            if isinstance(value, str):
                raw = value.strip()
                if raw.startswith("[") and raw.endswith("]"):
                    return None
            val = float(value)
            if math.isnan(val):
                return None
            return val

        def _update_column(
            col: str,
            adjust: Callable[[int, Optional[float]], Optional[float]],
        ) -> None:
            value = row.get(col)
            base_map = self._profile_to_map(value)
            default = _coerce_default(value)
            changed = False
            pairs: List[Tuple[int, float]] = []
            for year in self.scenario_years:
                base_raw = base_map.get(year, default)
                base_val = _coerce_default(base_raw)
                adjusted = adjust(year, base_val)
                if adjusted is None:
                    if base_val is None:
                        continue
                    adjusted = base_val
                adjusted_f = float(adjusted)
                if base_val is None or abs(adjusted_f - (base_val if base_val is not None else adjusted_f)) > 1e-9:
                    changed = True
                pairs.append((year, adjusted_f))
            if changed and pairs:
                formatted = self._format_profile(pairs)
                if formatted:
                    row[col] = formatted

        def _adjust_cap_max(year: int, base: Optional[float]) -> Optional[float]:
            if hydrogen_related:
                if year < hydrogen_start_year:
                    return 0.0
                if base is None:
                    return UNBOUNDED_CAP
            if year < lockout_until_year and not is_indirect:
                # Lockout period: no new capacity beyond what already exists.
                return cap_limit
            if cap_limit <= 0.0:
                return base
            if year != first_year:
                return base
            if base is None:
                return cap_limit
            return min(base, cap_limit)

        def _adjust_cap_min(year: int, base: Optional[float]) -> Optional[float]:
            base_val = base if base is not None else 0.0
            if hydrogen_related and year < hydrogen_start_year:
                return 0.0
            if year < lockout_until_year and not is_indirect:
                return min(base_val, cap_limit)
            # Keep user-specified minima for later years when there is no existing capacity.
            if cap_limit <= 0.0:
                return base_val
            return base_val

        def _adjust_cap_res_min(year: int, base: Optional[float]) -> Optional[float]:
            base_val = base if base is not None else 0.0
            if hydrogen_related and year < hydrogen_start_year:
                return 0.0
            if year < lockout_until_year and not is_indirect:
                return min(base_val, cap_limit)
            return base_val

        def _adjust_cap_res_max(year: int, base: Optional[float]) -> Optional[float]:
            if hydrogen_related and year < hydrogen_start_year:
                return 0.0
            if year < lockout_until_year and not is_indirect:
                if base is None:
                    return cap_limit
                return min(float(base), cap_limit)
            return base

        _update_column("cap_max", _adjust_cap_max)
        _update_column("cap_min", _adjust_cap_min)
        _update_column("cap_res_min", _adjust_cap_res_min)
        _update_column("cap_res_max", _adjust_cap_res_max)

    # Misc helpers -----------------------------------------------------------
    def _years_since_start(self, year: int) -> float:
        if not self.scenario_years:
            return 0.0
        return float(year - self.scenario_years[0])

    def _heat_comm_for_district(self, district: int) -> str:
        heat_idx = self.district_index.get(district)
        if heat_idx is None or heat_idx >= len(self.heat_names):
            raise ValueError(f"Missing heat commodity mapping for district {district}")
        return self.heat_names[heat_idx]
# ====================================================================================
# Backend adapter
# ====================================================================================
class CESMBackend(OptimizationModel):
    """Adapter that writes CESM inputs, invokes CESM, and parses results."""

    def __init__(
        self,
        workdir: str | Path = "CESM",
        cli: Optional[List[str]] = None,
        run_args: Optional[List[str]] = None,
        run_subdir: Optional[str] = None,
        results_db_name: str = "db.sqlite",
        model_name: Optional[str] = None,
        scenario_name: Optional[str] = None,
        tss_name: Optional[str] = None,
        write_inputs: bool = True,
        scenario: Scenario | None = None,
        demand_name: str = "residential_heat",
        retain_existing_output_factor: float | None = None,
        retain_existing_output_years_factor: float | None = None,
        retain_existing_output_schedule: Optional[List[float]] = None,
    ):
        super().__init__(conversion_sub_processes=None, conversion_processes=None, commodities=None, tss=None)
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

        self.cli = cli or ["env\\Scripts\\python.exe", "-m", "main", "run"]
        self.run_args = run_args or []

        self.model_name = model_name
        self.scenario_name = scenario_name
        self.tss_name = tss_name
        self.run_subdir = run_subdir or (f"{self.model_name}-{self.scenario_name}" if self.model_name and self.scenario_name else None)
        self.results_db_name = results_db_name

        self.write_inputs = write_inputs
        self.scenario = scenario
        self.demand_name = demand_name
        self.retain_existing_output_factor = retain_existing_output_factor
        self.retain_existing_output_years_factor = retain_existing_output_years_factor
        self.retain_existing_output_schedule = retain_existing_output_schedule

    # OptimizationModel API ---------------------------------------------------
    def optimize(self, model: EnergySystem, *, scenario: Scenario | None = None, demand_name: str | None = None) -> Solution:
        if not isinstance(model, EnergySystem):
            raise TypeError("CESMBackend.optimize expects an EnergySystem")

        logger.debug("optimize: energy_system commodity_config=%s", model.commodity_config)

        scenario_obj = scenario or self.scenario
        if scenario_obj is None:
            raise ValueError("scenario is required to write CESM inputs")
        demand = demand_name or self.demand_name or "residential_heat"

        self._materialize_inputs_from_energy_system(model, scenario_obj, demand)
        self._run_cli()
        db_path = self._expected_run_db()
        if not db_path.exists():
            raise FileNotFoundError(
                f"Expected DB not found: {db_path}\n"
                f"Check logs: {self.workdir / 'cesm_stdout.log'}, {self.workdir / 'cesm_stderr.log'} "
                f"and command {self.workdir / 'cesm_cmd.txt'}"
            )
        self._backfill_missing_commodity_timeseries(db_path)
        results = self._parse_outputs(db_path)
        sol = Solution()
        sol.results = results
        return sol

    # Internal helpers --------------------------------------------------------
    def _expected_run_db(self) -> Path:
        if not self.run_subdir:
            raise ValueError("run_subdir is not set (expected '{model}-{scenario}').")
        primary = self.workdir / "Runs" / self.run_subdir / self.results_db_name
        return primary

    def _materialize_inputs_from_energy_system(self, energy_system: EnergySystem, scenario: Scenario, demand_name: str) -> None:
        if not self.write_inputs:
            return
        if not (self.model_name and self.scenario_name and self.tss_name):
            raise ValueError("model_name, scenario_name, and tss_name must be set to write CESM inputs")

        write_cesm_inputs_from_energy_system(
            energy_system,
            scenario,
            workdir=self.workdir,
            model_name=self.model_name,
            scenario_name=self.scenario_name,
            tss_name=self.tss_name,
            demand_name=demand_name,
            retain_existing_output_factor=self.retain_existing_output_factor,
            retain_existing_output_years_factor=self.retain_existing_output_years_factor,
            retain_existing_output_schedule=self.retain_existing_output_schedule,
        )

    def _run_cli(self, extra_args: list[str] | None = None) -> None:
        exe = self.cli[0]
        exe_path = Path(exe)
        if not exe_path.is_absolute():
            exe_path = (self.workdir / exe_path).resolve()
        if not exe_path.exists():
            raise FileNotFoundError(
                f"CESM executable not found: {exe_path}\nWorkdir: {self.workdir.resolve()}\nCLI: {self.cli}"
            )
        cmd = [str(exe_path), *self.cli[1:], *self.run_args]
        if extra_args:
            cmd.extend(extra_args)
        (self.workdir / "cesm_cmd.txt").write_text(json.dumps(cmd, indent=2))
        cp = subprocess.run(cmd, cwd=self.workdir, capture_output=True, text=True)
        (self.workdir / "cesm_stdout.log").write_text(cp.stdout or "")
        (self.workdir / "cesm_stderr.log").write_text(cp.stderr or "")
        if cp.returncode != 0:
            raise RuntimeError(f"CESM failed (exit {cp.returncode}). See logs in workdir")

    def _backfill_missing_commodity_timeseries(self, db_path: Path) -> None:
        with sqlite3.connect(db_path) as con:
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {row[0] for row in cur.fetchall()}
            required = {"output_cs_y_t", "conversion_subprocess", "output_co_y_t"}
            if not required.issubset(tables):
                return
            cur.execute(
                """
                WITH per_kind AS (
                    SELECT cs.cout_id AS co_id, o.y_id, o.t_id,
                           SUM(COALESCE(o.eouttime, 0.0)) AS value,
                           1 AS is_gen
                    FROM output_cs_y_t AS o
                    JOIN conversion_subprocess AS cs ON cs.id = o.cs_id
                    GROUP BY cs.cout_id, o.y_id, o.t_id
                    HAVING ABS(SUM(COALESCE(o.eouttime, 0.0))) > 1e-9
                    UNION ALL
                    SELECT cs.cin_id AS co_id, o.y_id, o.t_id,
                           SUM(COALESCE(o.eintime, 0.0)) AS value,
                           0 AS is_gen
                    FROM output_cs_y_t AS o
                    JOIN conversion_subprocess AS cs ON cs.id = o.cs_id
                    GROUP BY cs.cin_id, o.y_id, o.t_id
                    HAVING ABS(SUM(COALESCE(o.eintime, 0.0))) > 1e-9
                ),
                aggregated AS (
                    SELECT co_id, y_id, t_id,
                           SUM(CASE WHEN is_gen = 1 THEN value ELSE 0 END) AS gen,
                           SUM(CASE WHEN is_gen = 0 THEN value ELSE 0 END) AS cons
                    FROM per_kind
                    GROUP BY co_id, y_id, t_id
                ),
                missing AS (
                    SELECT a.*
                    FROM aggregated AS a
                    LEFT JOIN output_co_y_t AS existing
                          ON existing.co_id = a.co_id
                         AND existing.y_id = a.y_id
                         AND existing.t_id = a.t_id
                    WHERE existing.co_id IS NULL
                )
                INSERT INTO output_co_y_t (co_id, y_id, t_id, enetgen, enetcons)
                SELECT co_id, y_id, t_id, gen, cons FROM missing
                """
            )
            con.commit()

    def _parse_outputs(self, db_path: Path) -> Dict[str, Any]:
        out: Dict[str, Any] = {"status": "ok", "db": str(db_path)}
        with sqlite3.connect(db_path) as con:
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [t[0] for t in cur.fetchall()]
            out["tables"] = tables

            def pragma_cols(table: str) -> list[str]:
                cur.execute(f"PRAGMA table_info({table})")
                return [row[1] for row in cur.fetchall()]
            columns = {t: pragma_cols(t) for t in tables}
            row_counts: Dict[str, int] = {}
            for t in tables:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                row_counts[t] = int(cur.fetchone()[0])
            out["columns"] = columns
            out["row_counts"] = row_counts

            def kv(table: str, k: str, v: str) -> dict:
                cur.execute(f"SELECT {k}, {v} FROM {table}")
                return {row[0]: row[1] for row in cur.fetchall()}
            year_map = kv("year", "id", "value") if "year" in tables else {}
            com_map = kv("commodity", "id", "name") if "commodity" in tables else {}

            tech_map: dict[int, str] = {}
            if "conversion_subprocess" in tables and "conversion_process" in tables:
                cur.execute(
                    """
                    SELECT cs.id, cp.name
                    FROM conversion_subprocess cs
                    JOIN conversion_process cp ON cs.cp_id = cp.id
                    """
                )
                tech_map = {row[0]: row[1] for row in cur.fetchall()}

            kpis: Dict[str, Any] = {}
            if "output_y" in tables and all(c in columns["output_y"] for c in ("y_id", "total_annual_co2_emission")):
                cur.execute("SELECT y_id, SUM(total_annual_co2_emission) FROM output_y GROUP BY y_id")
                kpis["emissions_by_year"] = {str(year_map.get(y, y)): float(v) for (y, v) in cur.fetchall()}

            if "output_co_y_t" in tables and all(c in columns["output_co_y_t"] for c in ("co_id", "y_id", "enetgen", "enetcons")):
                cur.execute(
                    "SELECT co_id, y_id, SUM(enetgen) AS gen, SUM(enetcons) AS cons FROM output_co_y_t GROUP BY co_id, y_id"
                )
                gen: Dict[str, Dict[str, float]] = {}
                cons: Dict[str, Dict[str, float]] = {}
                for co, y, g, c in cur.fetchall():
                    cn = str(com_map.get(co, co))
                    yn = str(year_map.get(y, y))
                    gen.setdefault(cn, {})[yn] = float(g)
                    cons.setdefault(cn, {})[yn] = float(c)
                kpis["gen_by_commodity_year"] = gen
                kpis["cons_by_commodity_year"] = cons
                bal: Dict[str, float] = {}
                for c, per_year in gen.items():
                    for yname, gval in per_year.items():
                        bal[yname] = bal.get(yname, 0.0) + gval
                for c, per_year in cons.items():
                    for yname, cval in per_year.items():
                        bal[yname] = bal.get(yname, 0.0) - cval
                kpis["net_energy_balance_by_year"] = bal

            if "output_cs_y" in tables and all(c in columns["output_cs_y"] for c in ("cs_id", "y_id", "eouttot")):
                cur.execute("SELECT cs_id, y_id, SUM(eouttot) FROM output_cs_y GROUP BY cs_id, y_id")
                e_by_tech: Dict[str, Dict[str, float]] = {}
                for cs, y, val in cur.fetchall():
                    tn = str(tech_map.get(cs, cs))
                    yn = str(year_map.get(y, y))
                    e_by_tech.setdefault(tn, {})[yn] = float(val)
                kpis["energy_by_tech_year"] = e_by_tech

            if "output_cs_y" in tables and all(c in columns["output_cs_y"] for c in ("cs_id", "y_id", "cap_active", "cap_new")):
                cur.execute("SELECT cs_id, y_id, SUM(cap_active), SUM(cap_new) FROM output_cs_y GROUP BY cs_id, y_id")
                cap_active: Dict[str, Dict[str, float]] = {}
                cap_new: Dict[str, Dict[str, float]] = {}
                for cs, y, a, n in cur.fetchall():
                    tn = str(tech_map.get(cs, cs))
                    yn = str(year_map.get(y, y))
                    cap_active.setdefault(tn, {})[yn] = float(a)
                    cap_new.setdefault(tn, {})[yn] = float(n)
                kpis["cap_active_by_tech_year"] = cap_active
                kpis["cap_new_by_tech_year"] = cap_new

            if "output_cs_y_t" in tables and all(c in columns["output_cs_y_t"] for c in ("cs_id", "y_id", "pout")):
                cur.execute("SELECT cs_id, y_id, MAX(pout) FROM output_cs_y_t GROUP BY cs_id, y_id")
                peak: Dict[str, Dict[str, float]] = {}
                for cs, y, p in cur.fetchall():
                    tn = str(tech_map.get(cs, cs))
                    yn = str(year_map.get(y, y))
                    peak.setdefault(tn, {})[yn] = float(p)
                kpis["peak_pout_by_tech_year"] = peak

            if "output_global" in tables and any(c in columns["output_global"] for c in ("OPEX", "CAPEX", "TOTEX")):
                cur.execute("SELECT OPEX, CAPEX, TOTEX FROM output_global LIMIT 1")
                row = cur.fetchone()
                if row is not None:
                    kpis["system_cost_totals"] = {
                        "OPEX": float(row[0]) if row[0] is not None else None,
                        "CAPEX": float(row[1]) if row[1] is not None else None,
                        "TOTEX": float(row[2]) if row[2] is not None else None,
                    }
            out["kpis"] = kpis
        return out

def tech_to_cesms_row(
    tech: Technology,
    *,
    cp_name: str,
    cin: str,
    cout: str,
    scenario_name: str,
    **overrides: Any,
) -> Dict[str, Any]:
    """Utility used by tests to convert a Technology into a ConversionSubProcess row dict."""
    cap_min_attr = tech.cap_min
    cap_max_attr = tech.cap_max
    max_units = tech.max_units
    spec_co2_attr = tech.spec_co2
    is_storage_attr = tech.is_storage
    out_frac_min_attr = tech.out_frac_min
    out_frac_max_attr = tech.out_frac_max
    in_frac_min_attr = tech.in_frac_min
    in_frac_max_attr = tech.in_frac_max
    availability_profile_attr = tech.availability_profile
    output_profile_attr = tech.output_profile

    efficiency_val = _require_finite("efficiency", tech.efficiency, gt_zero=True)
    lifetime_val = _require_finite("technical_lifetime", tech.technical_lifetime, gt_zero=True)
    opex_energy_val = _require_finite("opex_cost_energy", tech.opex_cost_energy, allow_zero=True)
    opex_power_val = _require_finite("opex_cost_power", tech.opex_cost_power, allow_zero=True)
    capex_power_val = _require_finite("capex_cost_power", tech.capex_cost_power, allow_zero=True)
    capex_base_val = _require_finite("capex_cost_base", tech.capex_cost_base, allow_zero=True)

    spec_co2_val = None if spec_co2_attr is None else _require_finite("spec_co2", spec_co2_attr, allow_zero=True)
    is_storage_val = bool(is_storage_attr)
    c_rate_val: float | None = None
    efficiency_charge_val: float | None = None
    if is_storage_val:
        c_rate_raw = tech.c_rate
        if c_rate_raw is None:
            raise ValueError(f"c_rate is required for storage technology '{tech.name}'")
        efficiency_charge_raw = tech.efficiency_charge
        if efficiency_charge_raw is None:
            raise ValueError(f"efficiency_charge is required for storage technology '{tech.name}'")
        c_rate_val = _require_finite("c_rate", c_rate_raw, gt_zero=True)
        efficiency_charge_val = _require_finite("efficiency_charge", efficiency_charge_raw, gt_zero=True)

    def _optional_frac(label: str, raw: Any) -> float | None:
        if raw is None:
            return None
        return _require_finite(label, raw, allow_zero=True)

    out_frac_min_val = _optional_frac("out_frac_min", out_frac_min_attr)
    out_frac_max_val = _optional_frac("out_frac_max", out_frac_max_attr)
    in_frac_min_val = _optional_frac("in_frac_min", in_frac_min_attr)
    in_frac_max_val = _optional_frac("in_frac_max", in_frac_max_attr)

    if cap_max_attr is None:
        raise ValueError(f"cap_max is required for technology '{tech.name}'")
    cap_max_val = _require_finite(f"cap_max for {tech.name}", cap_max_attr, gt_zero=True)
    max_units_val = _require_positive_int("max_units", max_units)

    cap_min_val = None
    if cap_min_attr is not None:
        cap_min_val = _require_finite(f"cap_min for {tech.name}", cap_min_attr, allow_zero=True, gt_zero=False)

    row: Dict[str, Any] = {
        "conversion_process_name": cp_name,
        "commodity_in": _canon_co(cin),
        "commodity_out": _canon_co(cout),
        "scenario": scenario_name,
        "efficiency": efficiency_val,
        "technical_lifetime": lifetime_val,
        "technical_availability": 1.0,
        "spec_co2": spec_co2_val,
        "c_rate": c_rate_val,
        "efficiency_charge": efficiency_charge_val,
        "is_storage": 1 if is_storage_val else 0,
        "opex_cost_energy": opex_energy_val,
        "opex_cost_power": opex_power_val,
        "capex_cost_power": capex_power_val,
        "capex_cost_base": capex_base_val,
        "cap_min": cap_min_val,
        "cap_max": cap_max_val,
        "cap_active": None,
        "max_units": max_units_val,
        "out_frac_min": out_frac_min_val,
        "out_frac_max": out_frac_max_val,
        "in_frac_min": in_frac_min_val,
        "in_frac_max": in_frac_max_val,
        "availability_profile": availability_profile_attr,
        "output_profile": output_profile_attr,
    }
    row.update(overrides)
    return row

def _write_cesm_inputs_from_om(
    om,
    *,
    workdir: PathLike,
    model_name: str,
    scenario_name: str,
    tss_name: str,
    polygons_path: Optional[PathLike] = None,
    polygons_gdf: Optional[gpd.GeoDataFrame] = None,
    street_segments_gdf: Optional[gpd.GeoDataFrame] = None,
    data_dir: Optional[PathLike] = None,
    start_year: int | None = None,
    end_year: int | None = None,
    year_gap: int | None = None,
    discount_rate: float | None = None,
    lockout_years: int | None = None,
    dt_hours: int = 1,
    elec_profile_file: str | None = None,
    heat_commodity_base: str | None = None,
    elec_price_eur_per_mwh: float | None = None,
    export_price_eur_per_mwh: float | None = None,
    grid_prices: dict[str, float] | None = None,
    supply_prices: dict[str, float] | None = None,
    pipe_loss_fraction: float | None = None,
    pipe_cap_max_mw: float | None = None,
    pipe_opex_eur_per_mwh: float | None = None,
    pipe_capex_eur_per_mw: float | None = None,
    pipe_lifetime_years: int | None = None,
    selected_techs: list[Technology] | TechnologyRegistry | None = None,
    retain_existing_output_factor: float | None = None,
    retain_existing_output_years_factor: float | None = None,
    retain_existing_output_schedule: Optional[List[float]] = None,
    technology_registry: TechnologyRegistry | None = None,
    pipe_technology_name: str = "heat_pipe",
) -> None:
    workdir = Path(workdir)
    paths = _prepare_io_paths(workdir, model_name, tss_name)
    ts_dir = paths.timeseries_dir
    if start_year is None or end_year is None or year_gap is None:
        raise ValueError("start_year, end_year, and year_gap must be provided via arguments")
    if discount_rate is None:
        raise ValueError("discount_rate must be provided via arguments")

    if retain_existing_output_schedule is None:
        raise ValueError("retain_existing_output_schedule must be provided by the scenario (no defaults)")

    if data_dir is None:
        raise ValueError("data_dir must be provided via the EnergySystem (no default path)")
    data_dir = Path(data_dir)
    om_regions = list(om.regions)

    if grid_prices is None or supply_prices is None:
        raise ValueError(
            "Commodity prices must be provided via EnergySystem.commodity_config or explicitly as grid_prices/supply_prices"
        )
    grid_prices = sanitize_price_map(grid_prices)
    supply_prices = sanitize_price_map(supply_prices)

    if elec_price_eur_per_mwh is None:
        elec_price_eur_per_mwh = float(grid_prices["electricity"])

    if export_price_eur_per_mwh is None:
        export_price_eur_per_mwh = float(grid_prices["export"])

    logger.debug(
        "commodity pricing resolved: grid_prices=%s, elec=%s, export=%s, supply_prices=%s",
        grid_prices,
        elec_price_eur_per_mwh,
        export_price_eur_per_mwh,
        supply_prices,
    )
    
    start_year_int = int(start_year)
    end_year_int = int(end_year)
    year_step_int = int(year_gap)
    if year_step_int <= 0:
        raise ValueError("year_gap must be a positive integer")
    scenario_years: List[int] = list(range(start_year_int, end_year_int + 1, year_step_int))
    if not scenario_years:
        raise ValueError("scenario years cannot be empty")

    om_profile = om.demand_profile
    if hasattr(om_profile, "values"):
        om_profile = om_profile.values
    profile_full = np.asarray(list(om_profile), dtype=float)
    if profile_full.size != 8760:
        raise ValueError("om.demand_profile must contain exactly 8760 values")
    tss_vals = ensure_tss_indices(paths.tss_file)
    profile_full = normalize_profile_for_tss(profile_full, tss_vals)
    demand_profile_name = "HeatDemandProfile"
    _write_demand_profile(ts_dir, demand_profile_name, profile_full)
    districts: List[int]
    heat_names: List[str]
    pipe_pairs: List[Tuple[int, int]] = []
    pipe_specs_by_pair: dict[tuple[int, int], dict[str, float]] = {}
    base_heat_name = heat_commodity_base or om.commodity or demand_commodity
    gdf: Optional[gpd.GeoDataFrame] = None
    if polygons_gdf is not None:
        gdf = polygons_gdf.copy()
    if polygons_path is None and gdf is None:
        raise ValueError("polygons must be provided via EnergySystem (no synthetic fallback)")
    if gdf is None:
        if gpd is None:
            raise RuntimeError("geopandas required for polygons_path.")
        poly_path = Path(polygons_path)
        if not poly_path.exists():
            raise FileNotFoundError(f"polygons not found: {poly_path}")
        gdf = gpd.read_file(poly_path)
    if gdf.crs is None or gdf.crs.is_geographic:
        gdf = gdf.to_crs(3035)
    if street_segments_gdf is not None:
        street_segments_gdf = street_segments_gdf.copy()
        if street_segments_gdf.crs is None and gdf.crs is not None:
            street_segments_gdf = street_segments_gdf.set_crs(gdf.crs, allow_override=True)
        if gdf.crs is not None and street_segments_gdf.crs is not None and street_segments_gdf.crs != gdf.crs:
            street_segments_gdf = street_segments_gdf.to_crs(gdf.crs)
        if street_segments_gdf.crs is None or street_segments_gdf.crs.is_geographic:
            street_segments_gdf = street_segments_gdf.to_crs(3035)

    n = int(len(gdf))
    if n < 1:
        raise ValueError("No polygons found.")
    areas = gdf.geometry.area.values.astype(float)
    if not np.isfinite(areas).all() or areas.sum() <= 0:
        area_weights = np.ones(n, dtype=float) / n
    else:
        area_weights = areas / areas.sum()
    districts = list(range(n))
    if n == 1:
        heat_names = [f"{base_heat_name}"]
    else:
        heat_names = [f"{base_heat_name}_D{i}" for i in range(n)]
        if street_segments_gdf is not None and not street_segments_gdf.empty:
            pipe_candidates = build_inter_dhn_pipes_from_street_segments(
                polygons=gdf,
                street_segments_gdf=street_segments_gdf,
                pipe_capex_eur_per_km=1.0,
                region_id_column="id",
            )
            pipe_specs_by_pair = {
                (int(i), int(j)): dict(specs or {})
                for (i, j), specs in pipe_candidates.items()
            }
            pipe_pairs = sorted((int(i), int(j)) for (i, j) in pipe_candidates.keys())

    district_index = {d: idx for idx, d in enumerate(districts)}
    if len(districts) <= 1:
        grid_hub_name = f"{base_heat_name}_Grid"
        grid_names = [grid_hub_name]
    else:
        grid_hub_name = f"{base_heat_name}_GridHub"
        grid_names = [f"{base_heat_name}_Grid_D{d}" for d in districts]

    district_heat_in_names = {d: f"district_heat_in_D{d}" for d in districts}
    district_heat_out_names = {d: f"district_heat_out_D{d}" for d in districts}
    district_heat_import_names = {d: f"district_heat_import_D{d}" for d in districts}

    def _annual_demands_from_om() -> List[float]:
        if not om_regions:
            return []
        annual_map = om.annual_demand
        target_year = int(start_year)
        values: List[float] = []
        for rid in om_regions:
            per_year = annual_map[rid]
            values.append(float(per_year[target_year]))
        return values

    annual_heat_by_d = _annual_demands_from_om()
    if annual_heat_by_d and len(annual_heat_by_d) != len(districts):
        total_from_om = float(sum(annual_heat_by_d))
        if total_from_om > 0:
            annual_heat_by_d = (area_weights * total_from_om).tolist()
        else:
            annual_heat_by_d = [0.0] * len(districts)
    if not annual_heat_by_d:
        annual_heat_by_d = [0.0] * len(districts)
    annual_heat_mwh_total = float(sum(annual_heat_by_d))
    if annual_heat_mwh_total < 0.0:
        raise ValueError("Annual heat demand is negative.")
    if len(annual_heat_by_d) != len(districts):
        annual_heat_by_d = (area_weights * annual_heat_mwh_total).tolist()
    xlsx = paths.xlsx_path
    units_df = _units_df()
    scenario_df = _scenario_df(
        scenario_name=scenario_name,
        start_year=start_year,
        end_year=end_year,
        year_gap=year_gap,
        tss_name=tss_name,
        discount_rate=discount_rate,
    )
    tss_df = _tss_df(tss_name=tss_name, dt_hours=dt_hours)
    base = ["Electricity", "External", "Dummy"]
    district_heat_names = (
        [district_heat_in_names[d] for d in districts]
        + [district_heat_out_names[d] for d in districts]
    )
    commodity_list = base + [grid_hub_name] + grid_names + heat_names + district_heat_names
    commodity_list = list(dict.fromkeys(commodity_list))
    sel_list: list[Technology] = []
    if isinstance(selected_techs, TechnologyRegistry):
        sel_list = selected_techs.get_all(return_type="instance")
    elif isinstance(selected_techs, list):
        sel_list = [t for t in selected_techs if isinstance(t, Technology)]
    elif selected_techs is None:
        om_techs = om.technologies
        if isinstance(om_techs, dict):
            sel_list = [t for t in om_techs.values() if isinstance(t, Technology)]
    dedup: dict[str, Technology] = {}
    filtered: list[Technology] = []
    for tech in sel_list:
        if tech.name not in dedup:
            dedup[tech.name] = tech
            filtered.append(tech)
    sel_list = filtered

    default_registry = get_default_technology_registry()
    default_registry.load_from_default()
    pipe_tech = default_registry.get_by_name(pipe_technology_name)

    demand_street_lengths = pd.to_numeric(
        gdf.reindex(columns=["demand_street_length_m"]).iloc[:, 0],
        errors="coerce",
    ).fillna(0.0)
    gdf = gdf.assign(demand_street_length_m=demand_street_lengths)

    local_pipe_capex_per_km = float(pipe_tech.pipe_capex_eur_per_km)
    local_grid_costs = build_district_heat_grid_from_polygons(
        polygons=gdf,
        local_pipe_capex_eur_per_km=local_pipe_capex_per_km,
        street_segments_gdf=street_segments_gdf,
        region_id_column="id",
    )
    local_dhn_capex_base_by_district: dict[int, float] = {
        int(district_id): float(payload["local_grid_capex_base_eur"])
        for district_id, payload in local_grid_costs.items()
    }

    def _require_value(value_override: Any, spec_value: Any, field_name: str) -> Any:
        if value_override is not None:
            return value_override
        if spec_value is not None:
            return spec_value
        raise ValueError(f"{field_name} is required for pipe technology '{pipe_technology_name}'")

    spec_efficiency = max(0.0, min(1.0, float(pipe_tech.efficiency)))
    spec_loss = max(0.0, 1.0 - spec_efficiency)
    spec_capex = float(pipe_tech.capex_cost_power)
    spec_opex = float(pipe_tech.opex_cost_energy)
    spec_lifetime = int(pipe_tech.technical_lifetime)
    spec_cap_max = float(pipe_tech.cap_max)

    if pipe_pairs:
        resolved_loss = _require_value(pipe_loss_fraction, spec_loss, "pipe_loss_fraction or pipe efficiency")
        pipe_eff = max(0.0, 1.0 - float(resolved_loss))
        pipe_capex_value = _require_value(pipe_capex_eur_per_mw, spec_capex, "pipe_capex_eur_per_mw")
        pipe_opex_value = _require_value(pipe_opex_eur_per_mwh, spec_opex, "pipe_opex_eur_per_mwh")
        pipe_lifetime_value = int(_require_value(pipe_lifetime_years, spec_lifetime, "pipe_lifetime_years"))
        pipe_cap_max_value = float(_require_value(pipe_cap_max_mw, spec_cap_max, "pipe_cap_max_mw"))
    else:
        resolved_loss = _require_value(pipe_loss_fraction, spec_loss, "pipe_loss_fraction or pipe efficiency")
        pipe_eff = max(0.0, 1.0 - float(resolved_loss))
        pipe_capex_value = _require_value(pipe_capex_eur_per_mw, spec_capex, "pipe_capex_eur_per_mw")
        pipe_opex_value = _require_value(pipe_opex_eur_per_mwh, spec_opex, "pipe_opex_eur_per_mwh")
        pipe_lifetime_value = int(_require_value(pipe_lifetime_years, spec_lifetime, "pipe_lifetime_years"))
        pipe_cap_max_value = float(_require_value(pipe_cap_max_mw, spec_cap_max, "pipe_cap_max_mw"))

    constraints_raw = om.constraints or {}
    if constraints_raw and not isinstance(constraints_raw, dict):
        raise ValueError("constraints must be provided as a mapping")

    demand_commodity = om.commodity or "residential_heat"

    min_dhn_targets_raw = constraints_raw.get("min_dhn_throughput_mwh", {}) if constraints_raw else {}
    if min_dhn_targets_raw and not isinstance(min_dhn_targets_raw, dict):
        raise ValueError("min_dhn_throughput_mwh constraint must be a mapping of region ids to values")
    min_dhn_targets: dict[int, float] = {}
    for key, value in (min_dhn_targets_raw or {}).items():
        rid = to_int_id(key)
        val = float(value)
        if val > 0:
            min_dhn_targets[rid] = float(val)

    min_heat_grid_key = f"min_heat_grid_{demand_commodity}"
    min_heat_grid_raw = constraints_raw.get(min_heat_grid_key, {}) if constraints_raw else {}
    if min_heat_grid_raw and not isinstance(min_heat_grid_raw, dict):
        raise ValueError(f"{min_heat_grid_key} constraint must be a mapping of region ids to values")
    min_heat_grid_targets: dict[int, float] = {}
    for key, value in (min_heat_grid_raw or {}).items():
        rid = to_int_id(key)
        val = float(value)
        if val > 0:
            min_heat_grid_targets[rid] = float(val)

    min_pipe_import_share_raw = constraints_raw.get("min_pipe_import_share_by_region", {}) if constraints_raw else {}
    if min_pipe_import_share_raw and not isinstance(min_pipe_import_share_raw, dict):
        raise ValueError("min_pipe_import_share_by_region constraint must be a mapping of region ids to shares")
    min_pipe_import_share_targets: dict[int, float] = {}
    for key, value in (min_pipe_import_share_raw or {}).items():
        rid = to_int_id(key)
        share = float(value)
        if share <= 0:
            continue
        min_pipe_import_share_targets[rid] = max(0.0, min(1.0, share))

    min_central_cap_raw = constraints_raw.get("min_central_cap_mw", {}) if constraints_raw else {}
    min_central_cap_targets: dict[str, float] = {}
    for tech_name, value in (min_central_cap_raw or {}).items():
        val = float(value)
        if val > 0:
            min_central_cap_targets[tech_name] = float(val)

    min_central_cap_total_raw = constraints_raw.get("min_central_cap_mw_total", {})
    min_central_cap_totals: dict[int, float] = {}
    for key, value in (min_central_cap_total_raw or {}).items():
        did = to_int_id(key)
        val = float(value)
        if val > 0:
            min_central_cap_totals[did] = float(val)

    min_central_cap_total_by_co_raw = constraints_raw.get("min_central_cap_mw_total_by_commodity", {}) 
    min_central_cap_totals_by_co: dict[str, dict[int, float]] = {}
    for commodity_raw, per_district in (min_central_cap_total_by_co_raw or {}).items():
        commodity = str(commodity_raw).strip().lower()
        bucket: dict[int, float] = {}
        for key, value in per_district.items():
            did = to_int_id(key)
            val = float(value)
            if val > 0:
                bucket[did] = float(val)
        if bucket:
            min_central_cap_totals_by_co[commodity] = bucket

    om_region_ids = list(om.regions)
    district_to_region: dict[int, int] = {}
    for idx, district_id in enumerate(districts):
        if idx >= len(om_region_ids):
            raise ValueError(f"Missing region id for district {district_id}")
        raw_rid = om_region_ids[idx]
        district_to_region[district_id] = to_int_id(raw_rid)

    region_metrics = om.region_technology_metrics or {}
    if not isinstance(region_metrics, dict):
        region_metrics = {}

    def _resolve_pipe_metric_dst_district(metric_name: str, rid_key: Any) -> Optional[int]:
        match = re.match(r"^heat_pipe_D(\d+)_D(\d+)$", str(metric_name))
        if not match:
            return None
        first = int(match.group(1))
        second = int(match.group(2))

        rid_int = to_int_id(rid_key)

        first_region = district_to_region.get(first)
        second_region = district_to_region.get(second)
        if rid_int is not None:
            if first_region == rid_int and second_region != rid_int:
                return first
            if second_region == rid_int and first_region != rid_int:
                return second

        # Fallback to legacy interpretation when not disambiguated by region key.
        return second

    for _rid_key, tech_map in list(region_metrics.items()):
        if not isinstance(tech_map, dict):
            continue
        for tech_name, metrics in list(tech_map.items()):
            if not isinstance(metrics, dict):
                continue
            dst_district = _resolve_pipe_metric_dst_district(str(tech_name), _rid_key)
            if dst_district is None:
                continue
            existing_cap = float(metrics["initial_capacity"] or 0.0)
            if existing_cap <= 0.0:
                continue
            dst_region = district_to_region.get(dst_district)
            if dst_region is None:
                continue
            dst_metrics = region_metrics.setdefault(dst_region, {})
            for tech_key in (f"heat_grid_D{dst_district}", f"heat_exchanger_D{dst_district}"):
                seeded = dst_metrics.setdefault(tech_key, {})
                seeded_cap = float(seeded.get("initial_capacity", 0.0) or 0.0)
                if seeded_cap <= 0.0:
                    seeded["initial_capacity"] = 1.0
                seeded.setdefault("initial_energy_output", 0.0)

    total_metrics: dict[str, dict[str, float]] = {}
    for rid, tech_map in region_metrics.items():
        if not isinstance(tech_map, dict):
            continue
        for tech_name, metrics in tech_map.items():
            if not isinstance(metrics, dict):
                continue
            agg = total_metrics.setdefault(
                tech_name,
                {"initial_energy_output": 0.0, "initial_capacity": 0.0},
            )
            agg["initial_energy_output"] += float(metrics["initial_energy_output"] or 0.0)
            agg["initial_capacity"] += float(metrics["initial_capacity"] or 0.0)

    historical_pipe_import_targets_mwh: dict[int, float] = {}
    historical_pipe_import_by_region: dict[int, float] = {}
    for _rid_key, tech_map in list(region_metrics.items()):
        if not isinstance(tech_map, dict):
            continue
        for tech_name, metrics in tech_map.items():
            if not isinstance(metrics, dict):
                continue
            dst_district = _resolve_pipe_metric_dst_district(str(tech_name), _rid_key)
            if dst_district is None:
                continue
            dst_region = district_to_region.get(dst_district)
            if dst_region is None:
                continue
            imported = float(metrics["initial_energy_output"] or 0.0)
            if imported <= 0.0:
                continue
            historical_pipe_import_by_region[dst_region] = historical_pipe_import_by_region.get(dst_region, 0.0) + imported

    for district, rid in district_to_region.items():
        if district >= len(annual_heat_by_d):
            raise IndexError(
                f"annual_heat_by_d missing entry for district index {district}; "
                f"len(annual_heat_by_d)={len(annual_heat_by_d)}"
            )
        annual_heat = float(annual_heat_by_d[district])
        if annual_heat <= 0.0:
            continue

        share_override = min_pipe_import_share_targets.get(rid)
        if share_override is not None:
            target = annual_heat * share_override
            if target > 0.0:
                historical_pipe_import_targets_mwh[rid] = target
            continue

        hist_import = float(historical_pipe_import_by_region.get(rid, 0.0) or 0.0)
        if hist_import > 0.0:
            historical_pipe_import_targets_mwh[rid] = hist_import
            continue

        # Fallback: if Fernwärme exists historically (via heat exchanger output),
        # enforce a small mandatory inter-district import floor.
        district_metrics = region_metrics.get(rid, {})
        if not isinstance(district_metrics, dict):
            raise TypeError(f"region_technology_metrics[{rid}] must be a dict")
        hx_key = f"heat_exchanger_D{district}"
        hx_metrics = district_metrics.get(hx_key, {})
        if not isinstance(hx_metrics, dict):
            raise TypeError(f"region_technology_metrics[{rid}][{hx_key}] must be a dict")
        hx_output = float(hx_metrics.get("initial_energy_output", 0.0) or 0.0)
        if hx_output > 0.0:
            target = hx_output * 0.001
            if target > 0.0:
                historical_pipe_import_targets_mwh[rid] = target

    if retain_existing_output_factor is None:
        retain_factor: float | None = None
    else:
        retain_factor = max(0.0, float(retain_existing_output_factor))

    retain_schedule: Optional[List[float]] = None
    if retain_existing_output_schedule:
        sanitized: List[float] = []
        for entry in retain_existing_output_schedule:
            val = float(entry)
            if math.isnan(val):
                raise ValueError("retain_existing_output_schedule contains NaN")
            sanitized.append(max(0.0, val))
        if sanitized:
            retain_schedule = sanitized

    if lockout_years is None:
        lockout_years = 2
    lockout_years = max(0, int(lockout_years))

    lockout_until_year = scenario_years[0] + lockout_years if scenario_years else None

    def _import_retain_factor(year: int, year_index: int) -> float:
        if lockout_until_year is not None and year < lockout_until_year:
            return 1.0
        if retain_schedule:
            if year_index < len(retain_schedule):
                return max(0.0, float(retain_schedule[year_index]))
            return 0.5
        if retain_factor is not None:
            return max(0.0, float(retain_factor))
        return 1.0

    def _format_year_profile(pairs: List[Tuple[int, float]]) -> str | None:
        if not pairs:
            return None
        segments: list[str] = []
        for year, value in pairs:
            year_i = int(year)
            value_f = float(value)
            segments.append(f"{year_i} {value_f:.10g}")
        return "[" + " ; ".join(segments) + "]"
    
    logger.debug(
        "retain_existing_output_factor=%s -> retain_factor=%s",
        retain_existing_output_factor,
        retain_factor,
    )

    total_min_dhn = float(sum(min_dhn_targets.values())) if min_dhn_targets else 0.0
    total_min_heat_grid = float(sum(min_heat_grid_targets.values())) if min_heat_grid_targets else 0.0

    for tech in sel_list:
        cin = _canon_co(tech.commodity_in)
        if tech.commodity_out == demand_commodity:
            pass
        else:
            cout = _canon_co(tech.commodity_out)
            if cout not in commodity_list:
                commodity_list.append(cout)
        if cin not in commodity_list:
            commodity_list.append(cin)
    produced_commodities = {_canon_co(t.commodity_out) for t in sel_list}
    required_inputs = {_canon_co(t.commodity_in) for t in sel_list}
    protected_commodities = set(heat_names + grid_names + [grid_hub_name])
    supply_candidates = sorted(
        c
        for c in required_inputs - produced_commodities
        if c not in ("", "Dummy")
        and c.lower() != "electricity"
        and c not in protected_commodities
        and not c.startswith("district_heat_")
    )

    for commodity in supply_candidates:
        if commodity not in commodity_list:
            commodity_list.append(commodity)
    conv_procs: List[str] = ["GridExportElec"]
    if len(districts) == 1:
        conv_procs.append("HeatDemand")
    else:
        conv_procs += [f"HeatDemand_D{i}" for i in districts]
        conv_procs += [f"Pipe_D{i}_D{j}" for (i, j) in pipe_pairs]
    for tech in sel_list:
        base_name, tech_district = _split_base_and_district(tech.name)
        if base_name in HEAT_EXCHANGER_BASE_NAMES:
            if len(districts) == 1:
                name = "HeatExchanger"
                if name not in conv_procs:
                    conv_procs.append(name)
            else:
                target_ds = [tech_district] if tech_district is not None else list(districts)
                for d in target_ds:
                    name = f"HeatExchanger_D{d}"
                    if name not in conv_procs:
                        conv_procs.append(name)
            continue

        is_central = _is_central_heat_supply(tech.name)
        if is_central and tech_district is None and len(districts) > 1:
            for d in districts:
                name = f"{tech.name}_D{d}"
                if name not in conv_procs:
                    conv_procs.append(name)
            continue

        if len(districts) == 1 or tech.commodity_out != demand_commodity:
            name = f"{tech.name}"
            if name not in conv_procs:
                conv_procs.append(name)
            continue

        if tech_district is not None:
            name = tech.name
            if name not in conv_procs:
                conv_procs.append(name)
            continue

        for i in districts:
            name = f"{tech.name}_D{i}"
            if name not in conv_procs:
                conv_procs.append(name)

    extra_supply_rows: List[dict[str, Any]] = []
    supply_price_defaults = {k.lower(): float(v) for k, v in supply_prices.items()}
    for commodity in supply_candidates:
        if commodity not in commodity_list:
            commodity_list.append(commodity)
        cp_name = f"{commodity.capitalize()}Supply"
        if cp_name not in conv_procs:
            conv_procs.append(cp_name)
        commodity_key = commodity.lower()
        if commodity_key not in supply_price_defaults:
            raise ValueError(
                f"Missing supply price for commodity '{commodity}' in commodity configuration "
                "(supply_prices_eur_per_mwh)."
            )
        price = float(supply_price_defaults[commodity_key])
        extra_supply_rows.append(
            {
                "conversion_process_name": cp_name,
                "commodity_in": "Dummy",
                "commodity_out": commodity,
                "scenario": scenario_name,
                "efficiency": 1.0,
                "technical_availability": 1.0,
                "max_eout": UNBOUNDED_ENERGY,
                "cap_max": UNBOUNDED_CAP,
                "opex_cost_energy": price,
            }
        )
    commodity_df = pd.DataFrame(
        [{"commodity_name": c, "order": idx + 1, "color": ""} for idx, c in enumerate(commodity_list)],
        columns=["commodity_name", "order", "color"],
    )

    convproc_df = pd.DataFrame(
        [{"conversion_process_name": t, "order": i + 1, "color": ""} for i, t in enumerate(conv_procs)],
        columns=["conversion_process_name", "order", "color"],
    )
    cs_rows: List[dict] = []
    cs_rows.append(
        {
            "conversion_process_name": "GridExportElec",
            "commodity_in": "Electricity",
            "commodity_out": "Dummy",
            "scenario": scenario_name,
            "efficiency": 1.0,
            "technical_availability": 1.0,
            "max_eout": UNBOUNDED_ENERGY,
            "cap_max": UNBOUNDED_CAP,
            "opex_cost_energy": float(export_price_eur_per_mwh),
        }
    )
    cs_rows.extend(extra_supply_rows)
    for idx, i in enumerate(districts):
        heat_comm = heat_names[i]
        cp_name = "HeatDemand" if len(districts) == 1 else f"HeatDemand_D{i}"
        if i >= len(annual_heat_by_d):
            raise IndexError(
                f"annual_heat_by_d missing entry for district index {i}; "
                f"len(annual_heat_by_d)={len(annual_heat_by_d)}"
            )
        min_eout = float(annual_heat_by_d[i])
        cs_rows.append(
            {
                "conversion_process_name": cp_name,
                "commodity_in": heat_comm,
                "commodity_out": "Dummy",
                "scenario": scenario_name,
                "efficiency": 1.0,
                "technical_availability": 1.0,
                "min_eout": min_eout,
                "output_profile": demand_profile_name,
            }
        )

    incoming_pipe_pairs_by_district: dict[int, list[tuple[int, int]]] = {}
    for (i, j) in pipe_pairs:
        incoming_pipe_pairs_by_district.setdefault(int(i), []).append((int(i), int(j)))

    pipe_min_eout_by_pair: dict[tuple[int, int], Any] = {}

    def _historical_hx_output_for_district(district_id: int) -> float:
        rid = district_to_region.get(district_id, district_id)
        district_metrics = region_metrics.get(rid, {})
        if not isinstance(district_metrics, dict):
            raise TypeError(f"region_technology_metrics[{rid}] must be a dict")
        hx_key = f"heat_exchanger_D{district_id}"
        hx_metrics = district_metrics.get(hx_key, {})
        if not isinstance(hx_metrics, dict):
            raise TypeError(f"region_technology_metrics[{rid}][{hx_key}] must be a dict")
        return float(hx_metrics.get("initial_energy_output", 0.0) or 0.0)

    for district in districts:
        rid = district_to_region.get(district, district)
        min_import_target = float(historical_pipe_import_targets_mwh.get(rid, 0.0) or 0.0)
        if min_import_target <= 0.0:
            continue
        incoming_pairs = incoming_pipe_pairs_by_district.get(int(district), [])
        if not incoming_pairs:
            raise ValueError(
                f"Historical DHN import target > 0 for district D{district} but no incoming Pipe_D{district}_D* connection is available"
            )

        # Apply target on one strongest incoming connection to avoid over-constraining all links.
        anchor_pair = max(
            incoming_pairs,
            key=lambda pair: _historical_hx_output_for_district(int(pair[1])),
        )
        per_pipe_target = float(min_import_target)
        if scenario_years:
            min_pairs: list[tuple[int, float]] = []
            for idx, year in enumerate(scenario_years):
                min_pairs.append((int(year), per_pipe_target * _import_retain_factor(int(year), idx)))
            min_profile = _format_year_profile(min_pairs)
            pipe_min_eout_by_pair[anchor_pair] = min_profile if min_profile else per_pipe_target
        else:
            pipe_min_eout_by_pair[anchor_pair] = per_pipe_target

    for (i, j) in pipe_pairs:
        # Pipe_Di_Dj: import into district Di from district Dj.
        src_idx = district_index.get(j)
        if src_idx is None:
            raise KeyError(f"Missing district index for source district {j}")
        dst_idx = district_index.get(i)
        if dst_idx is None:
            raise KeyError(f"Missing district index for target district {i}")
        src_comm = district_heat_out_names.get(districts[src_idx], f"district_heat_out_D{districts[src_idx]}")
        dst_comm = district_heat_in_names.get(districts[dst_idx], f"district_heat_in_D{districts[dst_idx]}")
        pair_specs = pipe_specs_by_pair.get((int(i), int(j)), {})
        pipe_capex_base = float(pair_specs.get("pipe_capex_base_eur", 0.0) or 0.0)
        row = {
            "conversion_process_name": f"Pipe_D{i}_D{j}",
            "commodity_in": src_comm,
            "commodity_out": dst_comm,
            "scenario": scenario_name,
            "efficiency": pipe_eff,
            "technical_availability": 1.0,
            "technical_lifetime": int(pipe_lifetime_value),
            "cap_max": float(pipe_cap_max_value),
            "max_eout": UNBOUNDED_ENERGY,
            "opex_cost_energy": float(pipe_opex_value),
            "capex_cost_power": float(pipe_capex_value),
            "capex_cost_base": pipe_capex_base,
        }
        min_eout_value = pipe_min_eout_by_pair.get((int(i), int(j)))
        if min_eout_value is not None:
            row["min_eout"] = min_eout_value
        cs_rows.append(row)

    builder = _ConversionRowsBuilder(
        scenario_name=scenario_name,
        scenario_years=scenario_years,
        demand_commodity=demand_commodity,
        districts=districts,
        district_index=district_index,
        heat_names=heat_names,
        district_heat_in_names=district_heat_in_names,
        district_heat_out_names=district_heat_out_names,
        min_dhn_targets=min_dhn_targets,
        min_heat_grid_targets=min_heat_grid_targets,
        min_central_cap_targets=min_central_cap_targets,
        min_central_cap_totals=min_central_cap_totals,
        min_central_cap_totals_by_co=min_central_cap_totals_by_co,
        district_to_region=district_to_region,
        region_metrics=region_metrics,
        total_metrics=total_metrics,
        retain_factor=retain_factor,
        retain_years_factor=retain_existing_output_years_factor,
        retain_schedule=retain_schedule,
        lockout_years=lockout_years,
        elec_price_eur_per_mwh=elec_price_eur_per_mwh,
        local_dhn_capex_base_by_district=local_dhn_capex_base_by_district,
    )

    cs_rows.extend(builder.build_rows(sel_list))
    convsubproc_df = _convsubproc_dataframe(cs_rows)
    _write_techmap_workbook(
        xlsx,
        units_df=units_df,
        scenario_df=scenario_df,
        tss_df=tss_df,
        commodity_df=commodity_df,
        convproc_df=convproc_df,
        convsubproc_df=convsubproc_df,
    )
    _log_techmap_stats(
        commodity_count=len(commodity_list),
        convproc_count=len(conv_procs),
        convsubproc_rows=len(convsubproc_df),
    )

def write_cesm_inputs_from_energy_system(
    energy_system: EnergySystem,
    scenario: Scenario,
    *,
    workdir: PathLike,
    model_name: str,
    scenario_name: str,
    tss_name: str,
    demand_name: str = "residential_heat",
    technology_registry: TechnologyRegistry | None = None,
    polygons_gdf: Optional[gpd.GeoDataFrame] = None,
    retain_existing_output_factor: float | None = None,
    retain_existing_output_years_factor: float | None = None,
    retain_existing_output_schedule: Optional[List[float]] = None,
    **kwargs: Any,
) -> None:
    """Generate CESM inputs from an EnergySystem."""

    resolved_schedule = _resolve_retain_schedule(
        explicit_schedule=retain_existing_output_schedule,
        scenario=scenario,
    )
    if resolved_schedule is None:
        raise ValueError("retain_existing_output_schedule must be provided via scenario or argument")

    om_ctx = build_om_from_es(energy_system, scenario, demand_name=demand_name)
    grid_prices, supply_prices = _commodity_config_from_energy_system(energy_system)
    logger.debug(
        "commodity_config extracted: grid_prices=%s (%s), supply_prices=%s (%s)",
        grid_prices,
        type(grid_prices).__name__,
        supply_prices,
        type(supply_prices).__name__,
    )
    if polygons_gdf is None:
        polygons_gdf = _polygons_from_energy_system(energy_system)
    _write_cesm_inputs_from_om(
        om_ctx,
        workdir=workdir,
        model_name=model_name,
        scenario_name=scenario_name,
        tss_name=tss_name,
        data_dir=getattr(energy_system, "data_dir", None),
        grid_prices=grid_prices,
        supply_prices=supply_prices,
        start_year=getattr(scenario, "start_year", None),
        end_year=getattr(scenario, "end_year", None),
        year_gap=getattr(scenario, "year_gap", None),
        discount_rate=getattr(scenario, "discount_rate", None),
        polygons_gdf=polygons_gdf,
        street_segments_gdf=getattr(energy_system, "district_street_segments_gdf", None),
        technology_registry=technology_registry,
        retain_existing_output_factor=retain_existing_output_factor,
        retain_existing_output_years_factor=retain_existing_output_years_factor,
        retain_existing_output_schedule=resolved_schedule,
        **kwargs,
    )

# ====================================================================================
# Runner CLI
# ====================================================================================

def _validate_cesm_paths(cesm_dir: Path, model_name: str) -> tuple[Path, Path, Path]:
    data_dir = cesm_dir / "Data"
    techmap_dir = data_dir / "Techmap"
    ts_dir = data_dir / "TimeSeries"
    runs_dir = cesm_dir / "Runs"
    workbook = techmap_dir / f"{model_name}.xlsx"
    required = [
        ("CESM folder", cesm_dir),
        ("CESM core folder", cesm_dir / "core"),
        ("CESM Data directory", data_dir),
        ("Techmap directory", techmap_dir),
    ]
    missing: list[tuple[str, Path]] = [(desc, path) for desc, path in required if not path.exists()]
    if not workbook.exists():
        missing.append(("Techmap workbook", workbook))
    if missing:
        for desc, path in missing:
            logger.error("Missing %s: %s", desc, path)
        raise FileNotFoundError("Missing CESM resources")
    return techmap_dir, ts_dir, runs_dir

def main() -> int: 
    import sys as _sys
    cwd = Path.cwd().resolve()
    if (cwd / "core").exists():
        cesm_root = cwd
    elif (cwd / "CESM" / "core").exists():
        cesm_root = (cwd / "CESM").resolve()
    else:
        cesm_root = None
    if cesm_root:
        paths_to_add = {str(cesm_root), str(cesm_root / "core"), str(cesm_root.parent)}
        for p in list(paths_to_add):
            if p not in _sys.path:
                _sys.path.insert(0, p)
  
    from cesm.core.input_parser import Parser  # type: ignore
    from cesm.core.model import Model  # type: ignore

    ap = argparse.ArgumentParser(description="Invoke CESM run using unified plugin.")
    ap.add_argument("--workdir", default=".", help="Project root that contains the CESM/ folder (default: .)")
    ap.add_argument("-m", "--model", required=True, help="Techmap XLSX name (without .xlsx)")
    ap.add_argument("-s", "--scenario", required=True, help="Scenario name as in the XLSX Scenario sheet")
    args = ap.parse_args()
    root = Path(args.workdir).resolve()
    cesm_dir = root / "CESM" if (root / "CESM" / "core").exists() else root
    model_name = args.model
    scenario_name = args.scenario
    run_name = f"{model_name}-{scenario_name}"
    techmap_dir, ts_dir, runs_dir = _validate_cesm_paths(cesm_dir, model_name)
    db_dir = runs_dir / run_name
    db_path = db_dir / "db.sqlite"
    ts_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(exist_ok=True)
    db_dir.mkdir(exist_ok=True)
    conn = sqlite3.connect(":memory:")
    parser = Parser(model_name, techmap_dir_path=techmap_dir, ts_dir_path=ts_dir, db_conn=conn, scenario=scenario_name)
    parser.parse()
    model = Model(conn=conn)
    model.solve()
    model.save_output()
    if db_path.exists():
        db_path.unlink()
        logger.info("Deleted previous DB: %s", db_path)
    disk = sqlite3.connect(str(db_path))
    conn.backup(disk)
    disk.close()
    conn.close()
    logger.info("DB written: %s", db_path)
    return 0

if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
