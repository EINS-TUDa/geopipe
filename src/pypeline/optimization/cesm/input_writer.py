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
full :class:`~pypeline.energy_system.core.EnergySystem` and
:class:`~pypeline.energy_system.core.Scenario`, builds the context
internally, then delegates to :func:`_write_cesm_inputs_from_om`.
"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from pypeline.energy_system.core import EnergySystem, Scenario
from pypeline.optimization.cesm.io_utils import (
    _canon_co,
    _convsubproc_dataframe,
    _format_year_profile,
    _profile_to_map_and_scalar,
    _value_for_year,
    commodity_config_from_energy_system as _commodity_config_from_energy_system,
    resolve_retain_schedule as _resolve_retain_schedule,
    _write_demand_profile,
    _write_techmap_workbook,
    _units_df,
    _scenario_df,
    _tss_df,
)
from pypeline.energy_technology.technology import (
    Technology,
    split_base_and_district as _split_base_and_district,
)
from pypeline.energy_technology.technology_registry import TechnologyRegistry, get_default_technology_registry
from pypeline.optimization.cesm.conversion_rows import (_ConversionRowsBuilder)
from pypeline.optimization.optimization_context import (
    OptimizationContext,
    build_optimization_context,
    enforce_historical_fernwaerme_dependency,
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
    polygons_gdf: Any | None = None,
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
    heat_names: List[str]
    pipe_pairs: List[Tuple[int, int]] = []
    pipe_specs_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
    free_pipe_pairs: set[tuple[int, int]] = set()
    base_heat_name = heat_commodity_base or optimization_context.commodity
    districts = list(range(len(om_regions)))
    if len(districts) == 1:
        heat_names = [f"{base_heat_name}"]
    else:
        heat_names = [f"{base_heat_name}_D{i}" for i in districts]
        if inter_district_pipe_specs is not None:
            pipe_specs_by_pair = {
                (int(i), int(j)): dict(specs or {})
                for (i, j), specs in inter_district_pipe_specs.items()
            }
            free_pipe_pairs = {
                (int(i), int(j))
                for (i, j), specs in pipe_specs_by_pair.items()
                if bool((specs or {}).get("is_free", False))
            }
            pipe_pairs = sorted(
                (int(i), int(j))
                for (i, j) in inter_district_pipe_specs.keys()
            )

    district_index = {d: idx for idx, d in enumerate(districts)}
    if len(districts) <= 1:
        grid_hub_name = f"{base_heat_name}_Grid"
        grid_names = [grid_hub_name]
    else:
        grid_hub_name = f"{base_heat_name}_GridHub"
        grid_names = [f"{base_heat_name}_Grid_D{d}" for d in districts]

    district_heat_in_names = {d: f"district_heat_in_D{d}" for d in districts}
    district_heat_out_names = {d: f"district_heat_out_D{d}" for d in districts}


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
    if not annual_heat_by_d:
        annual_heat_by_d = [0.0] * len(districts)
    if len(annual_heat_by_d) != len(districts):
        raise ValueError(
            f"Annual heat demand count ({len(annual_heat_by_d)}) does not match "
            f"number of districts ({len(districts)})."
        )
    annual_heat_mwh_total = float(sum(annual_heat_by_d))
    if annual_heat_mwh_total < 0.0:
        raise ValueError("Annual heat demand is negative.")
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

    historical_exchanger_targets_mwh = optimization_context.historical_exchanger_targets_mwh
    if historical_exchanger_targets_mwh is None:
        historical_exchanger_targets_mwh = enforce_historical_fernwaerme_dependency(
            optimization_context,
            end_year=end_year_int,
            year_gap=year_step_int,
        )

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

    if "min_pipe_import_share_by_region" in constraints_raw:
        raise ValueError(
            "Constraint 'min_pipe_import_share_by_region' is no longer supported. "
            "Use exchanger-level constraints ('min_dhn_throughput_mwh' and/or historical exchanger metrics) instead."
        )

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

    commodity_activation_year_raw = constraints_raw.get("commodity_activation_year", {}) if constraints_raw else {}
    if commodity_activation_year_raw and not isinstance(commodity_activation_year_raw, dict):
        raise ValueError("commodity_activation_year constraint must be a mapping of commodity names to years")
    commodity_activation_year: dict[str, int] = {}
    for commodity_raw, year_raw in (commodity_activation_year_raw or {}).items():
        commodity = str(commodity_raw).strip().lower()
        if not commodity:
            continue
        try:
            activation_year = int(year_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"commodity_activation_year['{commodity_raw}'] must be an integer year") from exc
        commodity_activation_year[commodity] = max(start_year_int, activation_year)

    technology_activation_year_raw = constraints_raw.get("technology_activation_year", {}) if constraints_raw else {}
    if technology_activation_year_raw and not isinstance(technology_activation_year_raw, dict):
        raise ValueError("technology_activation_year constraint must be a mapping of technology names to years")
    technology_activation_year: dict[str, int] = {}
    for technology_raw, year_raw in (technology_activation_year_raw or {}).items():
        technology = str(technology_raw).strip().lower()
        if not technology:
            continue
        try:
            activation_year = int(year_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"technology_activation_year['{technology_raw}'] must be an integer year") from exc
        technology_activation_year[technology] = max(start_year_int, activation_year)

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

    primary_heat_exchanger = "heat_exchanger"

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

    logger.debug(
        "retain_existing_output_factor=%s -> retain_factor=%s",
        retain_existing_output_factor,
        retain_factor,
    )

    _hx_base_name = primary_heat_exchanger

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
        if base_name == _hx_base_name:
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
            "cap_max": None,
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
        is_free_pair = bool((pair_specs or {}).get("is_free", False))
        pipe_capex_base = None if is_free_pair else float(pair_specs.get("pipe_capex_base_eur", 0.0) or 0.0)
        fixed_free_pipe_capacity = float(pipe_cap_max_value) if is_free_pair else None
        row = {
            "conversion_process_name": f"Pipe_D{i}_D{j}",
            "commodity_in": src_comm,
            "commodity_out": dst_comm,
            "scenario": scenario_name,
            "efficiency": pipe_eff,
            "technical_availability": 1.0,
            "technical_lifetime": int(pipe_lifetime_value),
            "cap_max": float(pipe_cap_max_value),
            "cap_res_min": fixed_free_pipe_capacity,
            "cap_res_max": fixed_free_pipe_capacity,
            "opex_cost_energy": 0.0 if is_free_pair else float(pipe_opex_value),
            "capex_cost_power": 0.0 if is_free_pair else float(pipe_capex_value),
            "capex_cost_base": pipe_capex_base,
        }
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
        exchanger_throughput_targets=historical_exchanger_targets_mwh,
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

    def _technology_activation_year_for_row(row: dict[str, Any]) -> int | None:
        if not technology_activation_year:
            return None
        cp_raw = str(row.get("conversion_process_name", "") or "").strip()
        if not cp_raw:
            return None
        cp = cp_raw.lower()

        years: list[int] = []
        direct = technology_activation_year.get(cp)
        if direct is not None:
            years.append(int(direct))

        # Allow base-name matching for district clones, e.g. ind_oil_boiler -> ind_oil_boiler_D0.
        base, district = _split_base_and_district(cp_raw)
        if district is not None:
            base_year = technology_activation_year.get(base.lower())
            if base_year is not None:
                years.append(int(base_year))

        if not years:
            return None
        return max(years)

    def _row_activation_year(row: dict[str, Any]) -> int | None:
        cin = str(row.get("commodity_in", "") or "").strip().lower()
        cout = str(row.get("commodity_out", "") or "").strip().lower()
        years: list[int] = []
        if cin in commodity_activation_year:
            years.append(int(commodity_activation_year[cin]))
        if cout in commodity_activation_year:
            years.append(int(commodity_activation_year[cout]))
        tech_year = _technology_activation_year_for_row(row)
        if tech_year is not None:
            years.append(int(tech_year))
        if not years:
            return None
        # For rows with multiple applicable constraints, apply the stricter
        # (later) activation year.
        return max(years)

    for row in cs_rows:
        activation_year = _row_activation_year(row)
        if activation_year is None:
            continue

        cap_res_min_map, cap_res_min_scalar = _profile_to_map_and_scalar(
            row.get("cap_res_min"),
            ignore_invalid=True,
        )
        cap_res_max_map, cap_res_max_scalar = _profile_to_map_and_scalar(
            row.get("cap_res_max"),
            ignore_invalid=True,
        )

        def _residual_ceiling_for_year(year_i: int) -> float:
            res_max = _value_for_year(cap_res_max_map, cap_res_max_scalar, year_i)
            res_min = _value_for_year(cap_res_min_map, cap_res_min_scalar, year_i)
            if res_max is None and res_min is None:
                return 0.0
            vals = [float(v) for v in (res_max, res_min) if v is not None]
            return max(vals) if vals else 0.0

        for column in ("cap_max", "cap_min"):
            existing = row.get(column)
            if existing is None and column != "cap_max":
                continue

            profile_map, scalar = _profile_to_map_and_scalar(
                existing,
                ignore_invalid=True,
            )
            pairs: list[tuple[int, float]] = []
            for year in scenario_years:
                year_i = int(year)
                if year_i < activation_year:
                    if column == "cap_max":
                        # Block new build while still allowing already-existing residual capacity.
                        pairs.append((year_i, _residual_ceiling_for_year(year_i)))
                    else:
                        pairs.append((year_i, 0.0))
                    continue

                base_value = _value_for_year(profile_map, scalar, year_i)
                if base_value is None:
                    if column == "cap_max":
                        # Keep post-activation rows effectively unbounded when cap_max is unspecified.
                        # No NaN here: CESM profile interpolation drops NaN and  backfills zero values into later years.
                        pairs.append((year_i, 100000000.0))
                    continue
                pairs.append((year_i, float(base_value)))

            if pairs:
                prof = _format_year_profile(pairs)
                if prof is not None:
                    row[column] = prof

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
