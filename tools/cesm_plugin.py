"""Unified CESM plugin consolidating backend adapter, runner CLI, and writer utilities.

Public API:
    - CESMBackend (Optimization Model adapter)
    - write_cesm_inputs_minimal_from_om
    - write_cesm_inputs_from_energy_system
"""
from __future__ import annotations
import argparse, json, math, sqlite3, subprocess, sys, logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import pandas as pd
import numpy as np
import geopandas as gpd 

from pypeline.optimization.solver import OptimizationModel, Solution
from pypeline.optimization.om_adapter import OMContext, build_om_from_es
from pypeline.energy_system.energy_system import EnergySystem
from pypeline.energy_system.scenario import Scenario
from pypeline.energy_technology.technology import Technology
from pypeline.energy_technology.technology_registry import (
    TechnologyRegistry,
    get_default_technology_registry,
)
PathLike = Union[str, Path]

logger = logging.getLogger(__name__)

NUMERIC_ERRORS = (TypeError, ValueError)

def _require_float(label: str, value: Any) -> float:
    f = float(value)
    if math.isnan(f):
        raise ValueError(f"{label} is NaN")
    return f


def _require_finite(label: str, value: Any, *, allow_zero: bool = True, gt_zero: bool = False) -> float:
    f = _require_float(label, value)
    if not math.isfinite(f):
        raise ValueError(f"{label} must be finite")
    if gt_zero and f <= 0.0:
        raise ValueError(f"{label} must be > 0")
    if not allow_zero and f == 0.0:
        raise ValueError(f"{label} must be non-zero")
    return f


def _require_positive_int(label: str, value: Any) -> int:
    if value is None:
        raise ValueError(f"{label} is required")
    try:
        iv = int(value)
    except NUMERIC_ERRORS as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if iv <= 0:
        raise ValueError(f"{label} must be > 0")
    return iv

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


def _commodity_config_from_energy_system(es: EnergySystem) -> tuple[dict[str, float], dict[str, float]]:
    config_raw = getattr(es, "commodity_config", None)
    if config_raw is None:
        raise ValueError("EnergySystem.commodity_config is required to provide commodity prices for CESM export")
    if not isinstance(config_raw, dict):
        raise ValueError("EnergySystem.commodity_config must be a mapping")

    grid_raw = config_raw.get("grid_prices")
    supply_raw = config_raw.get("supply_prices_eur_per_mwh")

    if not isinstance(grid_raw, dict) or not isinstance(supply_raw, dict):
        raise ValueError("EnergySystem.commodity_config must include grid_prices and supply_prices_eur_per_mwh mappings")

    def _coerce_prices(source: dict[str, Any], *, lower_keys: bool = True) -> dict[str, float]:
        out: dict[str, float] = {}
        for key, raw_val in source.items():
            try:
                val = float(raw_val)
            except NUMERIC_ERRORS as exc:
                raise ValueError("Non-numeric commodity price in EnergySystem.commodity_config") from exc
            if not math.isfinite(val):
                raise ValueError("Commodity prices must be finite")
            out[str(key).lower() if lower_keys else str(key)] = val
        return out

    grid_prices = _coerce_prices(grid_raw, lower_keys=True)
    supply_prices = _coerce_prices(supply_raw, lower_keys=True)


    logger.debug(
        "_commodity_config_from_energy_system parsed: grid_prices=%s, supply_prices=%s",
        grid_prices,
        supply_prices,
    )

    return grid_prices, supply_prices


def _ensure_tss_indices(tss_file: Path, *, default_hours: int = 224) -> List[int]:
    if not tss_file.exists():
        raise FileNotFoundError(f"TSS file missing: {tss_file}. Provide it via the EnergySystem/Scenario.")
    content = tss_file.read_text(encoding="utf-8").strip()
    return [int(line) for line in content.splitlines() if line.strip()]


def _normalize_profile(profile_full: np.ndarray, tss_vals: List[int]) -> np.ndarray:
    """Normalize a profile and optionally rescale to TSS indices."""
    prof_sum = float(profile_full.sum())
    if prof_sum <= 0:
        raise ValueError("Electricity profile sums to zero; cannot normalize.")
    profile_full = profile_full / prof_sum
    if not tss_vals:
        return profile_full

    max_idx = max(tss_vals)
    if max_idx >= len(profile_full):
        raise ValueError(f"TSS requires index {max_idx} but profile length is {len(profile_full)}.")

    idx_arr = np.asarray(tss_vals, dtype=int) - 1
    selected_sum = float(profile_full[idx_arr].sum())
    if selected_sum <= 0.0:
        raise ValueError("Electricity profile assigns zero weight to selected TSS hours; cannot normalize.")
    if not np.isclose(selected_sum, 1.0):
        profile_full = profile_full / selected_sum
    from decimal import Decimal, ROUND_HALF_UP, getcontext

    getcontext().prec = 28
    quantum = Decimal("0.00000001")
    rounded: list[Decimal] = []
    for pos in idx_arr:
        rounded.append(Decimal(profile_full[pos]).quantize(quantum, rounding=ROUND_HALF_UP))
    correction = Decimal("1.0") - sum(rounded)
    if correction != 0:
        rounded[-1] = (rounded[-1] + correction).quantize(quantum, rounding=ROUND_HALF_UP)
    for pos, dec_val in zip(idx_arr, rounded):
        profile_full[pos] = float(dec_val)
    return profile_full


def _write_demand_profile(timeseries_dir: Path, profile_name: str, profile: np.ndarray) -> Path:
    path = timeseries_dir / f"{profile_name}.txt"
    path.write_text(" ".join(f"{x:.8f}" for x in profile.tolist()), encoding="utf-8")
    return path


def _resolve_retain_schedule(*, explicit_schedule: Optional[List[float]], scenario: Scenario | None) -> Optional[List[float]]:
    """Pick retention schedule from explicit arg or scenario; derive from drop-per-year if provided."""
    if explicit_schedule:
        return explicit_schedule
    if scenario is None:
        return None
    scen_schedule = getattr(scenario, "retain_existing_output_schedule", None)
    if scen_schedule:
        return scen_schedule
    drop_val_raw = getattr(scenario, "retain_existing_output_drop_per_year", None)
    if drop_val_raw is None:
        return None
    try:
        drop_val = float(drop_val_raw)
    except NUMERIC_ERRORS:
        return None
    if math.isnan(drop_val):
        return None
    drop_val = max(0.0, drop_val)
    years = scenario.years() if hasattr(scenario, "years") else []
    if not years:
        return None
    start_year = years[0]
    return [(1.0 - drop_val) ** (year - start_year) for year in years]


def _convsubproc_dataframe(rows: List[dict[str, Any]]) -> pd.DataFrame:
    convsubproc_df = pd.DataFrame(rows)
    base_cols = list(CONV_SUBPROC_BASE_COLS)
    param_cols = list(CONV_SUBPROC_PARAM_COLS)
    missing_cols = [col for col in base_cols + param_cols if col not in convsubproc_df.columns]
    if missing_cols:
        raise ValueError(f"Missing conversion subprocess columns: {missing_cols}")
    return convsubproc_df[base_cols + param_cols]


def _write_techmap_workbook(
    xlsx: Path,
    *,
    units_df: pd.DataFrame,
    scenario_df: pd.DataFrame,
    tss_df: pd.DataFrame,
    commodity_df: pd.DataFrame,
    convproc_df: pd.DataFrame,
    convsubproc_df: pd.DataFrame,
) -> None:
    with pd.ExcelWriter(xlsx, engine="openpyxl", mode="w") as xw:
        _write_standard_sheets(
            xw,
            units_df=units_df,
            scenario_df=scenario_df,
            tss_df=tss_df,
            commodity_df=commodity_df,
            convproc_df=convproc_df,
        )
        cs_sheet = "ConversionSubProcess"
        pd.DataFrame(columns=convsubproc_df.columns).to_excel(xw, sheet_name=cs_sheet, index=False)
        convsubproc_df.to_excel(xw, sheet_name=cs_sheet, index=False, header=False, startrow=3)
    logger.info("Wrote techmap: %s", xlsx)
    logger.info(
        "Techmap sheets: Units, Scenario, TSS, Commodity, ConversionProcess, ConversionSubProcess"
    )


def _log_techmap_stats(*, commodity_count: int, convproc_count: int, convsubproc_rows: int) -> None:
    logger.info(
        "Techmap stats: commodities=%d | conversion_processes=%d | conversion_subprocess_rows=%d",
        commodity_count,
        convproc_count,
        convsubproc_rows,
    )

CONV_SUBPROC_BASE_COLS: tuple[str, ...] = (
    "conversion_process_name",
    "commodity_in",
    "commodity_out",
    "scenario",
)

CONV_SUBPROC_PARAM_COLS: tuple[str, ...] = (
    "spec_co2",
    "efficiency",
    "technical_lifetime",
    "technical_availability",
    "c_rate",
    "efficiency_charge",
    "is_storage",
    "opex_cost_energy",
    "opex_cost_power",
    "capex_cost_power",
    "capex_cost_base",
    "cap_active",
    "max_units",
    "max_eout",
    "min_eout",
    "cap_min",
    "cap_max",
    "cap_res_min",
    "cap_res_max",
    "out_frac_min",
    "out_frac_max",
    "in_frac_min",
    "in_frac_max",
    "availability_profile",
    "output_profile",
)

# Census heating categories (100 m grid) mapped to existing technology names.
CENSUS_HEATING_CATEGORY_TO_TECH: Dict[str, Optional[str]] = {
    "Gas": "ind_gas_boiler",
    "Heizoel": "ind_oil_boiler",
    "Holz_Holzpellets": "ind_biomass_boiler",
    "Biomasse_Biogas": "ind_biogas_boiler",
    "Solar_Geothermie_Waermepumpen": "ind_heat_pump",
    "Strom": "ind_direct_electric",
    "Kohle": "ind_coal_boiler",
    "Fernwaerme": "HeatExchanger",
    "kein_Energietraeger": None,
}

HEAT_EXCHANGER_BASE_NAMES: tuple[str, ...] = ("heat_exchanger", "ind_district_heating_connection")
PRIMARY_HEAT_EXCHANGER: str = HEAT_EXCHANGER_BASE_NAMES[0]

def census_category_to_tech(category: str) -> Optional[str]:
    """Map a Census 2022 heating carrier label to a technology name used in the registry."""
    if category is None:
        return None
    key = category.strip()
    if not key:
        return None
    return CENSUS_HEATING_CATEGORY_TO_TECH.get(key)


def _extract_district_id_from_name(name: str) -> Optional[int]:
    if not name:
        return None
    base = name
    if "_D" not in base:
        return None
    suffix = base.rsplit("_D", 1)[-1]
    digits = []
    for ch in suffix:
        if ch.isdigit():
            digits.append(ch)
        else:
            break
    if not digits:
        return None
    try:
        return int("".join(digits))
    except ValueError:
        return None


def _split_base_and_district(name: str) -> tuple[str, Optional[int]]:
    district = _extract_district_id_from_name(name)
    if district is None:
        return name, None
    base = name.rsplit("_D", 1)[0]
    return base, district


def _is_central_heat_supply(name: str | None) -> bool:
    if not name:
        return False
    base = name
    if "_D" in base:
        base, _ = base.rsplit("_D", 1)
    return base.startswith("cen_")


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
            raw = getattr(t, "commodity_in", None)
            return str(raw).strip().lower() if raw is not None else ""

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
                val = getattr(tech, "cap_max", None)
                if val is None:
                    raise ValueError(f"cap_max missing for central tech '{tech.name}' in district {district}")
                return _require_finite(f"cap_max for {tech.name}", val, gt_zero=True)

            def _max_units(tech: Technology) -> float:
                units = getattr(tech, "max_units", None)
                return float(_require_positive_int(f"max_units for {tech.name}", units))

            def _total_cap(tech: Technology) -> float:
                cap = _cap_max(tech)
                units = _max_units(tech)
                return cap * units

            def _capex(tech: Technology) -> float:
                return _require_finite(f"capex_cost_power for {tech.name}", getattr(tech, "capex_cost_power", None), allow_zero=True)

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
        overrides = self._min_eout_override(tech, None)
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
        return row

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

        total_cap_limit = math.inf
        total_cap_limit = cap_max_peak * max_units_val
        enforce_min = target_total if math.isinf(total_cap_limit) else target_total

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
            cap_res_max_existing_map = self._profile_to_map(row.get("cap_res_max"))

            cap_min_pairs: list[tuple[int, float]] = []
            cap_res_min_pairs: list[tuple[int, float]] = []
            cap_res_max_pairs: list[tuple[int, float]] = []

            for year in self.scenario_years:
                active = year >= self.lockout_until_year
                cap_min_floor = per_unit_min if active else 0.0
                cap_res_min_floor = enforce_min if active else 0.0
                cap_res_max_floor = (UNBOUNDED_CAP if math.isinf(total_cap_limit) else total_cap_limit) if active else 0.0

                cap_min_pairs.append((year, max(float(cap_min_existing_map.get(year, 0.0) or 0.0), cap_min_floor)))
                cap_res_min_pairs.append((year, max(float(cap_res_min_existing_map.get(year, 0.0) or 0.0), cap_res_min_floor)))
                cap_res_max_pairs.append((year, max(float(cap_res_max_existing_map.get(year, 0.0) or 0.0), cap_res_max_floor)))

            cap_min_prof = self._format_profile(cap_min_pairs)
            cap_res_min_prof = self._format_profile(cap_res_min_pairs)
            cap_res_max_prof = self._format_profile(cap_res_max_pairs)
            if cap_min_prof:
                row["cap_min"] = cap_min_prof
            if cap_res_min_prof:
                row["cap_res_min"] = cap_res_min_prof
            if cap_res_max_prof:
                row["cap_res_max"] = cap_res_max_prof
            return

        existing_cap_min = _require_float("cap_min", row.get("cap_min")) if row.get("cap_min") is not None else 0.0
        row["cap_min"] = max(existing_cap_min, per_unit_min)

        existing_cap_res_min = _require_float("cap_res_min", self._profile_peak(row.get("cap_res_min"))) if row.get("cap_res_min") is not None else 0.0
        row["cap_res_min"] = max(existing_cap_res_min, enforce_min)

        existing_cap_res_max = _require_float("cap_res_max", self._profile_peak(row.get("cap_res_max"))) if row.get("cap_res_max") is not None else 0.0
        row["cap_res_max"] = UNBOUNDED_CAP if math.isinf(total_cap_limit) else max(existing_cap_res_max, total_cap_limit)

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
        val = _require_float("initial_capacity", metrics.get("initial_capacity", 0.0))
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
            cap = _require_float("initial_capacity", metrics.get("initial_capacity", 0.0))
            output = _require_float("initial_energy_output", metrics.get("initial_energy_output", 0.0))
            base_name, _ = _split_base_and_district(tech.name)
            is_indirect = base_name.startswith("ind_")

            lifetime_years = _require_float("technical_lifetime", getattr(tech, "technical_lifetime", 0.0) or 0.0)
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
                if output > 0.0:
                    pairs = []
                    for idx, year in enumerate(self.scenario_years):
                        if self._years_since_start(year) > remaining_years + 1e-9:
                            pairs.append((year, 0.0))
                            continue
                        if (
                            self.lockout_until_year is not None
                            and year < self.lockout_until_year
                            and not is_indirect
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
                if output > 0.0:
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
            try:
                year_i = int(year)
                val_f = float(value)
            except NUMERIC_ERRORS:
                continue
            segments.append(f"{year_i} {val_f:.10g}")
        if not segments:
            return None
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
                    try:
                        year_i = int(float(parts[0]))
                        val_f = float(parts[1])
                    except NUMERIC_ERRORS:
                        continue
                    mapping[year_i] = val_f
                return mapping
        try:
            scalar = float(value)
        except NUMERIC_ERRORS:
            return mapping
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
        try:
            scalar = float(value)
        except NUMERIC_ERRORS:
            return None
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

        cin = getattr(tech, "commodity_in", None)
        cout = getattr(tech, "commodity_out", None)
        hydrogen_related = "hydrogen" in name_lower or _has_h2(cin) or _has_h2(cout)

        cap_limit = max(0.0, float(existing_capacity or 0.0))
        base_name, _ = _split_base_and_district(tech.name)
        is_indirect = base_name.startswith("ind_")
        if cap_limit <= 0.0 and is_indirect and isinstance(metrics, dict):
            try:
                existing_output = float(metrics.get("initial_energy_output", 0.0) or 0.0)
            except NUMERIC_ERRORS:
                existing_output = 0.0
            if existing_output > 0.0:
                cap_limit = max(cap_limit, existing_output / 8760.0)
        first_year = self.scenario_years[0]
        lockout_until_year = self.lockout_until_year if self.lockout_until_year is not None else first_year

        hydrogen_start_year = 2030

        def _coerce_default(value: Any) -> Optional[float]:
            if value is None:
                return None
            try:
                val = float(value)
            except NUMERIC_ERRORS:
                return None
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
                try:
                    adjusted_f = float(adjusted)
                except NUMERIC_ERRORS:
                    continue
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
            if year < lockout_until_year:
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
            if year < lockout_until_year:
                return min(base_val, cap_limit)
            # Keep user-specified minima for later years when there is no existing capacity.
            if cap_limit <= 0.0:
                return base_val
            return base_val

        def _adjust_cap_res_min(year: int, base: Optional[float]) -> Optional[float]:
            base_val = base if base is not None else 0.0
            if hydrogen_related and year < hydrogen_start_year:
                return 0.0
            if year < lockout_until_year:
                return min(base_val, cap_limit)
            return base_val

        def _adjust_cap_res_max(year: int, base: Optional[float]) -> Optional[float]:
            if hydrogen_related and year < hydrogen_start_year:
                return 0.0
            if year < lockout_until_year:
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

        logger.debug("optimize: energy_system commodity_config=%s", getattr(model, "commodity_config", None))

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
        try:
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
        except sqlite3.Error as exc:
            logger.warning("Failed to backfill commodity timeseries for %s: %s", db_path, exc)

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
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    row_counts[t] = int(cur.fetchone()[0])
                except sqlite3.Error:
                    row_counts[t] = -1
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

def write_cesm_inputs_minimal_from_om(
    om: OMContext,
    cesm_root: Path,
    model_name: str,
    scenario_name: str,
    tss_name: str,
) -> None:
    tm_dir = cesm_root / "Data" / "Techmap"
    ts_dir = cesm_root / "Data" / "TimeSeries"
    tm_dir.mkdir(parents=True, exist_ok=True)
    ts_dir.mkdir(parents=True, exist_ok=True)
    df_units = pd.DataFrame(
        [
            {"quantity": "energy", "scale_factor": 1.0, "input": "MWh", "output": "MWh"},
            {"quantity": "power", "scale_factor": 1.0, "input": "MW", "output": "MW"},
            {"quantity": "cost_energy", "scale_factor": 1.0, "input": "€/MWh", "output": "EUR/MWh"},
            {"quantity": "cost_power", "scale_factor": 1.0, "input": "€/MW", "output": "EUR/MW"},
            {"quantity": "co2_spec", "scale_factor": 1.0, "input": "t/MWh", "output": "t/MWh"},
        ]
    )
    scen = getattr(om, "scenario", None)
    y0 = getattr(scen, "start_year", 2020)
    y1 = getattr(scen, "end_year", y0)
    step = getattr(scen, "year_gap", 5)
    disc = getattr(scen, "discount_rate", 0.05) if hasattr(scen, "discount_rate") else 0.05
    df_scenario = pd.DataFrame(
        [
            {
                "scenario_name": scenario_name,
                "from_year": y0,
                "until_year": y1,
                "year_step": step,
                "discount_rate": disc,
                "TSS": tss_name,
                "annual_co2_limit": None,
                "co2_price": None,
            }
        ]
    )
    df_tss = pd.DataFrame([{"TSS_name": tss_name, "dt": 1}])
    commodities = list(getattr(om, "commodities", []) or [])
    processes = list(getattr(om, "conversion_processes", []) or [])
    df_co = (
        pd.DataFrame(
            [{"commodity_name": c, "order": None, "color": None} for c in commodities]
        )
        if commodities
        else pd.DataFrame(columns=["commodity_name", "order", "color"])
    )
    df_cp = (
        pd.DataFrame(
            [{"conversion_process_name": p, "order": None, "color": None} for p in processes]
        )
        if processes
        else pd.DataFrame(columns=["conversion_process_name", "order", "color"])
    )
    cs = list(getattr(om, "conversion_sub_processes", []) or [])
    base_cols = list(CONV_SUBPROC_BASE_COLS)
    param_cols = list(CONV_SUBPROC_PARAM_COLS)
    rows = []
    for x in cs:
        row = {
            "conversion_process_name": x.get("cp") or x.get("process") or "",
            "commodity_in": x.get("cin") or x.get("in") or "",
            "commodity_out": x.get("cout") or x.get("out") or "",
            "scenario": scenario_name,
        }
        for p in param_cols:
            row[p] = x.get(p, None)
        rows.append(row)
    df_cs = (
        pd.DataFrame(rows, columns=base_cols + param_cols)
        if rows
        else pd.DataFrame(columns=base_cols + param_cols)
    )
    xlsx = tm_dir / f"{model_name}.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl", mode="w") as xw:
        _write_standard_sheets(
            xw,
            units_df=df_units,
            scenario_df=df_scenario,
            tss_df=df_tss,
            commodity_df=df_co,
            convproc_df=df_cp,
        )
        df_cs.to_excel(xw, sheet_name="ConversionSubProcess", index=False)


# ====================================================================================
# writer utilities 
# ====================================================================================

def _write_standard_sheets(
    writer: pd.ExcelWriter,
    *,
    units_df: pd.DataFrame,
    scenario_df: pd.DataFrame,
    tss_df: pd.DataFrame,
    commodity_df: pd.DataFrame,
    convproc_df: pd.DataFrame,
) -> None:
    """Write the common workbook sheets shared across CESM exporters."""
    units_df.to_excel(writer, sheet_name="Units", index=False)
    scenario_df.to_excel(writer, sheet_name="Scenario", index=False)
    tss_df.to_excel(writer, sheet_name="TSS", index=False)
    commodity_df.to_excel(writer, sheet_name="Commodity", index=False)
    convproc_df.to_excel(writer, sheet_name="ConversionProcess", index=False)

def _canon_co(name: str) -> str:
    if name is None:
        return ""
    m = name.strip().lower()
    if m == "electricity":
        return "Electricity"
    if m == "dummy":
        return "Dummy"
    if m == "external":
        return "External"
    return name

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
    cap_min_attr = getattr(tech, "cap_min", None)
    cap_max_attr = getattr(tech, "cap_max", None)
    max_units = getattr(tech, "max_units", 1)
    spec_co2_attr = getattr(tech, "spec_co2", None)
    is_storage_attr = getattr(tech, "is_storage", False)
    out_frac_min_attr = getattr(tech, "out_frac_min", None)
    out_frac_max_attr = getattr(tech, "out_frac_max", None)
    in_frac_min_attr = getattr(tech, "in_frac_min", None)
    in_frac_max_attr = getattr(tech, "in_frac_max", None)
    availability_profile_attr = getattr(tech, "availability_profile", None)
    output_profile_attr = getattr(tech, "output_profile", None)

    efficiency_val = _require_finite("efficiency", tech.efficiency, gt_zero=True)
    lifetime_val = _require_finite("technical_lifetime", tech.technical_lifetime, gt_zero=True)
    opex_energy_val = _require_finite("opex_cost_energy", tech.opex_cost_energy, allow_zero=True)
    opex_power_val = _require_finite("opex_cost_power", tech.opex_cost_power, allow_zero=True)
    capex_power_val = _require_finite("capex_cost_power", tech.capex_cost_power, allow_zero=True)
    capex_base_val = _require_finite("capex_cost_base", getattr(tech, "capex_cost_base", None), allow_zero=True)

    spec_co2_val = None if spec_co2_attr is None else _require_finite("spec_co2", spec_co2_attr, allow_zero=True)
    is_storage_val = bool(is_storage_attr)
    c_rate_val: float | None = None
    efficiency_charge_val: float | None = None
    if is_storage_val:
        c_rate_raw = getattr(tech, "c_rate", None)
        if c_rate_raw is None:
            raise ValueError(f"c_rate is required for storage technology '{tech.name}'")
        efficiency_charge_raw = getattr(tech, "efficiency_charge", None)
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
    data_dir: Optional[PathLike] = None,
    start_year: int | None = None,
    end_year: int | None = None,
    year_gap: int | None = None,
    discount_rate: float | None = None,
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
    edge_strategy: str | None = None,
    min_shared_border_m: float | None = None,
    max_pipes_per_district: Optional[int] = None,
    neighbor_distance_m: Optional[float] = None,
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
    om_scenario = getattr(om, "scenario", None)
    start_year = start_year if start_year is not None else getattr(om_scenario, "start_year", None)
    end_year = end_year if end_year is not None else getattr(om_scenario, "end_year", None)
    year_gap = year_gap if year_gap is not None else getattr(om_scenario, "year_gap", None)
    discount_rate = discount_rate if discount_rate is not None else getattr(om_scenario, "discount_rate", None)

    if start_year is None or end_year is None or year_gap is None:
        raise ValueError("start_year, end_year, and year_gap must be provided via scenario or arguments")
    if discount_rate is None:
        raise ValueError("discount_rate must be provided via scenario or arguments")
    if retain_existing_output_schedule is None:
        raise ValueError("retain_existing_output_schedule must be provided by the scenario (no defaults)")

    if data_dir is None:
        raise ValueError("data_dir must be provided via the EnergySystem (no default path)")
    data_dir = Path(data_dir)
    om_regions = list(getattr(om, "regions", []))

    def _sanitize_price_map(source: dict[str, float] | None) -> dict[str, float]:
        if source is None:
            return {}
        if not isinstance(source, dict):
            raise ValueError(f"Commodity price map must be a mapping, got {type(source).__name__}: {source!r}")
        out: dict[str, float] = {}
        for key, value in source.items():
            try:
                val = float(value)
            except NUMERIC_ERRORS as exc:
                raise ValueError("Commodity prices must be numeric") from exc
            if not math.isfinite(val):
                raise ValueError("Commodity prices must be finite")
            out[str(key).lower()] = val
        return out

    if grid_prices is None or supply_prices is None:
        raise ValueError(
            "Commodity prices must be provided via EnergySystem.commodity_config or explicitly as grid_prices/supply_prices"
        )
    grid_prices = _sanitize_price_map(grid_prices)
    supply_prices = _sanitize_price_map(supply_prices)

    if elec_price_eur_per_mwh is None:
        try:
            elec_price_eur_per_mwh = float(grid_prices["electricity"])
        except KeyError as exc:
            raise ValueError("Missing electricity grid price in commodity configuration") from exc
        except NUMERIC_ERRORS as exc:
            raise ValueError("Non-numeric electricity grid price in commodity configuration") from exc

    if export_price_eur_per_mwh is None:
        try:
            export_price_eur_per_mwh = float(grid_prices["export"])
        except KeyError as exc:
            raise ValueError("Missing export grid price in commodity configuration") from exc
        except NUMERIC_ERRORS as exc:
            raise ValueError("Non-numeric export grid price in commodity configuration") from exc

    logger.debug(
        "commodity pricing resolved: grid_prices=%s, elec=%s, export=%s, supply_prices=%s",
        grid_prices,
        elec_price_eur_per_mwh,
        export_price_eur_per_mwh,
        supply_prices,
    )
    
    edge_strategy = "mst"
    min_shared_border_m = 0.0 if min_shared_border_m is None else float(min_shared_border_m)
    if max_pipes_per_district is not None:
        max_pipes_per_district = int(max_pipes_per_district)
    if neighbor_distance_m is not None:
        neighbor_distance_m = float(neighbor_distance_m)

    scenario_years: List[int] = []
    try:
        start_year_int = int(start_year)
        end_year_int = int(end_year)
    except NUMERIC_ERRORS:
        start_year_int = None
        end_year_int = None
    try:
        year_step_int = int(year_gap) if year_gap is not None else 1
    except NUMERIC_ERRORS:
        year_step_int = 1
    if year_step_int <= 0:
        year_step_int = 1
    if start_year_int is not None and end_year_int is not None:
        scenario_years = list(range(start_year_int, end_year_int + 1, year_step_int))
        if not scenario_years:
            scenario_years = [start_year_int]
    elif start_year_int is not None:
        scenario_years = [start_year_int]
    scenario_years = sorted(set(scenario_years))

    profile_full = None
    try:
        om_profile = getattr(om, "demand_profile", None)
        if om_profile is None:
            om_profile = getattr(om, "profile", None)
        if om_profile is not None:
            if hasattr(om_profile, "values"):
                om_profile = om_profile.values
            profile_arr = np.asarray(list(om_profile), dtype=float)
            if profile_arr.size == 8760:
                profile_full = profile_arr
    except NUMERIC_ERRORS:
        profile_full = None
    if profile_full is None:
        if elec_profile_file is None:
            raise ValueError("elec_profile_file must be provided via the EnergySystem (no default)")
        profile_full = _load_numeric_txt(data_dir / elec_profile_file)
    tss_vals = _ensure_tss_indices(paths.tss_file)
    profile_full = _normalize_profile(profile_full, tss_vals)
    demand_profile_name = "HeatDemandProfile"
    _write_demand_profile(ts_dir, demand_profile_name, profile_full)
    districts: List[int]
    heat_names: List[str]
    directed_edges: List[Tuple[int, int]] = []
    base_heat_name = heat_commodity_base or getattr(om, "commodity", None) or demand_commodity
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
    try:
        if gdf.crs is None or getattr(gdf.crs, "is_geographic", False):
            gdf = gdf.to_crs(3035)
    except (AttributeError, TypeError, ValueError):
        pass
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
        undirected_edges = _edges_pruned(
            gdf,
            edge_strategy=edge_strategy,
            min_shared_border_m=min_shared_border_m,
            max_pipes_per_district=max_pipes_per_district,
            neighbor_distance_m=neighbor_distance_m,
        )
        for i, j in undirected_edges:
            directed_edges.append((i, j))
            directed_edges.append((j, i))

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
        annual_map = getattr(om, "annual_demand", {}) or {}
        target_year = start_year if isinstance(start_year, int) else None
        if target_year is None:
            om_years = getattr(om, "years", None)
            if om_years:
                target_year = om_years[0]
        if target_year is None and annual_map:
            first_region = next(iter(annual_map.values()), {})
            if first_region:
                target_year = next(iter(first_region.keys()))
        values: List[float] = []
        for rid in om_regions:
            per_year = annual_map.get(rid, {})
            val = per_year.get(target_year) if isinstance(target_year, int) else None
            if val is None and per_year:
                val = next(iter(per_year.values()))
            values.append(float(val or 0.0))
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
    district_heat_names = [district_heat_in_names[d] for d in districts] + [district_heat_out_names[d] for d in districts]
    commodity_list = base + [grid_hub_name] + grid_names + heat_names + district_heat_names
    commodity_list = list(dict.fromkeys(commodity_list))
    sel_list: list[Technology] = []
    if isinstance(selected_techs, TechnologyRegistry):
        sel_list = selected_techs.get_all(return_type="instance")
    elif isinstance(selected_techs, list):
        sel_list = [t for t in selected_techs if isinstance(t, Technology)]
    elif selected_techs is None:
        om_techs = getattr(om, "technologies", None)
        if isinstance(om_techs, dict):
            sel_list = [t for t in om_techs.values() if isinstance(t, Technology)]
    dedup: dict[str, Technology] = {}
    filtered: list[Technology] = []
    for tech in sel_list:
        if tech.name not in dedup:
            dedup[tech.name] = tech
            filtered.append(tech)
    sel_list = filtered

    registry_for_specs = technology_registry
    if registry_for_specs is None and isinstance(selected_techs, TechnologyRegistry):
        registry_for_specs = selected_techs

    pipe_tech: Technology | None = None
    if registry_for_specs and registry_for_specs.has_technology(pipe_technology_name):
        pipe_tech = registry_for_specs.get_by_name(pipe_technology_name)
    else:
        default_registry = get_default_technology_registry()
        if default_registry.has_technology(pipe_technology_name):
            pipe_tech = default_registry.get_by_name(pipe_technology_name)

    def _require_value(value_override: Any, spec_value: Any, field_name: str) -> Any:
        if value_override is not None:
            return value_override
        if spec_value is not None:
            return spec_value
        raise ValueError(f"{field_name} is required for pipe technology '{pipe_technology_name}'")

    spec_efficiency: float | None = None
    if pipe_tech and getattr(pipe_tech, "efficiency", None) is not None:
        try:
            spec_efficiency = float(pipe_tech.efficiency)
        except NUMERIC_ERRORS:
            spec_efficiency = None
    if spec_efficiency is not None:
        spec_efficiency = max(0.0, min(1.0, spec_efficiency))
    spec_loss = None if spec_efficiency is None else max(0.0, 1.0 - spec_efficiency)

    spec_capex = None
    if pipe_tech and getattr(pipe_tech, "capex_cost_power", None) is not None:
        try:
            spec_capex = float(pipe_tech.capex_cost_power)
        except NUMERIC_ERRORS:
            spec_capex = None

    spec_opex = None
    if pipe_tech and getattr(pipe_tech, "opex_cost_energy", None) is not None:
        try:
            spec_opex = float(pipe_tech.opex_cost_energy)
        except NUMERIC_ERRORS:
            spec_opex = None

    spec_lifetime = None
    if pipe_tech and getattr(pipe_tech, "technical_lifetime", None) is not None:
        try:
            spec_lifetime = int(pipe_tech.technical_lifetime)
        except NUMERIC_ERRORS:
            spec_lifetime = None

    spec_cap_max = None
    if pipe_tech and getattr(pipe_tech, "cap_max", None) is not None:
        try:
            spec_cap_max = float(pipe_tech.cap_max)
        except NUMERIC_ERRORS:
            spec_cap_max = None

    resolved_loss = _require_value(pipe_loss_fraction, spec_loss, "pipe_loss_fraction or pipe efficiency")
    pipe_eff = max(0.0, 1.0 - float(resolved_loss))
    pipe_capex_value = _require_value(pipe_capex_eur_per_mw, spec_capex, "pipe_capex_eur_per_mw")
    pipe_opex_value = _require_value(pipe_opex_eur_per_mwh, spec_opex, "pipe_opex_eur_per_mwh")
    pipe_lifetime_value = int(_require_value(pipe_lifetime_years, spec_lifetime, "pipe_lifetime_years"))
    pipe_cap_max_value = float(_require_value(pipe_cap_max_mw, spec_cap_max, "pipe_cap_max_mw"))

    def _to_int_id(raw: Any) -> int:
        if hasattr(raw, "iloc"):
            try:
                raw = raw.iloc[0]
            except (IndexError, KeyError, TypeError, AttributeError):
                pass
        if isinstance(raw, np.generic):
            raw = raw.item()
        try:
            return int(raw)
        except NUMERIC_ERRORS:
            try:
                return int(float(raw))
            except NUMERIC_ERRORS as exc:
                raise ValueError(f"Unable to parse integer id from {raw!r}") from exc

    constraints_raw = getattr(om, "constraints", {}) or {}
    if constraints_raw and not isinstance(constraints_raw, dict):
        raise ValueError("constraints must be provided as a mapping")

    demand_commodity = getattr(om, "commodity", None) or "residential_heat"

    min_dhn_targets_raw = constraints_raw.get("min_dhn_throughput_mwh", {}) if constraints_raw else {}
    if min_dhn_targets_raw and not isinstance(min_dhn_targets_raw, dict):
        raise ValueError("min_dhn_throughput_mwh constraint must be a mapping of region ids to values")
    min_dhn_targets: dict[int, float] = {}
    for key, value in (min_dhn_targets_raw or {}).items():
        rid = _to_int_id(key)
        val = float(value)
        if val > 0:
            min_dhn_targets[rid] = float(val)

    min_heat_grid_key = f"min_heat_grid_{demand_commodity}"
    min_heat_grid_raw = constraints_raw.get(min_heat_grid_key, {}) if constraints_raw else {}
    if min_heat_grid_raw and not isinstance(min_heat_grid_raw, dict):
        raise ValueError(f"{min_heat_grid_key} constraint must be a mapping of region ids to values")
    min_heat_grid_targets: dict[int, float] = {}
    for key, value in (min_heat_grid_raw or {}).items():
        rid = _to_int_id(key)
        val = float(value)
        if val > 0:
            min_heat_grid_targets[rid] = float(val)

    min_central_cap_raw = constraints_raw.get("min_central_cap_mw", {}) if constraints_raw else {}
    min_central_cap_targets: dict[str, float] = {}
    for tech_name, value in (min_central_cap_raw or {}).items():
        val = float(value)
        if val > 0:
            min_central_cap_targets[tech_name] = float(val)

    min_central_cap_total_raw = constraints_raw.get("min_central_cap_mw_total", {})
    min_central_cap_totals: dict[int, float] = {}
    for key, value in (min_central_cap_total_raw or {}).items():
        did = _to_int_id(key)
        val = float(value)
        if val > 0:
            min_central_cap_totals[did] = float(val)

    min_central_cap_total_by_co_raw = constraints_raw.get("min_central_cap_mw_total_by_commodity", {}) 
    min_central_cap_totals_by_co: dict[str, dict[int, float]] = {}
    for commodity_raw, per_district in (min_central_cap_total_by_co_raw or {}).items():
        commodity = str(commodity_raw).strip().lower()
        bucket: dict[int, float] = {}
        for key, value in per_district.items():
            did = _to_int_id(key)
            val = float(value)
            if val > 0:
                bucket[did] = float(val)
        if bucket:
            min_central_cap_totals_by_co[commodity] = bucket

    om_region_ids = list(getattr(om, "regions", []))
    district_to_region: dict[int, int] = {}
    for idx, district_id in enumerate(districts):
        if idx >= len(om_region_ids):
            raise ValueError(f"Missing region id for district {district_id}")
        raw_rid = om_region_ids[idx]
        district_to_region[district_id] = _to_int_id(raw_rid)

    region_metrics = getattr(om, "region_technology_metrics", {}) or {}
    total_metrics: dict[str, dict[str, float]] = {}
    if isinstance(region_metrics, dict):
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
                try:
                    agg["initial_energy_output"] += float(metrics.get("initial_energy_output", 0.0) or 0.0)
                except NUMERIC_ERRORS:
                    pass
                try:
                    agg["initial_capacity"] += float(metrics.get("initial_capacity", 0.0) or 0.0)
                except NUMERIC_ERRORS:
                    pass

    if retain_existing_output_factor is None:
        retain_factor: float | None = None
    else:
        try:
            retain_factor = max(0.0, float(retain_existing_output_factor))
        except NUMERIC_ERRORS:
            retain_factor = None

    retain_schedule: Optional[List[float]] = None
    if retain_existing_output_schedule:
        sanitized: List[float] = []
        for entry in retain_existing_output_schedule:
            try:
                val = float(entry)
            except NUMERIC_ERRORS:
                continue
            if math.isnan(val):
                continue
            sanitized.append(max(0.0, val))
        if sanitized:
            retain_schedule = sanitized

    lockout_years_raw = getattr(om_scenario, "lockout_years", 2)
    try:
        lockout_years = max(0, int(lockout_years_raw))
    except NUMERIC_ERRORS:
        lockout_years = 2
    
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
        if c not in ("", "Dummy") and c.lower() != "electricity" and c not in protected_commodities
    )

    for commodity in supply_candidates:
        if commodity not in commodity_list:
            commodity_list.append(commodity)
    conv_procs: List[str] = ["GridExportElec"]
    if len(districts) == 1:
        conv_procs.append("HeatDemand")
    else:
        conv_procs += [f"HeatDemand_D{i}" for i in districts]
        conv_procs += [f"Pipe_D{i}_D{j}" for (i, j) in directed_edges]
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
        price = float(supply_price_defaults.get(commodity.lower(), elec_price_eur_per_mwh))
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
        min_eout = float(annual_heat_by_d[i]) if i < len(annual_heat_by_d) else 0.0
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

    for (i, j) in directed_edges:
        src_idx = district_index.get(i, i if 0 <= i < len(districts) else 0)
        dst_idx = district_index.get(j, j if 0 <= j < len(districts) else 0)
        src_comm = district_heat_out_names.get(districts[src_idx], f"district_heat_out_D{districts[src_idx]}")
        dst_comm = district_heat_in_names.get(districts[dst_idx], f"district_heat_in_D{districts[dst_idx]}")
        cs_rows.append(
            {
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
            }
        )

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

def _polygons_from_energy_system(es: EnergySystem) -> Optional[gpd.GeoDataFrame]:
    regions = getattr(es, "regions", []) or []
    frames: list[gpd.GeoDataFrame] = []
    for region in regions:
        poly = getattr(region, "polygon", None)
        if isinstance(poly, gpd.GeoDataFrame):
            frames.append(poly.copy())
    if not frames:
        return None
    gdf = pd.concat(frames, ignore_index=True)
    if "id" not in gdf.columns:
        gdf["id"] = range(len(gdf))
    try:
        if gdf.crs is None or getattr(gdf.crs, "is_geographic", False):
            gdf = gdf.to_crs(3035)
    except Exception:
        pass
    return gdf


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
        technology_registry=technology_registry,
        retain_existing_output_factor=retain_existing_output_factor,
        retain_existing_output_years_factor=retain_existing_output_years_factor,
        retain_existing_output_schedule=resolved_schedule,
        **kwargs,
    )

# Edge helpers ----------------------------------------------------------------

def _edges_pruned(
    gdf,
    *,
    edge_strategy="mst",
    min_shared_border_m: float = 0.0,
    max_pipes_per_district: Optional[int] = None,
    neighbor_distance_m: Optional[float] = None,
) -> List[Tuple[int, int]]:
    cand = _candidate_edges(
        gdf,
        min_shared_border_m=min_shared_border_m,
        neighbor_distance_m=neighbor_distance_m,
    )
    if not cand:
        return []
    n = len(gdf)
    if edge_strategy == "all":
        return [(i, j) for (i, j, _) in cand]
    if edge_strategy == "k_nearest":
        if not max_pipes_per_district or max_pipes_per_district <= 0:
            return [(i, j) for (i, j, _) in cand]
        by_i: List[List[Tuple[float, int]]] = [[] for _ in range(n)]
        for i, j, d in cand:
            by_i[i].append((d, j))
            by_i[j].append((d, i))
        keep = set()
        for i in range(n):
            by_i[i].sort(key=lambda x: x[0])
            for _, j in by_i[i][: max_pipes_per_district]:
                keep.add(tuple(sorted((i, j))))
        return sorted(list(keep))
    parent = list(range(n))
    rank = [0] * n
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        if rank[ra] < rank[rb]:
            parent[ra] = rb
        elif rank[ra] > rank[rb]:
            parent[rb] = ra
        else:
            parent[rb] = ra
            rank[ra] += 1
        return True
    cand_sorted = sorted(cand, key=lambda t: t[2])
    mst: List[Tuple[int, int]] = []
    for i, j, _ in cand_sorted:
        if union(i, j):
            mst.append((i, j))
    return mst

def _candidate_edges(
    gdf,
    *,
    min_shared_border_m=0.0,
    neighbor_distance_m=None,
):
    edges = []
    geoms = list(gdf.geometry)
    cents = [g.centroid for g in geoms]
    n = len(geoms)
    for i, gi in enumerate(geoms):
        if gi is None or gi.is_empty:
            continue
        for j in range(i + 1, n):
            gj = geoms[j]
            if gj is None or gj.is_empty:
                continue
            if not (gi.touches(gj) or gi.intersects(gj)):
                continue
            shared = gi.boundary.intersection(gj.boundary)
            length = float(getattr(shared, "length", 0.0) or 0.0)
            if length < float(min_shared_border_m):
                continue
            dist = float(cents[i].distance(cents[j]))
            if neighbor_distance_m is not None and dist > float(neighbor_distance_m):
                continue
            edges.append((i, j, dist))
    return edges

def _load_numeric_txt(path: Path) -> np.ndarray:
    txt = Path(path).read_text(encoding="utf-8").strip().replace("\n", " ")
    vals = [v for v in txt.split(" ") if v != ""]
    return np.asarray([float(x) for x in vals], dtype=float)

def _units_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"quantity": "energy", "scale_factor": 1.0, "input": "MWh", "output": "MWh"},
            {"quantity": "power", "scale_factor": 1.0, "input": "MW", "output": "MW"},
            {"quantity": "cost_energy", "scale_factor": 1.0, "input": "EUR/MWh", "output": "EUR/MWh"},
            {"quantity": "cost_power", "scale_factor": 1.0, "input": "EUR/MW", "output": "EUR/MW"},
            {"quantity": "co2_spec", "scale_factor": 1.0, "input": "tCO2/MWh", "output": "tCO2/MWh"},
        ],
        columns=["quantity", "scale_factor", "input", "output"],
    )

def _scenario_df(
    *,
    scenario_name: str,
    start_year: int | None,
    end_year: int | None,
    year_gap: int | None,
    tss_name: str,
    discount_rate: float,
) -> pd.DataFrame:
    if start_year is None:
        start_year = 2020
    if end_year is None:
        end_year = start_year
    if year_gap is None:
        year_gap = 1
    return pd.DataFrame(
        [
            {
                "scenario_name": scenario_name,
                "from_year": int(start_year),
                "until_year": int(end_year),
                "year_step": int(year_gap),
                "discount_rate": float(discount_rate),
                "TSS": tss_name,
                "annual_co2_limit": np.nan,
                "co2_price": np.nan,
            }
        ],
        columns=[
            "scenario_name",
            "from_year",
            "until_year",
            "year_step",
            "discount_rate",
            "TSS",
            "annual_co2_limit",
            "co2_price",
        ],
    )

def _tss_df(*, tss_name: str, dt_hours: int) -> pd.DataFrame:
    return pd.DataFrame([{"TSS_name": tss_name, "dt": int(dt_hours)}], columns=["TSS_name", "dt"])

def _param_cols() -> list[str]:
    return list(CONV_SUBPROC_PARAM_COLS)

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
    try:
        techmap_dir, ts_dir, runs_dir = _validate_cesm_paths(cesm_dir, model_name)
    except FileNotFoundError:
        return 2
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
