"""Optimization-stage DHN helpers."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

from pypeline.energy_technology.technology import split_base_and_district as _split_base_and_district
from pypeline.validation import to_int_id


def enforce_historical_fernwaerme_dependency(
    region_metrics_raw: Mapping[Any, Any] | None,
) -> tuple[dict[int, dict[str, dict[str, float]]], Dict[int, float]]:
    """Normalize historical Fernwaerme dependency and return throughput targets.

    Districts without historical Fernwaerme keep DHN/heat-exchanger assets at
    zero initial but buildable.
    """
    if region_metrics_raw is None:
        return {}, {}
    if not isinstance(region_metrics_raw, Mapping):
        raise TypeError("region_technology_metrics must be a mapping")

    region_metrics: dict[int, dict[str, dict[str, float]]] = {
        to_int_id(rid_raw): dict(tech_map or {})
        for rid_raw, tech_map in region_metrics_raw.items()
    }

    historical_exchanger_targets_mwh: Dict[int, float] = {}

    for rid, tech_map in region_metrics.items():
        if not isinstance(tech_map, Mapping):
            raise TypeError(f"region_technology_metrics[{rid}] must be a dict")

        hx_keys: list[str] = []
        hg_keys: list[str] = []
        for tech_name in list(tech_map.keys()):
            base, _ = _split_base_and_district(str(tech_name))
            base_l = str(base).strip().lower()
            if base_l == "heat_exchanger":
                hx_keys.append(str(tech_name))
            elif base_l == "heat_grid":
                hg_keys.append(str(tech_name))

        def _metrics_for(key: str) -> Dict[str, float]:
            metrics = tech_map.get(key)
            if metrics is None:
                metrics = {}
                tech_map[key] = metrics
            if not isinstance(metrics, dict):
                raise TypeError(f"region_technology_metrics[{rid}][{key}] must be a dict")
            return metrics

        hx_output = 0.0
        hx_capacity = 0.0
        for hx_key in hx_keys:
            hx_metrics = _metrics_for(hx_key)
            hx_output += max(0.0, float(hx_metrics.get("initial_energy_output", 0.0) or 0.0))
            hx_capacity += max(0.0, float(hx_metrics.get("initial_capacity", 0.0) or 0.0))

        has_historical_fernwaerme = bool(hx_output > 0.0)

        if not has_historical_fernwaerme:
            for tech_key in list(dict.fromkeys(hx_keys + hg_keys)):
                metrics = _metrics_for(tech_key)
                metrics["initial_energy_output"] = 0.0
                metrics["initial_capacity"] = 0.0
        else:
            historical_exchanger_targets_mwh[rid] = hx_output
            if hx_capacity <= 0.0:
                hx_capacity = max(hx_output / 8760.0, 0.0)

            if not hg_keys and hx_keys:
                _, district = _split_base_and_district(hx_keys[0])
                inferred_hg_key = "heat_grid" if district is None else f"heat_grid_D{district}"
                tech_map[inferred_hg_key] = {
                    "initial_energy_output": 0.0,
                    "initial_capacity": 0.0,
                }
                hg_keys = [inferred_hg_key]

            for hg_key in hg_keys:
                hg_metrics = _metrics_for(hg_key)
                if float(hg_metrics.get("initial_energy_output", 0.0) or 0.0) <= 0.0:
                    hg_metrics["initial_energy_output"] = float(hx_output)
                if float(hg_metrics.get("initial_capacity", 0.0) or 0.0) <= 0.0 and hx_capacity > 0.0:
                    hg_metrics["initial_capacity"] = float(hx_capacity)

    return region_metrics, historical_exchanger_targets_mwh


def parse_dhn_constraints(
    constraints_raw: Mapping[str, Any] | None,
    *,
    demand_commodity: str,
) -> tuple[dict[int, float], dict[int, float]]:
    """Parse DHN-related constraints with existing validation semantics."""
    if constraints_raw is None:
        constraints: Mapping[str, Any] = {}
    elif not isinstance(constraints_raw, Mapping):
        raise ValueError("constraints must be provided as a mapping")
    else:
        constraints = constraints_raw

    min_dhn_targets_raw = constraints.get("min_dhn_throughput_mwh", {})
    if min_dhn_targets_raw and not isinstance(min_dhn_targets_raw, dict):
        raise ValueError("min_dhn_throughput_mwh constraint must be a mapping of region ids to values")

    min_dhn_targets: dict[int, float] = {}
    for key, value in (min_dhn_targets_raw or {}).items():
        rid = to_int_id(key)
        val = float(value)
        if val > 0:
            min_dhn_targets[rid] = float(val)

    min_heat_grid_key = f"min_heat_grid_{demand_commodity}"
    min_heat_grid_raw = constraints.get(min_heat_grid_key, {})
    if min_heat_grid_raw and not isinstance(min_heat_grid_raw, dict):
        raise ValueError(f"{min_heat_grid_key} constraint must be a mapping of region ids to values")

    min_heat_grid_targets: dict[int, float] = {}
    for key, value in (min_heat_grid_raw or {}).items():
        rid = to_int_id(key)
        val = float(value)
        if val > 0:
            min_heat_grid_targets[rid] = float(val)

    if "min_pipe_import_share_by_region" in constraints:
        raise ValueError(
            "Constraint 'min_pipe_import_share_by_region' is no longer supported. "
            "Use exchanger-level constraints ('min_dhn_throughput_mwh' and/or historical exchanger metrics) instead."
        )

    return min_dhn_targets, min_heat_grid_targets


def build_pipe_rows(
    *,
    pipe_connections: Sequence[Any] | None,
) -> list[dict[str, Any]]:
    """Build CESM pipe row payloads from explicit pipe connections."""
    rows: list[dict[str, Any]] = []

    if pipe_connections:
        for pipe in pipe_connections:
            cap_max_mwh_raw = getattr(pipe, "pipe_cap_max_mwh", None)
            capex_base_raw = getattr(pipe, "pipe_capex_base_eur", None)
            capex_base = None if capex_base_raw is None else float(capex_base_raw)
            if capex_base is not None and abs(capex_base) <= 1e-12:
                capex_base = None
            rows.append(
                {
                    "region_id_in": int(pipe.region_id_in),
                    "region_id_out": int(pipe.region_id_out),
                    "efficiency": max(0.0, 1.0 - float(pipe.pipe_loss_fraction)),
                    "technical_lifetime": int(pipe.pipe_lifetime_years),
                    "cap_max": float(pipe.pipe_cap_max_mw),
                    "max_eout": None if cap_max_mwh_raw is None else float(cap_max_mwh_raw),
                    "opex_cost_energy": float(pipe.pipe_opex_eur_per_mwh),
                    "capex_cost_power": float(pipe.pipe_capex_eur_per_mw),
                    "capex_cost_base": capex_base,
                }
            )

    return rows
