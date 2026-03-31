"""CESM input file writing: techmap XLSX and timeseries TXT.

This module converts an :class:`~pypeline.optimization.optimization_context.OptimizationContext`
(the backend-agnostic intermediate representation) into the two files the CESM
solver needs:

* An Excel **techmap** workbook (scenario, commodities, conversion processes,
  conversion sub-processes).
* A plain-text **timeseries** file with the hourly demand profile.

Typical call chain::

    OptimizationContext                          ← optimization_context.py
        ↓  _write_cesm_inputs_from_om()
    XLSX + TXT on disk                           ← this file
        ↓  _ConversionRowsBuilder.build_rows()  ← conversion_rows.py
    ConversionSubProcess rows (one per tech / district combination)

Public entry point: :func:`write_cesm_inputs_from_energy_system` — takes a
full :class:`~pypeline.energy_system.energy_system.EnergySystem` and
:class:`~pypeline.energy_system.scenario.Scenario`, builds the context
internally, then delegates to :func:`_write_cesm_inputs_from_om`.
"""
from __future__ import annotations
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import geopandas as gpd

from pypeline.energy_system.energy_system import EnergySystem
from pypeline.energy_system.io_utils import (
    _canon_co,
    _convsubproc_dataframe,
    commodity_config_from_energy_system as _commodity_config_from_energy_system,
    resolve_retain_schedule as _resolve_retain_schedule,
    polygons_from_energy_system as _polygons_from_energy_system,
    _write_demand_profile,
    _write_techmap_workbook,
    _units_df,
    _scenario_df,
    _tss_df,
)
from pypeline.energy_system.scenario import Scenario
from pypeline.energy_technology.technology import Technology
from pypeline.energy_technology.technology_registry import TechnologyRegistry, get_default_technology_registry
from pypeline.optimization.cesm.conversion_rows import (
    UNBOUNDED_CAP,
    UNBOUNDED_ENERGY,
    _ConversionRowsBuilder,
)
from pypeline.optimization.optimization_context import (
    OptimizationContext,
    build_optimization_context,
)
from pypeline.validation import (
    ensure_tss_indices,
    normalize_profile_for_tss,
    sanitize_price_map,
    to_int_id,
)

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


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


def _write_cesm_inputs_from_optimization_context(
        optimization_context: OptimizationContext,
        *,
        workdir: PathLike,
        model_name: str,
        scenario_name: str,
        tss_name: str,
        polygons_path: Optional[PathLike] = None,
        polygons_gdf: Optional[gpd.GeoDataFrame] = None,
        inter_district_pipe_specs: Optional[dict] = None,
        local_dhn_costs: Optional[dict] = None,
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
    om_regions = list(optimization_context.regions)

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

    om_profile = optimization_context.demand_profile
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
    base_heat_name = heat_commodity_base or optimization_context.commodity
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
        if inter_district_pipe_specs is not None:
            pipe_specs_by_pair = {
                (int(i), int(j)): dict(specs or {})
                for (i, j), specs in inter_district_pipe_specs.items()
            }
            pipe_pairs = sorted((int(i), int(j)) for (i, j) in inter_district_pipe_specs.keys())

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
        annual_map = optimization_context.annual_demand
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
        om_techs = optimization_context.technologies
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

    local_dhn_capex_base_by_district: dict[int, float] = {}
    if local_dhn_costs is not None:
        local_dhn_capex_base_by_district = {
            int(district_id): float(payload["local_grid_capex_base_eur"])
            for district_id, payload in local_dhn_costs.items()
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

    constraints_raw = optimization_context.constraints or {}
    if constraints_raw and not isinstance(constraints_raw, dict):
        raise ValueError("constraints must be provided as a mapping")

    demand_commodity = optimization_context.commodity or "residential_heat"

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

    om_region_ids = list(optimization_context.regions)
    district_to_region: dict[int, int] = {}
    for idx, district_id in enumerate(districts):
        if idx >= len(om_region_ids):
            raise ValueError(f"Missing region id for district {district_id}")
        raw_rid = om_region_ids[idx]
        district_to_region[district_id] = to_int_id(raw_rid)

    region_metrics = optimization_context.region_technology_metrics or {}
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
            historical_pipe_import_by_region[dst_region] = historical_pipe_import_by_region.get(dst_region,
                                                                                                0.0) + imported

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

    from pypeline.energy_system.energy_system import HEAT_EXCHANGER_NAMES as _HX_NAMES
    _hx_base_names: tuple[str, ...] = _HX_NAMES

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
        from pypeline.energy_technology.technology import split_base_and_district as _sbd, \
            is_central_heat_supply as _ichs
        base_name, tech_district = _sbd(tech.name)
        if base_name in _hx_base_names:
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

        is_central = _ichs(tech.name)
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

    om_ctx = build_optimization_context(energy_system, scenario, demand_name=demand_name)
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
    _write_cesm_inputs_from_optimization_context(
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
        inter_district_pipe_specs=getattr(energy_system, "inter_district_pipe_specs", None),
        local_dhn_costs={
            region.id: {"local_grid_capex_base_eur": region.local_dhn_capex_base_eur}
            for region in energy_system.regions
            if region.local_dhn_capex_base_eur is not None
        },
        technology_registry=technology_registry,
        retain_existing_output_factor=retain_existing_output_factor,
        retain_existing_output_years_factor=retain_existing_output_years_factor,
        retain_existing_output_schedule=resolved_schedule,
        **kwargs,
    )
