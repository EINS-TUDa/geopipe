from __future__ import annotations
import heapq
from math import isfinite
from typing import Dict, Iterable, Tuple
import geopandas as gpd
import pandas as pd


def _demand_street_length_from_segments(
    *,
    street_segments_gdf: gpd.GeoDataFrame,
    district_id: int,
    region_id_column: str,
    demand_segment_flag_column: str = "_is_demand_street",
    demand_segment_value_column: str = "total_heat_demand",
    demand_segment_value_min: float = 0.0,
) -> float:
    if street_segments_gdf is None or street_segments_gdf.empty:
        raise ValueError("street_segments_gdf is required and must be non-empty")
    if region_id_column not in street_segments_gdf.columns:
        raise ValueError(f"Missing required street segment column '{region_id_column}'")

    did = int(district_id)
    rid_numeric = pd.to_numeric(street_segments_gdf[region_id_column], errors="coerce")
    if rid_numeric.notna().any():
        seg = street_segments_gdf.loc[rid_numeric == float(did)].copy()
    else:
        seg = street_segments_gdf.loc[
            street_segments_gdf[region_id_column].astype(str) == str(did)
        ].copy()
    seg = seg.dropna(subset=["geometry"])
    if seg.empty:
        raise ValueError(f"No street segments found for district {did}")

    if demand_segment_flag_column in seg.columns:
        flag = seg[demand_segment_flag_column]
        if pd.api.types.is_bool_dtype(flag):
            demand_seg = seg.loc[flag.fillna(False)]
        else:
            demand_seg = seg.loc[pd.to_numeric(flag, errors="coerce").fillna(0.0) > 0.0]
        return float(demand_seg.geometry.length.sum())

    if demand_segment_value_column in seg.columns:
        demand_indicator = pd.to_numeric(seg[demand_segment_value_column], errors="coerce").fillna(0.0)
        demand_seg = seg.loc[demand_indicator > float(demand_segment_value_min)]
        return float(demand_seg.geometry.length.sum())

    raise ValueError(
        f"Street segments for district {did} do not contain demand markers "
        f"('{demand_segment_flag_column}' or '{demand_segment_value_column}')"
    )

def calculate_district_heat_grid_cost(
    *,
    polygons: gpd.GeoDataFrame,
    district_id: int,
    local_pipe_capex_eur_per_km: float,
    street_segments_gdf: gpd.GeoDataFrame | None = None,
    street_length_column: str = "street_length_m",
    demand_street_length_column: str = "demand_street_length_m",
    nondemand_street_length_column: str = "nondemand_street_length_m",
    region_id_column: str = "id",
    demand_segment_flag_column: str = "_is_demand_street",
    demand_segment_value_column: str = "total_heat_demand",
    demand_segment_value_min: float = 0.0,
) -> Dict[str, float]:
    """Calculate local DHN grid cost for one district.

    Uses minimum in-district demand-connecting pipe length and multiplies by per-km pipe CAPEX.
    """
    if polygons is None or polygons.empty:
        raise ValueError("polygons are required for district heat-grid build")
    if region_id_column not in polygons.columns:
        raise ValueError(f"Missing required polygon column '{region_id_column}'")
    try:
        capex_per_km = float(local_pipe_capex_eur_per_km)
    except (TypeError, ValueError) as exc:
        raise ValueError("local_pipe_capex_eur_per_km must be numeric") from exc
    if not isfinite(capex_per_km) or capex_per_km < 0.0:
        raise ValueError("local_pipe_capex_eur_per_km must be finite and >= 0")

    did = int(district_id)
    rid_numeric = pd.to_numeric(polygons[region_id_column], errors="coerce")
    if rid_numeric.notna().any():
        row_match = polygons.loc[rid_numeric == float(did)]
    else:
        row_match = polygons.loc[polygons[region_id_column].astype(str) == str(did)]
    if row_match.empty:
        raise ValueError(f"No polygon row found for district {did}")
    row = row_match.iloc[0]

    demand_street_m: float | None = None
    if street_segments_gdf is not None and not street_segments_gdf.empty:
        demand_street_m = _demand_street_length_from_segments(street_segments_gdf=street_segments_gdf,district_id=did, region_id_column=region_id_column, demand_segment_flag_column=demand_segment_flag_column, demand_segment_value_column=demand_segment_value_column,demand_segment_value_min=demand_segment_value_min)
    elif demand_street_length_column in polygons.columns:
        try:
            demand_street_m = float(row[demand_street_length_column])
        except (TypeError, ValueError) as exc:
            raise ValueError( f"Invalid demand street length for district {did} in column '{demand_street_length_column}'") from exc

    if demand_street_m is None:
        if street_length_column not in polygons.columns or nondemand_street_length_column not in polygons.columns:
            raise ValueError(f"Cannot infer minimum demand-connecting pipe length for district {did}: "f"need segment data or '{demand_street_length_column}' or both '{street_length_column}' and '{nondemand_street_length_column}'")
        try:
            total_street_m = float(row[street_length_column])
            nondemand_street_m = float(row[nondemand_street_length_column])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid street length inputs for district {did} in columns " f"'{street_length_column}'/'{nondemand_street_length_column}'") from exc
        demand_street_m = max(0.0, total_street_m - nondemand_street_m)

    if demand_street_m < 0.0:
        raise ValueError(f"Negative demand street length for district {did}")

    min_pipe_km = demand_street_m / 1000.0
    return { "min_pipe_km": float(min_pipe_km), "local_grid_capex_base_eur": float(capex_per_km * min_pipe_km)}

def build_district_heat_grid_from_polygons(
    *,
    polygons: gpd.GeoDataFrame,
    local_pipe_capex_eur_per_km: float,
    street_segments_gdf: gpd.GeoDataFrame | None = None,
    street_length_column: str = "street_length_m",
    demand_street_length_column: str = "demand_street_length_m",
    nondemand_street_length_column: str = "nondemand_street_length_m",
    region_id_column: str = "id",
    demand_segment_flag_column: str = "_is_demand_street",
    demand_segment_value_column: str = "total_heat_demand",
    demand_segment_value_min: float = 0.0,
) -> Dict[int, Dict[str, float]]:
    """Build local DHN grid costs for all districts contained in the polygons table.

    This is an aggregator over polygon districts and returns one record per district id.
    For a single district, use calculate_district_heat_grid_cost().

    The per-district cost assumes local grid build should use the least in-district
    pipe needed to connect demand segments. It uses, in priority order:
    - demand-marked street segments from street_segments_gdf,
    - demand_street_length_m when present, or
    - street_length_m - nondemand_street_length_m when available.

    Cost is then: min_pipe_km * local_pipe_capex_eur_per_km.
    """
    records: Dict[int, Dict[str, float]] = {}
    for _, row in polygons.iterrows():
        try:
            did = int(float(row[region_id_column]))
        except (TypeError, ValueError):
            continue
        records[did] = calculate_district_heat_grid_cost(
            polygons=polygons,
            district_id=did,
            local_pipe_capex_eur_per_km=local_pipe_capex_eur_per_km,
            street_segments_gdf=street_segments_gdf,
            street_length_column=street_length_column,
            demand_street_length_column=demand_street_length_column,
            nondemand_street_length_column=nondemand_street_length_column,
            region_id_column=region_id_column,
            demand_segment_flag_column=demand_segment_flag_column,
            demand_segment_value_column=demand_segment_value_column,
            demand_segment_value_min=demand_segment_value_min,
        )
    if not records:
        raise ValueError("No valid district rows found in polygons")
    return records

def _iter_segment_edges(geometry: object) -> Iterable[Tuple[Tuple[float, float], Tuple[float, float], float]]:
    if geometry is None:
        return []
    geoms: list[object]
    geom_type = getattr(geometry, "geom_type", "")
    if geom_type == "LineString":
        geoms = [geometry]
    elif geom_type == "MultiLineString":
        geoms = list(getattr(geometry, "geoms", []) or [])
    else:
        return []

    edges: list[Tuple[Tuple[float, float], Tuple[float, float], float]] = []
    for geom in geoms:
        coords = list(getattr(geom, "coords", []) or [])
        if len(coords) < 2:
            continue
        for idx in range(len(coords) - 1):
            p0 = (float(coords[idx][0]), float(coords[idx][1]))
            p1 = (float(coords[idx + 1][0]), float(coords[idx + 1][1]))
            seg_len = ((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2) ** 0.5
            if seg_len > 0.0:
                edges.append((p0, p1, seg_len))
    return edges

def _shortest_path_length_between_districts(
    *,
    street_segments_gdf: gpd.GeoDataFrame,
    district_i: int,
    district_j: int,
    region_id_column: str,
) -> float:
    if street_segments_gdf is None or street_segments_gdf.empty:
        raise ValueError("street_segments_gdf is required and must be non-empty for inter-DHN shortest-path routing")
    if region_id_column not in street_segments_gdf.columns:
        raise ValueError(f"Missing required street segment column '{region_id_column}'")

    i = int(district_i)
    j = int(district_j)
    if i == j:
        return 0.0

    rid_numeric = pd.to_numeric(street_segments_gdf[region_id_column], errors="coerce")
    seg = street_segments_gdf.copy()
    seg = seg.dropna(subset=["geometry"])
    if seg.empty:
        raise ValueError("street_segments_gdf has no usable geometry rows")

    seg["_rid_num"] = rid_numeric
    allowed_mask = seg["_rid_num"].isna() | (seg["_rid_num"] == float(i)) | (seg["_rid_num"] == float(j))
    allowed = seg.loc[allowed_mask].copy()
    if allowed.empty:
        raise ValueError(f"No allowed street segments for inter-DHN route between districts {i} and {j}")

    start_mask = allowed["_rid_num"] == float(i)
    end_mask = allowed["_rid_num"] == float(j)
    if "_is_demand_street" in allowed.columns:
        demand_marker = allowed["_is_demand_street"]
        if pd.api.types.is_bool_dtype(demand_marker):
            demand_mask = demand_marker.fillna(False)
        else:
            demand_mask = pd.to_numeric(demand_marker, errors="coerce").fillna(0.0) > 0.0
    elif "total_heat_demand" in allowed.columns:
        demand_mask = pd.to_numeric(allowed["total_heat_demand"], errors="coerce").fillna(0.0) > 0.0
    else:
        raise ValueError("Inter-DHN routing requires demand markers on street segments ""('_is_demand_street' or 'total_heat_demand')")

    start_rows = allowed.loc[start_mask & demand_mask]
    end_rows = allowed.loc[end_mask & demand_mask]

    if start_rows.empty or end_rows.empty:
        raise ValueError(
            f"Missing demand-marked district street segments for inter-DHN route between districts {i} and {j}"
        )

    graph: dict[Tuple[float, float], list[Tuple[Tuple[float, float], float]]] = {}

    def _add_edge(a: Tuple[float, float], b: Tuple[float, float], w: float) -> None:
        graph.setdefault(a, []).append((b, w))
        graph.setdefault(b, []).append((a, w))

    for _, row in allowed.iterrows():
        for a, b, w in _iter_segment_edges(row.geometry):
            _add_edge(a, b, w)

    start_nodes: set[Tuple[float, float]] = set()
    end_nodes: set[Tuple[float, float]] = set()
    for _, row in start_rows.iterrows():
        for a, b, _ in _iter_segment_edges(row.geometry):
            start_nodes.add(a)
            start_nodes.add(b)
    for _, row in end_rows.iterrows():
        for a, b, _ in _iter_segment_edges(row.geometry):
            end_nodes.add(a)
            end_nodes.add(b)

    if not start_nodes or not end_nodes:
        raise ValueError(f"Unable to build routing anchors for districts {i} and {j}")

    heap: list[Tuple[float, Tuple[float, float]]] = []
    dist: dict[Tuple[float, float], float] = {}
    for node in start_nodes:
        dist[node] = 0.0
        heapq.heappush(heap, (0.0, node))

    visited: set[Tuple[float, float]] = set()
    while heap:
        dcur, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)
        if node in end_nodes:
            return float(dcur)
        for nb, w in graph.get(node, []):
            nd = dcur + float(w)
            if nd < dist.get(nb, float("inf")):
                dist[nb] = nd
                heapq.heappush(heap, (nd, nb))

    raise ValueError(f"No street-segment path found between districts {i} and {j} using allowed segments")

def build_inter_dhn_pipes_from_street_segments(
    *,
    polygons: gpd.GeoDataFrame,
    street_segments_gdf: gpd.GeoDataFrame,
    pipe_capex_eur_per_km: float,
    region_id_column: str = "id",
    min_interdistrict_pipe_length_m: float = 1.0,
) -> Dict[Tuple[int, int], Dict[str, float]]:
    if polygons is None or polygons.empty:
        raise ValueError("polygons are required for inter-DHN pipe build")
    if region_id_column not in polygons.columns:
        raise ValueError(f"Missing required polygon column '{region_id_column}'")

    try:
        capex_per_km = float(pipe_capex_eur_per_km)
    except (TypeError, ValueError) as exc:
        raise ValueError("pipe_capex_eur_per_km must be numeric") from exc
    if not isfinite(capex_per_km) or capex_per_km < 0.0:
        raise ValueError("pipe_capex_eur_per_km must be finite and >= 0")

    try:
        min_length_m = float(min_interdistrict_pipe_length_m)
    except (TypeError, ValueError) as exc:
        raise ValueError("min_interdistrict_pipe_length_m must be numeric") from exc
    if not isfinite(min_length_m) or min_length_m <= 0.0:
        raise ValueError("min_interdistrict_pipe_length_m must be finite and > 0")

    district_ids = set(pd.to_numeric(polygons[region_id_column], errors="coerce").dropna().astype(int).tolist())
    if not district_ids:
        raise ValueError("No valid district ids found in polygons")

    district_list = sorted(district_ids)
    if len(district_list) < 2:
        return {}

    records: Dict[Tuple[int, int], Dict[str, float]] = {}
    for i in district_list:
        for j in district_list:
            if int(i) == int(j):
                continue
            try:
                length_m = _shortest_path_length_between_districts(
                    street_segments_gdf=street_segments_gdf,
                    district_i=i,
                    district_j=j,
                    region_id_column=region_id_column,
                )
            except ValueError:
                continue

            length_m = max(float(length_m), float(min_length_m))

            min_pipe_km = float(length_m / 1000.0)
            payload = {
                "min_pipe_km": min_pipe_km,
                "pipe_capex_base_eur": float(capex_per_km * min_pipe_km),
            }
            records[(i, j)] = dict(payload)

    return records