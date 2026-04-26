"""CESM input file writing: techmap XLSX and timeseries TXT.

Converts an :class:`~pypeline.energy_system.core.EnergySystem` and
:class:`~pypeline.energy_system.core.Scenario` into the two files the CESM
solver needs:

* An Excel **techmap** workbook (scenario, commodities, conversion processes,
  conversion sub-processes).
* A plain-text **timeseries** file with the hourly demand profile.

Typical call chain::

    EnergySystem + Scenario
        ↓  _write_cesm_inputs()
    XLSX + TXT on disk                           ← this file
        ↓  _ConversionRowsBuilder.build_rows()  ← conversion_rows.py
    ConversionSubProcess rows (one per tech / district combination)

Public entry point: :func:`write_cesm_inputs_from_energy_system`.
"""
from __future__ import annotations
import logging
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Union

import pandas as pd

from pypeline.energy_system.rule_book import DHN_TECH_NAMES
from pypeline.optimization.cesm.io_utils import (
    _canon_co,
    _convsubproc_dataframe,
    _format_year_profile,
    _profile_to_map_and_scalar,
    _value_for_year,
    _write_techmap_workbook,
    _units_df,
    _tss_df,
)
from pypeline.energy_system.imports import Imports
from pypeline.energy_technology.technology import (
    Technology)
from pypeline.optimization.cesm.conversion_rows import _ConversionRowsBuilder
from pypeline.optimization.cesm.conversion_sub_process import ConversionSubProcess
from pypeline.optimization.resolved_system import ResolvedSystem
from pypeline.validation import to_int_id

logger = logging.getLogger(__name__)
PathLike = Union[str, Path]


# ---------------------------------------------------------------------------
# IO paths
# ---------------------------------------------------------------------------

@dataclass
class _CesmIOPaths:
    techmap_dir: Path
    timeseries_dir: Path
    xlsx_path: Path
    tss_file: Path


def _prepare_io_paths(techmap_dir: Path, timeseries_dir: Path, model_name: str, tss_name: str) -> _CesmIOPaths:
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


def _imports_to_supply_csps(
    imports: list[Imports],
    supply_candidates: list[str],
    scenario_name: str,
) -> list[ConversionSubProcess]:
    """Build a ConversionSubProcess for each supply candidate using its matching Imports entry."""
    import_by_commodity = {imp.commodity_out.lower(): imp for imp in imports}
    csps: list[ConversionSubProcess] = []
    for commodity in supply_candidates:
        imp = import_by_commodity.get(commodity.lower())
        if imp is None:
            raise ValueError(
                f"Missing supply price for commodity '{commodity}' in commodity configuration "
                "(supply_prices_eur_per_mwh)."
            )
        csps.append(ConversionSubProcess(
            conversion_process_name=imp.name,
            commodity_in="Dummy",
            commodity_out=commodity,
            scenario=scenario_name,
            efficiency=1.0,
            technical_availability=1.0,
            max_eout=math.nan,
            cap_max=math.nan,
            opex_cost_energy=imp.price_eur_per_mwh,
        ))
    return csps


# ---------------------------------------------------------------------------
# Naming context
# ---------------------------------------------------------------------------

@dataclass
class _NamingContext:
    demand_profile_name: str
    demand_commodity: str
    base_heat_name: str
    districts: list[int]
    district_index: dict[int, int]
    heat_names: list[str]
    grid_hub_name: str
    grid_names: list[str]
    district_heat_in_names: dict[int, str]
    district_heat_out_names: dict[int, str]


def _build_naming_context(resolved: ResolvedSystem, heat_commodity_base: str | None) -> _NamingContext:
    demand_commodity = resolved.demand_commodity
    base_heat_name = heat_commodity_base or demand_commodity
    districts = list(range(len(resolved.region_ids)))
    district_index = {d: idx for idx, d in enumerate(districts)}

    if len(districts) == 1:
        heat_names = [base_heat_name]
        grid_hub_name = f"{base_heat_name}_Grid"
        grid_names = [grid_hub_name]
    else:
        heat_names = [f"{base_heat_name}_D{i}" for i in districts]
        grid_hub_name = f"{base_heat_name}_GridHub"
        grid_names = [f"{base_heat_name}_Grid_D{d}" for d in districts]

    return _NamingContext(
        demand_profile_name="HeatDemandProfile",
        demand_commodity=demand_commodity,
        base_heat_name=base_heat_name,
        districts=districts,
        district_index=district_index,
        heat_names=heat_names,
        grid_hub_name=grid_hub_name,
        grid_names=grid_names,
        district_heat_in_names={d: f"district_heat_in_D{d}" for d in districts},
        district_heat_out_names={d: f"district_heat_out_D{d}" for d in districts},
    )


# ---------------------------------------------------------------------------
# Technology list
# ---------------------------------------------------------------------------

def _resolve_tech_list(
    resolved: ResolvedSystem,
    selected_techs: list[Technology] | TechnologyRegistry | None,
) -> list[Technology]:
    if isinstance(selected_techs, TechnologyRegistry):
        raw: list[Technology] = selected_techs.get_all(return_type="instance")
    elif isinstance(selected_techs, list):
        raw = [t for t in selected_techs if isinstance(t, Technology)]
    else:
        raw = [t for t in resolved.technologies.values() if isinstance(t, Technology)]

    dedup: dict[str, Technology] = {}
    result: list[Technology] = []
    for tech in raw:
        if tech.name not in dedup:
            dedup[tech.name] = tech
            result.append(tech)
    return result


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

@dataclass
class _ParsedConstraints:
    min_dhn_targets: dict[int, float]
    min_heat_grid_targets: dict[int, float]
    min_central_cap_targets: dict[str, float]
    min_central_cap_totals: dict[int, float]
    min_central_cap_totals_by_co: dict[str, dict[int, float]]
    commodity_activation_year: dict[str, int]
    technology_activation_year: dict[str, int]


def _parse_constraints(
    constraints_raw: dict | None,
    *,
    start_year: int,
    demand_commodity: str,
) -> _ParsedConstraints:
    if not constraints_raw:
        return _ParsedConstraints({}, {}, {}, {}, {}, {}, {})

    if "min_pipe_import_share_by_region" in constraints_raw:
        raise ValueError(
            "Constraint 'min_pipe_import_share_by_region' is no longer supported. "
            "Use exchanger-level constraints ('min_dhn_throughput_mwh' and/or historical exchanger metrics) instead."
        )

    def _int_keyed(key: str) -> dict[int, float]:
        raw = constraints_raw.get(key, {})
        if raw and not isinstance(raw, dict):
            raise ValueError(f"{key} constraint must be a mapping of region ids to values")
        return {to_int_id(k): float(v) for k, v in (raw or {}).items() if float(v) > 0}

    min_central_cap_raw = constraints_raw.get("min_central_cap_mw", {})
    min_central_cap_targets: dict[str, float] = {
        str(k): float(v) for k, v in (min_central_cap_raw or {}).items() if float(v) > 0
    }

    min_central_cap_total_by_co_raw = constraints_raw.get("min_central_cap_mw_total_by_commodity", {})
    min_central_cap_totals_by_co: dict[str, dict[int, float]] = {}
    for commodity_raw, per_district in (min_central_cap_total_by_co_raw or {}).items():
        commodity = str(commodity_raw).strip().lower()
        bucket = {to_int_id(k): float(v) for k, v in per_district.items() if float(v) > 0}
        if bucket:
            min_central_cap_totals_by_co[commodity] = bucket

    def _activation_years(key: str, label: str) -> dict[str, int]:
        raw = constraints_raw.get(key, {})
        if raw and not isinstance(raw, dict):
            raise ValueError(f"{key} constraint must be a mapping of {label} names to years")
        result: dict[str, int] = {}
        for name_raw, year_raw in (raw or {}).items():
            name = str(name_raw).strip().lower()
            if not name:
                continue
            try:
                year = int(year_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key}['{name_raw}'] must be an integer year") from exc
            result[name] = max(start_year, year)
        return result

    return _ParsedConstraints(
        min_dhn_targets=_int_keyed("min_dhn_throughput_mwh"),
        min_heat_grid_targets=_int_keyed(f"min_heat_grid_{demand_commodity}"),
        min_central_cap_targets=min_central_cap_targets,
        min_central_cap_totals=_int_keyed("min_central_cap_mw_total"),
        min_central_cap_totals_by_co=min_central_cap_totals_by_co,
        commodity_activation_year=_activation_years("commodity_activation_year", "commodity"),
        technology_activation_year=_activation_years("technology_activation_year", "technology"),
    )


# ---------------------------------------------------------------------------
# District context
# ---------------------------------------------------------------------------

@dataclass
class _DistrictContext:
    district_to_region: dict[int, int]
    region_metrics: dict
    total_metrics: dict[str, dict[str, float]]
    historical_exchanger_targets_mwh: dict[int, float]
    local_dhn_capex_base_by_district: dict[int, float]
    retain_factor: float | None
    retain_schedule: list[float] | None


def _build_district_context(
    resolved: ResolvedSystem,
    naming: _NamingContext,
    *,
    retain_existing_output_factor: float | None,
) -> _DistrictContext:
    om_regions = resolved.region_ids
    districts = naming.districts

    district_to_region: dict[int, int] = {}
    for idx, district_id in enumerate(districts):
        if idx >= len(om_regions):
            raise ValueError(f"Missing region id for district {district_id}")
        district_to_region[district_id] = om_regions[idx]

    region_metrics = resolved.region_metrics
    total_metrics: dict[str, dict[str, float]] = {}
    for rid, tech_map in region_metrics.items():
        if not isinstance(tech_map, dict):
            continue
        for tech_name, metrics in tech_map.items():
            if not isinstance(metrics, dict):
                continue
            agg = total_metrics.setdefault(
                tech_name, {"initial_energy_output": 0.0, "initial_capacity": 0.0}
            )
            agg["initial_energy_output"] += float(metrics["initial_energy_output"] or 0.0)
            agg["initial_capacity"] += float(metrics["initial_capacity"] or 0.0)

    primary_heat_exchanger = DHN_TECH_NAMES[0]
    historical_exchanger_targets_mwh: dict[int, float] = {}
    for district, rid in district_to_region.items():
        district_metrics = region_metrics.get(rid)
        if district_metrics is None:
            continue
        if not isinstance(district_metrics, dict):
            raise TypeError(f"region_technology_metrics[{rid}] must be a dict")
        hx_key = f"{primary_heat_exchanger}_D{district}"
        hx_metrics = district_metrics.get(hx_key)
        if hx_metrics is None:
            continue
        if not isinstance(hx_metrics, dict):
            raise TypeError(f"region_technology_metrics[{rid}][{hx_key}] must be a dict")
        hx_output = float(hx_metrics.get("initial_energy_output", 0.0) or 0.0)
        if hx_output <= 0.0:
            continue
        historical_exchanger_targets_mwh[rid] = max(
            historical_exchanger_targets_mwh.get(rid, 0.0), hx_output
        )

    local_dhn_capex_base_by_district: dict[int, float] = {
        int(did): float(payload["local_grid_capex_base_eur"])
        for did, payload in resolved.local_dhn_costs.items()
    }

    retain_factor: float | None = (
        None if retain_existing_output_factor is None
        else max(0.0, float(retain_existing_output_factor))
    )
    logger.debug(
        "retain_existing_output_factor=%s -> retain_factor=%s",
        retain_existing_output_factor,
        retain_factor,
    )

    validated: list[float] = []
    for entry in (resolved.retain_schedule or []):
        val = float(entry)
        if math.isnan(val):
            raise ValueError("retain_existing_output_schedule contains NaN")
        validated.append(max(0.0, val))
    retain_schedule: list[float] | None = validated if validated else None

    return _DistrictContext(
        district_to_region=district_to_region,
        region_metrics=region_metrics,
        total_metrics=total_metrics,
        historical_exchanger_targets_mwh=historical_exchanger_targets_mwh,
        local_dhn_capex_base_by_district=local_dhn_capex_base_by_district,
        retain_factor=retain_factor,
        retain_schedule=retain_schedule,
    )


# ---------------------------------------------------------------------------
# Techmap lists (commodities, conversion processes, supply rows)
# ---------------------------------------------------------------------------

def _build_techmap_lists(
    naming: _NamingContext,
    sel_list: list[Technology],
    pipe_connections: list,
    scenario_name: str,
) -> tuple[list[str], list[str], list[str]]:
    """Returns (commodity_list, conv_procs, supply_candidates)."""
    districts = naming.districts
    demand_commodity = naming.demand_commodity
    hx_base_name = DHN_TECH_NAMES[0]

    # Commodity list: start with fixed base, then expand for tech commodities
    district_heat_names = (
        [naming.district_heat_in_names[d] for d in districts]
        + [naming.district_heat_out_names[d] for d in districts]
    )
    commodity_list: list[str] = list(dict.fromkeys(
        ["Electricity", "External", "Dummy"]
        + [naming.grid_hub_name]
        + naming.grid_names
        + naming.heat_names
        + district_heat_names
    ))
    for tech in sel_list:
        cin = _canon_co(tech.commodity_in)
        if tech.commodity_out != demand_commodity:
            cout = _canon_co(tech.commodity_out)
            if cout not in commodity_list:
                commodity_list.append(cout)
        if cin not in commodity_list:
            commodity_list.append(cin)

    produced_commodities = {_canon_co(t.commodity_out) for t in sel_list}
    required_inputs = {_canon_co(t.commodity_in) for t in sel_list}
    protected_commodities = set(naming.heat_names + naming.grid_names + [naming.grid_hub_name])
    supply_candidates = sorted(
        c for c in required_inputs - produced_commodities
        if c not in ("", "Dummy")
        and c.lower() != "electricity"
        and c not in protected_commodities
        and not c.startswith("district_heat_")
    )
    for commodity in supply_candidates:
        if commodity not in commodity_list:
            commodity_list.append(commodity)

    # Conversion process list
    if len(districts) == 1:
        conv_procs: list[str] = ["HeatDemand"]
    else:
        conv_procs = [f"HeatDemand_D{i}" for i in districts]
        conv_procs += [f"Pipe_D{pipe.region_id_in}_D{pipe.region_id_out}" for pipe in pipe_connections]

    for tech in sel_list:
        base_name, tech_district = _split_base_and_district(tech.name)
        if base_name == hx_base_name:
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

        if _is_central_heat_supply(tech.name) and tech_district is None and len(districts) > 1:
            for d in districts:
                name = f"{tech.name}_D{d}"
                if name not in conv_procs:
                    conv_procs.append(name)
            continue

        if len(districts) == 1 or tech.commodity_out != demand_commodity:
            if tech.name not in conv_procs:
                conv_procs.append(tech.name)
            continue

        if tech_district is not None:
            if tech.name not in conv_procs:
                conv_procs.append(tech.name)
            continue

        for i in districts:
            name = f"{tech.name}_D{i}"
            if name not in conv_procs:
                conv_procs.append(name)

    # Register supply process names in conv_procs
    for commodity in supply_candidates:
        cp_name = f"{commodity.capitalize()}Supply"
        if cp_name not in conv_procs:
            conv_procs.append(cp_name)

    return commodity_list, conv_procs, supply_candidates


# ---------------------------------------------------------------------------
# Annual heat demand
# ---------------------------------------------------------------------------

def _extract_annual_heat(
    resolved: ResolvedSystem,
    naming: _NamingContext,
    start_year: int,
) -> list[float]:
    om_regions = resolved.region_ids
    districts = naming.districts
    annual_heat_by_d = (
        [float(resolved.annual_demand[rid][start_year]) for rid in om_regions]
        if om_regions else []
    )
    if not annual_heat_by_d:
        annual_heat_by_d = [0.0] * len(districts)
    if len(annual_heat_by_d) != len(districts):
        raise ValueError(
            f"Annual heat demand count ({len(annual_heat_by_d)}) does not match "
            f"number of districts ({len(districts)})."
        )
    if float(sum(annual_heat_by_d)) < 0.0:
        raise ValueError("Annual heat demand is negative.")
    return annual_heat_by_d


# ---------------------------------------------------------------------------
# Conversion subprocess rows
# ---------------------------------------------------------------------------

def _build_cs_rows(
    naming: _NamingContext,
    district_ctx: _DistrictContext,
    constraints: _ParsedConstraints,
    sel_list: list[Technology],
    imports: list[Imports],
    supply_candidates: list[str],
    pipe_connections: list,
    *,
    scenario_name: str,
    scenario_years: list[int],
    annual_heat_by_d: list[float],
    retain_existing_output_years_factor: float | None,
    lockout_until_year: int,
) -> list[dict[str, Any]]:
    districts = naming.districts
    district_index = naming.district_index
    heat_names = naming.heat_names
    district_heat_in_names = naming.district_heat_in_names
    district_heat_out_names = naming.district_heat_out_names

    elec_import = next((imp for imp in imports if imp.commodity_out == "electricity"), None)
    if elec_import is None:
        raise ValueError(
            "No 'electricity' entry found in imports. "
            "Add an imports.yaml entry with commodity_out: electricity."
        )
    elec_price_eur_per_mwh = elec_import.price_eur_per_mwh

    supply_csps = _imports_to_supply_csps(imports, supply_candidates, scenario_name)
    cs_rows: list[dict[str, Any]] = [asdict(csp) for csp in supply_csps]

    for i in districts:
        heat_comm = heat_names[i]
        cp_name = "HeatDemand" if len(districts) == 1 else f"HeatDemand_D{i}"
        if i >= len(annual_heat_by_d):
            raise IndexError(
                f"annual_heat_by_d missing entry for district index {i}; "
                f"len(annual_heat_by_d)={len(annual_heat_by_d)}"
            )
        cs_rows.append({
            "conversion_process_name": cp_name,
            "commodity_in": heat_comm,
            "commodity_out": "Dummy",
            "scenario": scenario_name,
            "efficiency": 1.0,
            "technical_availability": 1.0,
            "min_eout": float(annual_heat_by_d[i]),
            "output_profile": naming.demand_profile_name,
        })

    for pipe in pipe_connections:
        # Pipe_Di_Dj: import into district Di from district Dj.
        i, j = pipe.region_id_in, pipe.region_id_out
        src_idx = district_index.get(j)
        if src_idx is None:
            raise KeyError(f"Missing district index for source district {j}")
        dst_idx = district_index.get(i)
        if dst_idx is None:
            raise KeyError(f"Missing district index for target district {i}")
        src_comm = district_heat_out_names.get(districts[src_idx], f"district_heat_out_D{districts[src_idx]}")
        dst_comm = district_heat_in_names.get(districts[dst_idx], f"district_heat_in_D{districts[dst_idx]}")
        cs_rows.append({
            "conversion_process_name": f"Pipe_D{i}_D{j}",
            "commodity_in": src_comm,
            "commodity_out": dst_comm,
            "scenario": scenario_name,
            "efficiency": max(0.0, 1.0 - float(pipe.pipe_loss_fraction)),
            "technical_availability": 1.0,
            "technical_lifetime": pipe.pipe_lifetime_years,
            "cap_max": pipe.pipe_cap_max_mw,
            "max_eout": pipe.pipe_cap_max_mwh,
            "opex_cost_energy": pipe.pipe_opex_eur_per_mwh,
            "capex_cost_base": pipe.pipe_capex_base_eur,
        })

    builder = _ConversionRowsBuilder(
        scenario_name=scenario_name,
        scenario_years=scenario_years,
        demand_commodity=naming.demand_commodity,
        districts=districts,
        district_index=district_index,
        heat_names=heat_names,
        district_heat_in_names=district_heat_in_names,
        district_heat_out_names=district_heat_out_names,
        min_dhn_targets=constraints.min_dhn_targets,
        min_heat_grid_targets=constraints.min_heat_grid_targets,
        min_central_cap_targets=constraints.min_central_cap_targets,
        min_central_cap_totals=constraints.min_central_cap_totals,
        min_central_cap_totals_by_co=constraints.min_central_cap_totals_by_co,
        exchanger_throughput_targets=district_ctx.historical_exchanger_targets_mwh,
        district_to_region=district_ctx.district_to_region,
        region_metrics=district_ctx.region_metrics,
        total_metrics=district_ctx.total_metrics,
        retain_factor=district_ctx.retain_factor,
        retain_years_factor=retain_existing_output_years_factor,
        retain_schedule=district_ctx.retain_schedule,
        lockout_until_year=lockout_until_year,
        elec_price_eur_per_mwh=elec_price_eur_per_mwh,
        local_dhn_capex_base_by_district=district_ctx.local_dhn_capex_base_by_district,
    )
    cs_rows.extend(builder.build_rows(sel_list))

    return cs_rows


# ---------------------------------------------------------------------------
# Activation year enforcement
# ---------------------------------------------------------------------------

def _apply_activation_year_caps(
    cs_rows: list[dict[str, Any]],
    constraints: _ParsedConstraints,
    scenario_years: list[int],
) -> None:
    commodity_activation_year = constraints.commodity_activation_year
    technology_activation_year = constraints.technology_activation_year

    def _tech_activation_year(cs_row: dict[str, Any]) -> int | None:
        if not technology_activation_year:
            return None
        cp_raw = str(cs_row.get("conversion_process_name", "") or "").strip()
        if not cp_raw:
            return None
        years: list[int] = []
        direct = technology_activation_year.get(cp_raw.lower())
        if direct is not None:
            years.append(int(direct))
        base, district = _split_base_and_district(cp_raw)
        if district is not None:
            base_year = technology_activation_year.get(base.lower())
            if base_year is not None:
                years.append(int(base_year))
        return max(years) if years else None

    def _row_activation_year(cs_row: dict[str, Any]) -> int | None:
        cin = str(cs_row.get("commodity_in", "") or "").strip().lower()
        cout = str(cs_row.get("commodity_out", "") or "").strip().lower()
        years: list[int] = []
        if cin in commodity_activation_year:
            years.append(int(commodity_activation_year[cin]))
        if cout in commodity_activation_year:
            years.append(int(commodity_activation_year[cout]))
        tech_year = _tech_activation_year(cs_row)
        if tech_year is not None:
            years.append(int(tech_year))
        # For rows with multiple applicable constraints, apply the stricter
        # (later) activation year.
        return max(years) if years else None

    for row in cs_rows:
        activation_year = _row_activation_year(row)
        if activation_year is None:
            continue

        cap_res_min_map, cap_res_min_scalar = _profile_to_map_and_scalar(row.get("cap_res_min"), ignore_invalid=True)
        cap_res_max_map, cap_res_max_scalar = _profile_to_map_and_scalar(row.get("cap_res_max"), ignore_invalid=True)

        def _residual_ceiling_for_year(y: int) -> float:
            res_max = _value_for_year(cap_res_max_map, cap_res_max_scalar, y)
            res_min = _value_for_year(cap_res_min_map, cap_res_min_scalar, y)
            if res_max is None and res_min is None:
                return 0.0
            vals = [float(v) for v in (res_max, res_min) if v is not None]
            return max(vals) if vals else 0.0

        for column in ("cap_max", "cap_min"):
            existing = row.get(column)
            if existing is None and column != "cap_max":
                continue

            profile_map, scalar = _profile_to_map_and_scalar(existing, ignore_invalid=True)
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
                        # No NaN here: CESM profile interpolation drops NaN and backfills zero values into later years.
                        pairs.append((year_i, 10000000.0))
                    continue
                pairs.append((year_i, float(base_value)))

            if pairs:
                prof = _format_year_profile(pairs)
                if prof is not None:
                    row[column] = prof


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _write_cesm_inputs(
        resolved: ResolvedSystem,
        *,
        techmap_dir: PathLike,
        timeseries_dir: PathLike,
        model_name: str,
        scenario_name: str,
        tss_name: str,
        dt_hours: int = 1,
        heat_commodity_base: str | None = None,
        selected_techs: list[Technology] | TechnologyRegistry | None = None,
        retain_existing_output_factor: float | None = None,
        retain_existing_output_years_factor: float | None = None,
) -> None:
    paths = _prepare_io_paths(Path(techmap_dir), Path(timeseries_dir), model_name, tss_name)

    scenario_years = resolved.scenario_years
    if resolved.retain_schedule is None:
        raise ValueError("retain_existing_output_schedule must be provided by the scenario (no defaults)")

    start_year = scenario_years[0]
    end_year = scenario_years[-1]
    year_step = scenario_years[1] - scenario_years[0] if len(scenario_years) > 1 else 1

    naming = _build_naming_context(resolved, heat_commodity_base)
    sel_list = _resolve_tech_list(resolved, selected_techs)
    constraints = _parse_constraints(
        resolved.constraints, start_year=start_year, demand_commodity=naming.demand_commodity
    )
    district_ctx = _build_district_context(
        resolved, naming, retain_existing_output_factor=retain_existing_output_factor
    )
    annual_heat_by_d = _extract_annual_heat(resolved, naming, start_year)

    commodity_list, cp_list, supply_candidates = _build_techmap_lists(
        naming, sel_list, resolved.pipe_connections, scenario_name
    )
    cs_rows = _build_cs_rows(
        naming, district_ctx, constraints, sel_list, resolved.imports, supply_candidates,
        resolved.pipe_connections,
        scenario_name=scenario_name,
        scenario_years=scenario_years,
        annual_heat_by_d=annual_heat_by_d,
        retain_existing_output_years_factor=retain_existing_output_years_factor,
        lockout_until_year=resolved.lockout_until_year,
    )
    _apply_activation_year_caps(cs_rows, constraints, scenario_years)

    commodity_df = pd.DataFrame(
        [{"commodity_name": c, "order": i + 1, "color": ""} for i, c in enumerate(commodity_list)],
        columns=["commodity_name", "order", "color"],
    )
    cp_df = pd.DataFrame(
        [{"conversion_process_name": t, "order": i + 1, "color": ""} for i, t in enumerate(cp_list)],
        columns=["conversion_process_name", "order", "color"],
    )
    cs_df = _convsubproc_dataframe(cs_rows)
    _write_techmap_workbook(
        paths.xlsx_path,
        units_df=_units_df(),
        scenario_df=_scenario_df(
            scenario_name=scenario_name,
            start_year=start_year,
            end_year=end_year,
            year_gap=year_step,
            tss_name=tss_name,
            discount_rate=resolved.discount_rate,
        ),
        tss_df=_tss_df(tss_name=tss_name, dt_hours=dt_hours),
        commodity_df=commodity_df,
        convproc_df=cp_df,
        convsubproc_df=cs_df,
    )
    _log_techmap_stats(
        commodity_count=len(commodity_list),
        convproc_count=len(cp_list),
        convsubproc_rows=len(cs_df),
    )
