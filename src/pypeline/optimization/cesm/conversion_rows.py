"""CESM conversion subprocess row building.

Converts :class:`~pypeline.energy_technology.technology.Technology` objects
into the row dicts that populate the *ConversionSubProcess* sheet of the CESM
techmap Excel workbook.

Two public names:

* :class:`_ConversionRowsBuilder` — stateful builder that takes a list of
  technologies and the district/constraint configuration for a scenario and
  produces one or more rows per technology (handling multi-district
  replication, retention overrides, lockout periods, min-capacity targets,
  etc.).

* :func:`tech_to_cesms_row` — thin, stateless converter used directly by
  tests.  Takes a single :class:`Technology` and returns the raw row dict
  without any district or constraint logic applied.
"""
from __future__ import annotations
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from pypeline.energy_system.energy_system import HEAT_EXCHANGER_NAMES
from pypeline.energy_system.io_utils import _canon_co
from pypeline.energy_technology.technology import (
    Technology,
    extract_district_id_from_name as _extract_district_id_from_name,
    split_base_and_district as _split_base_and_district,
    is_central_heat_supply as _is_central_heat_supply,
)
from pypeline.validation import require_finite, require_float, require_positive_int

_require_float = require_float
_require_finite = require_finite
_require_positive_int = require_positive_int

UNBOUNDED_CAP = 1e9
UNBOUNDED_ENERGY = 1e12
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

        if base_name.startswith("ind_"):
            # ind_ technologies are modeled as continuous MW-scalable options.
            row["capex_cost_base"] = None
            row["cap_min"] = None
            row["cap_max"] = None
            row["max_units"] = None
            existing_cap = self._existing_capacity(metrics)
            self._limit_first_year_capacity(row, existing_cap, tech, metrics=metrics)
            return

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
            if is_indirect and year == first_year:
                # First year: allow scaling only for technologies that already exist.
                if cap_limit <= 0.0:
                    return 0.0
                return base
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
            if base is None:
                return None
            base_val = base
            if hydrogen_related and year < hydrogen_start_year:
                return 0.0
            if year < lockout_until_year and not is_indirect:
                return min(base_val, cap_limit)
            # Keep user-specified minima for later years when there is no existing capacity.
            if cap_limit <= 0.0:
                return base_val
            return base_val

        def _adjust_cap_res_min(year: int, base: Optional[float]) -> Optional[float]:
            if base is None:
                return None
            base_val = base
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

    base_name, _ = _split_base_and_district(tech.name)
    is_indirect = base_name.startswith("ind_")

    if is_indirect:
        cap_max_val = None
    elif cap_max_attr is None:
        raise ValueError(f"cap_max is required for technology '{tech.name}'")
    else:
        cap_max_val = _require_finite(f"cap_max for {tech.name}", cap_max_attr, gt_zero=True)

    if is_indirect:
        max_units_val = None
    elif max_units is None:
        raise ValueError(f"max_units is required for technology '{tech.name}'")
    else:
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
