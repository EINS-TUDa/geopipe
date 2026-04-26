"""Supports injecting demand and supply into the energy system."""

from __future__ import annotations
from typing import Any
import geopandas as gpd
import pandas as pd
# from pypeline.energy_system.core import EnergySystem


def resolve_region_technology(region: Any, technology_name: str) -> Any:
    candidates = [str(technology_name)]
    if "_D" not in str(technology_name):
        candidates.append(f"{technology_name}_D{region.id}")

    for candidate in candidates:
        hit = next((rt for rt in region.region_technologies if rt.technology.name == candidate), None)
        if hit is not None:
            return hit

    base_matches = [
        rt
        for rt in region.region_technologies
        if rt.technology.name.rsplit("_D", 1)[0] == str(technology_name)
    ]
    if len(base_matches) == 1:
        return base_matches[0]

    raise ValueError(f"Could not resolve technology '{technology_name}' for region {region.id}")


# def apply_injected_techs(energy_system: EnergySystem, injected_techs: list[dict[str, Any]]) -> None:
#     if not injected_techs:
#         return
#
#     region_by_id = {int(region.id): region for region in energy_system.regions}
#
#     for idx, spec in enumerate(injected_techs):
#         if not isinstance(spec, dict):
#             raise ValueError(f"injected_techs[{idx}] must be a mapping")
#
#         region_id_raw = spec.get("region_id")
#         if region_id_raw is None:
#             raise ValueError(f"injected_techs[{idx}] missing 'region_id'")
#
#         region = region_by_id.get(int(region_id_raw))
#         if region is None:
#             raise ValueError(f"Region {region_id_raw} has supply injection but was not built")
#
#         tech_name = str(spec.get("technology", "")).strip()
#         if not tech_name:
#             raise ValueError(f"injected_techs[{idx}] missing 'technology'")
#
#         target = resolve_region_technology(region, tech_name)
#
#         initial_capacity_raw = spec.get("initial_capacity_mw")
#         initial_capacity = float(initial_capacity_raw) if initial_capacity_raw is not None else None
#         if initial_capacity is not None and initial_capacity < 0.0:
#             raise ValueError(f"injected_techs[{idx}].initial_capacity_mw must be >= 0")
#
#         initial_output_raw = spec.get("initial_output_mw")
#         if initial_output_raw is None:
#             initial_output_raw = spec.get("initial_output_mwh")
#
#         initial_output = float(initial_output_raw) if initial_output_raw is not None else None
#         if initial_output is None and initial_capacity is not None:
#             initial_output = initial_capacity
#         if initial_output is not None and initial_output < 0.0:
#             raise ValueError(f"injected_techs[{idx}].initial_output_mw must be >= 0")
#         if initial_output is not None and initial_capacity is not None and initial_output > initial_capacity + 1e-9:
#             raise ValueError(
#                 f"injected_techs[{idx}].initial_output_mw ({initial_output}) must be <= initial_capacity_mw ({initial_capacity})"
#             )
#
#         if initial_capacity is not None:
#             target.initial_capacity = initial_capacity
#         if initial_output is not None:
#             target.initial_energy_output = initial_output


def find_segment_indices(streets: gpd.GeoDataFrame, *, street_id_column: str, segment_id: Any) -> list[int]:
    if street_id_column not in streets.columns:
        raise ValueError(f"Missing street id column '{street_id_column}' in street segments")

    exact = streets.index[streets[street_id_column] == segment_id].tolist()
    if exact:
        return sorted(int(i) for i in exact)

    by_string = streets.index[streets[street_id_column].astype(str) == str(segment_id)].tolist()
    return sorted(int(i) for i in by_string)


def _ensure_numeric_column(gdf: gpd.GeoDataFrame, column_name: str) -> None:
    if column_name not in gdf.columns:
        gdf[column_name] = 0.0
    gdf[column_name] = pd.to_numeric(gdf[column_name], errors="coerce").fillna(0.0).astype(float)


def _resolve_injection_target_index(
    streets: gpd.GeoDataFrame,
    *,
    injection: dict[str, Any],
    street_id_column: str,
) -> int:
    segment_id = injection.get("street_segment_id")
    if segment_id is None:
        raise ValueError("Injection requires 'street_segment_id'")

    idxs = find_segment_indices(streets, street_id_column=street_id_column, segment_id=segment_id)
    if not idxs:
        raise ValueError(f"Injection references unknown street_segment_id '{segment_id}'")
    return int(idxs[0])


def apply_injections(
    streets: gpd.GeoDataFrame,
    *,
    injections: list[dict[str, Any]],
    street_id_column: str,
    demand_column: str,
    region_id_column: str,
) -> tuple[gpd.GeoDataFrame, float, list[dict[str, Any]]]:

    enriched = streets.copy()
    _ensure_numeric_column(enriched, demand_column)
    _ensure_numeric_column(enriched, "injected_demand")

    injected_demand_mwh = 0.0
    injected_techs: list[dict[str, Any]] = []
    region_ids = {int(rid) for rid in enriched[region_id_column].dropna().astype(int).tolist()}

    for inj_idx, injection in enumerate(injections):
        if not isinstance(injection, dict):
            raise ValueError(f"injections[{inj_idx}] must be a mapping")

        injection_type = str(injection.get("type", "")).strip().lower()
        if injection_type not in {"demand", "supply"}:
            raise ValueError(f"injections[{inj_idx}] type must be 'demand' or 'supply'")

        if injection_type == "demand":
            allowed_injection_keys = {
                "type",
                "street_segment_id",
                "magnitude",
                "commodity_type",
            }
            unsupported_keys = sorted(set(injection.keys()) - allowed_injection_keys)
            if unsupported_keys:
                raise ValueError(f"injections[{inj_idx}] has unsupported keys: {unsupported_keys}")

            magnitude_raw = injection.get("magnitude")
            if magnitude_raw is None:
                raise ValueError(f"injections[{inj_idx}] missing 'magnitude'")

            magnitude = float(magnitude_raw)
            if magnitude < 0.0:
                raise ValueError(f"injections[{inj_idx}] magnitude must be >= 0")

            row_idx = _resolve_injection_target_index(
                enriched,
                injection=injection,
                street_id_column=street_id_column,
            )

            demand_delta = float(magnitude)
            if str(demand_column).strip().lower() == "total_heat_demand":
                demand_delta = demand_delta * 1_000_000.0

            enriched.at[row_idx, demand_column] = float(enriched.at[row_idx, demand_column]) + demand_delta
            enriched.at[row_idx, "injected_demand"] = float(enriched.at[row_idx, "injected_demand"]) + demand_delta
            injected_demand_mwh += float(magnitude)

            commodity_raw = injection.get("commodity_type")
            if commodity_raw is not None:
                commodity = str(commodity_raw).strip()
                if not commodity:
                    raise ValueError(f"injections[{inj_idx}].commodity_type must be a non-empty string")
        else:
            allowed_injection_keys = {
                "type",
                "region_id",
                "technology",
                "initial_capacity_mw",
                "initial_output_mw",
                "initial_output_mwh",
            }
            unsupported_keys = sorted(set(injection.keys()) - allowed_injection_keys)
            if unsupported_keys:
                raise ValueError(f"injections[{inj_idx}] has unsupported keys: {unsupported_keys}")

            region_id_raw = injection.get("region_id")
            if region_id_raw is None:
                raise ValueError(f"injections[{inj_idx}] missing 'region_id'")
            region_id = int(region_id_raw)
            if region_id not in region_ids:
                raise ValueError(f"injections[{inj_idx}] references unknown region_id '{region_id}'")

            technology = str(injection.get("technology", "")).strip()
            if not technology:
                raise ValueError(f"injections[{inj_idx}] missing 'technology'")

            initial_capacity_raw = injection.get("initial_capacity_mw")
            if initial_capacity_raw is None:
                raise ValueError(f"injections[{inj_idx}] missing 'initial_capacity_mw'")

            initial_capacity_mw = float(initial_capacity_raw)
            if initial_capacity_mw < 0.0:
                raise ValueError(f"injections[{inj_idx}].initial_capacity_mw must be >= 0")

            initial_output_raw = injection.get("initial_output_mw")
            if initial_output_raw is None:
                initial_output_raw = injection.get("initial_output_mwh")

            initial_output_mw = float(initial_output_raw) if initial_output_raw is not None else initial_capacity_mw
            if initial_output_mw < 0.0:
                raise ValueError(f"injections[{inj_idx}].initial_output_mw must be >= 0")
            if initial_output_mw > initial_capacity_mw + 1e-9:
                raise ValueError(
                    f"injections[{inj_idx}].initial_output_mw ({initial_output_mw}) must be <= initial_capacity_mw ({initial_capacity_mw})"
                )

            injected_techs.append(
                {
                    "region_id": region_id,
                    "technology": technology,
                    "initial_capacity_mw": initial_capacity_mw,
                    "initial_output_mw": initial_output_mw,
                }
            )

    return enriched, float(injected_demand_mwh), injected_techs


__all__ = [
    "resolve_region_technology",
    "find_segment_indices",
    "apply_injections",
]