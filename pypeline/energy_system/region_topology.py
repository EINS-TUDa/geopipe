from __future__ import annotations
from typing import Any
from collections import deque
import heapq
from itertools import combinations
import math
import geopandas as gpd
import pandas as pd
from shapely.ops import substring, unary_union
from shapely.geometry import Point
_JUNCTION_NEIGHBOR_ONLY_MIN_DEGREE = 4

def _to_float(value: Any, default: float=0.0) -> float:
    num = pd.to_numeric(pd.Series([value]), errors='coerce').fillna(float(default)).iloc[0]
    return float(num)

def _owner_map_from_assigned(assigned: pd.DataFrame, *, street_key_col: str, region_id_col: str) -> dict[int, int]:
    return {int(r[street_key_col]): int(r[region_id_col]) for _, r in assigned[[street_key_col, region_id_col]].iterrows()}

def _trace_prev_chain(prev: dict[int, int | None], start: int | None) -> list[int]:
    path: list[int] = []
    cur = None if start is None else int(start)
    while cur is not None:
        path.append(int(cur))
        cur = prev.get(int(cur))
    return path

def _connected_components(adjacency: dict[int, set[int]], nodes: set[int] | None=None) -> list[set[int]]:
    """Return connected components for an undirected adjacency map."""
    remaining = set(adjacency.keys()) if nodes is None else set(nodes)
    out: list[set[int]] = []
    while remaining:
        seed = next(iter(remaining))
        stack = [seed]
        comp: set[int] = set()
        while stack:
            cur = stack.pop()
            if cur in comp:
                continue
            comp.add(cur)
            stack.extend((n for n in adjacency.get(cur, set()) if n in remaining and n not in comp))
        out.append(comp)
        remaining -= comp
    return out

def _max_nondemand_to_demand_ratio_from_share(demand_share_pct: float | None) -> float | None:
    if demand_share_pct is None:
        return None
    share = float(demand_share_pct)
    if share <= 0.0 or share >= 100.0:
        raise ValueError('demand_share_pct must be between 0 and 100')
    return (100.0 - share) / share

def _iter_intersection_points(geom: Any) -> list[Point]:
    if geom is None or geom.is_empty:
        return []
    gtype = geom.geom_type
    if gtype == 'Point':
        return [geom]
    if gtype == 'MultiPoint':
        return [g for g in geom.geoms if g is not None and (not g.is_empty) and (g.geom_type == 'Point')]
    if gtype == 'GeometryCollection':
        out: list[Point] = []
        for gg in geom.geoms:
            out.extend(_iter_intersection_points(gg))
        return out
    return []

def _line_tangent_angle_at_point(line: Any, pt: Point) -> float | None:
    if line is None or line.is_empty or pt is None or pt.is_empty:
        return None
    if line.geom_type == 'MultiLineString':
        parts = [g for g in line.geoms if g is not None and (not g.is_empty)]
        if not parts:
            return None
        line = min(parts, key=lambda g: float(g.distance(pt)))
    if line.geom_type != 'LineString':
        return None
    length = float(line.length)
    if length <= 1e-09:
        return None
    d = float(line.project(pt))
    eps = min(max(length * 1e-06, 0.01), max(length * 0.49, 0.01))
    d1 = max(0.0, d - eps)
    d2 = min(length, d + eps)
    if d2 <= d1:
        return None
    p1 = line.interpolate(d1)
    p2 = line.interpolate(d2)
    dx = float(p2.x) - float(p1.x)
    dy = float(p2.y) - float(p1.y)
    if abs(dx) <= 1e-12 and abs(dy) <= 1e-12:
        return None
    ang = math.atan2(dy, dx)
    if ang < 0:
        ang += 2.0 * math.pi
    return float(ang)

def _street_intersection_context(streets: gpd.GeoDataFrame, *, street_key_col: str, tolerance_m: float) -> tuple[dict[int, Any], set[tuple[int, int]], dict[tuple[float, float], set[int]], dict[tuple[float, float], Point]]:
    base = streets[[street_key_col, 'geometry']].copy()
    base = base.dropna(subset=[street_key_col, 'geometry'])
    if base.empty:
        return ({}, set(), {}, {})
    base[street_key_col] = base[street_key_col].astype(int)
    geom_by_sid: dict[int, Any] = {int(r[street_key_col]): r.geometry for _, r in base.iterrows()}
    probe = base[[street_key_col, 'geometry']].copy()
    if float(tolerance_m) > 0:
        probe['geometry'] = probe.geometry.buffer(float(tolerance_m))
    joined = gpd.sjoin(probe[[street_key_col, 'geometry']], probe[[street_key_col, 'geometry']], how='inner', predicate='intersects')
    candidate_edges: set[tuple[int, int]] = set()
    for _, row in joined.iterrows():
        a = int(row[f'{street_key_col}_left'])
        b = int(row[f'{street_key_col}_right'])
        if a == b:
            continue
        if a > b:
            a, b = (b, a)
        candidate_edges.add((a, b))
    if not candidate_edges:
        return (geom_by_sid, set(), {}, {})
    junction_segments: dict[tuple[float, float], set[int]] = {}
    point_geom_by_key: dict[tuple[float, float], Point] = {}
    for a, b in candidate_edges:
        ga = geom_by_sid.get(int(a))
        gb = geom_by_sid.get(int(b))
        if ga is None or gb is None or ga.is_empty or gb.is_empty:
            continue
        try:
            inter = ga.intersection(gb)
        except Exception:
            continue
        for pt in _iter_intersection_points(inter):
            key = (round(float(pt.x), 3), round(float(pt.y), 3))
            point_geom_by_key[key] = pt
            junction_segments.setdefault(key, set()).update({int(a), int(b)})
    return (geom_by_sid, candidate_edges, junction_segments, point_geom_by_key)

def _build_street_adjacency(streets: gpd.GeoDataFrame, *, street_key_col: str, tolerance_m: float, junction_neighbor_only_min_degree: int=_JUNCTION_NEIGHBOR_ONLY_MIN_DEGREE) -> dict[int, set[int]]:
    if streets.empty:
        return {}
    base = streets[[street_key_col, 'geometry']].copy().dropna(subset=[street_key_col, 'geometry'])
    if base.empty:
        return {}
    base[street_key_col] = base[street_key_col].astype(int)
    adjacency: dict[int, set[int]] = {int(v): set() for v in base[street_key_col].tolist()}
    geom_by_sid, candidate_edges, junction_segments, point_geom_by_key = _street_intersection_context(base, street_key_col=street_key_col, tolerance_m=float(tolerance_m))
    if not candidate_edges:
        return adjacency
    forbidden_edges: set[tuple[int, int]] = set()
    for key, segs in junction_segments.items():
        if len(segs) < int(junction_neighbor_only_min_degree):
            continue
        pt = point_geom_by_key.get(key)
        if pt is None:
            continue
        angle_by_sid: dict[int, float] = {}
        for sid in sorted((int(v) for v in segs)):
            ang = _line_tangent_angle_at_point(geom_by_sid.get(int(sid)), pt)
            if ang is None:
                continue
            angle_by_sid[int(sid)] = float(ang)
        if len(angle_by_sid) < int(junction_neighbor_only_min_degree):
            continue
        ordered = sorted(angle_by_sid.keys(), key=lambda sid: angle_by_sid[sid])
        n = len(ordered)
        neighbor_pairs: set[tuple[int, int]] = set()
        for i in range(n):
            a = int(ordered[i])
            b = int(ordered[(i + 1) % n])
            if a > b:
                a, b = (b, a)
            neighbor_pairs.add((a, b))
        for a, b in combinations(sorted(segs), 2):
            aa, bb = (int(a), int(b))
            if aa > bb:
                aa, bb = (bb, aa)
            if (aa, bb) not in neighbor_pairs:
                forbidden_edges.add((aa, bb))
    for a, b in candidate_edges:
        if (int(a), int(b)) in forbidden_edges:
            continue
        adjacency[int(a)].add(int(b))
        adjacency[int(b)].add(int(a))
    return adjacency

def _build_overloaded_junction_segments(streets: gpd.GeoDataFrame, *, street_key_col: str, tolerance_m: float, min_degree: int=_JUNCTION_NEIGHBOR_ONLY_MIN_DEGREE) -> list[set[int]]:
    if streets.empty:
        return []
    base = streets[[street_key_col, 'geometry']].copy().dropna(subset=[street_key_col, 'geometry'])
    if base.empty:
        return []
    base[street_key_col] = base[street_key_col].astype(int)
    _, candidate_edges, junction_segments, _ = _street_intersection_context(base, street_key_col=street_key_col, tolerance_m=float(tolerance_m))
    if not candidate_edges:
        return []
    return [set((int(v) for v in segs)) for segs in junction_segments.values() if len(segs) >= int(min_degree)]

def _segment_streets_by_building_projections(streets: gpd.GeoDataFrame, buildings: gpd.GeoDataFrame, *, street_id_column: str, projection_buffer_m: float) -> gpd.GeoDataFrame:
    if streets.empty or buildings.empty or projection_buffer_m <= 0:
        return streets
    lines = streets[[street_id_column, 'geometry']].copy().reset_index(drop=True)
    bpts = buildings[['geometry']].copy().reset_index(drop=True)
    bpts['geometry'] = bpts.geometry.representative_point()
    line_probe = lines[['geometry']].copy()
    line_probe['_line_idx'] = line_probe.index
    line_probe['geometry'] = line_probe.geometry.buffer(float(projection_buffer_m))
    b_probe = bpts[['geometry']].copy()
    b_probe['_b_idx'] = b_probe.index
    pairs = gpd.sjoin(line_probe[['_line_idx', 'geometry']], b_probe[['_b_idx', 'geometry']], how='inner', predicate='intersects')
    if pairs.empty:
        return lines
    split_dists: dict[int, set[float]] = {}
    bgeom = bpts.geometry.to_dict()
    eps = 1e-06
    for _, row in pairs.iterrows():
        li = int(row['_line_idx'])
        bi = int(row['_b_idx'])
        line = lines.geometry.iloc[li]
        pt = bgeom.get(bi)
        if line is None or line.is_empty or pt is None or pt.is_empty:
            continue
        d = float(line.project(pt))
        if d <= eps or d >= float(line.length) - eps:
            continue
        split_dists.setdefault(li, set()).add(round(d, 3))
    if not split_dists:
        return lines
    out_rows: list[dict[str, Any]] = []
    for li, row in lines.iterrows():
        line = row.geometry
        if li not in split_dists or line is None or line.is_empty:
            attrs = row.drop(labels=['geometry']).to_dict()
            attrs['geometry'] = line
            out_rows.append(attrs)
            continue
        dists = sorted((float(d) for d in split_dists[li]))
        length = float(line.length)
        cuts = [0.0] + [d for d in dists if 0.0 < d < length] + [length]
        geoms: list[Any] = []
        for a, b in zip(cuts[:-1], cuts[1:]):
            if b - a <= eps:
                continue
            try:
                seg = substring(line, float(a), float(b))
            except Exception:
                continue
            if seg is None or seg.is_empty:
                continue
            geoms.append(seg)
        for geom in geoms or [line]:
            attrs = row.drop(labels=['geometry']).to_dict()
            attrs['geometry'] = geom
            out_rows.append(attrs)
    return gpd.GeoDataFrame(out_rows, geometry='geometry', crs=streets.crs)

def polygon_builder(buildings: gpd.GeoDataFrame, streets: gpd.GeoDataFrame, *, demand_data: pd.DataFrame | gpd.GeoDataFrame | None=None, demand_value_column: str='annual_demand_mwh', street_id_column: str='street_id', building_street_column: str | None=None, building_id_column: str | None=None, demand_street_column: str | None=None, demand_building_column: str | None=None, district_column: str | None=None, crs: str | None=None, id_column: str='id', building_buffer_m: float=2.0, street_buffer_m: float=8.0, segment_streets_by_building_projections: bool=False, segment_projection_buffer_m: float | None=None, demand_street_indicator_column: str | None=None, demand_street_indicator_min: float=0.0, street_corridor_buffer_m: float=15.0, caps: dict[str, float | None] | None=None, demand_share_pct: float | None=None, enforce_demand_gt_nondemand: bool=False, max_inter_region_connector_m: float | None=100.0, street_connect_tolerance_m: float=3.0, enforce_contiguous_polygons: bool=False, smooth_distance_m: float=0.0, keep_internal_columns: bool=False, min_seed_demand_mwh: float=1e-09) -> gpd.GeoDataFrame:
    if buildings.empty:
        raise ValueError('polygon_builder requires non-empty buildings')
    if streets.empty:
        raise ValueError('polygon_builder requires non-empty streets')
    if street_id_column not in streets.columns:
        raise ValueError(f"Missing '{street_id_column}' in streets")
    if demand_street_indicator_column and demand_street_indicator_column not in streets.columns:
        raise ValueError(f"Missing '{demand_street_indicator_column}' in streets")
    b = buildings.copy()
    s = streets.copy()
    if crs:
        if b.crs is None:
            b = b.set_crs(crs)
        if s.crs is None:
            s = s.set_crs(crs)
    if b.crs is None or s.crs is None:
        raise ValueError('buildings and streets must have CRS (or pass crs=...) to polygon_builder')
    if b.crs != s.crs:
        s = s.to_crs(b.crs)
    s_cols = [street_id_column, 'geometry']
    if demand_street_indicator_column:
        s_cols.append(demand_street_indicator_column)
    s_work = s[s_cols].copy().explode(index_parts=False).reset_index(drop=True)
    if segment_streets_by_building_projections:
        seg_buffer_m = float(street_buffer_m if segment_projection_buffer_m is None else segment_projection_buffer_m)
        s_work = _segment_streets_by_building_projections(s_work, b, street_id_column=street_id_column, projection_buffer_m=seg_buffer_m)
    s_work['_street_key'] = range(len(s_work))
    street_key_col = '_street_key'
    street_key_to_raw_id = s_work.set_index(street_key_col)[street_id_column].to_dict()
    cap_cfg = {'max_demand_mwh': None, 'max_street_length_km': 10.0, 'max_nondemand_street_km': None, **dict(caps or {})}
    assignment_col = '_assigned_street'
    def _fill_nearest_street_assignment(mask: pd.Series) -> None:
        if not bool(mask.any()):
            return
        nearest = gpd.sjoin_nearest(b.loc[mask, ['geometry']], s_work[[street_key_col, 'geometry']], how='left')
        nearest_assigned = nearest.groupby(level=0)[street_key_col].first()
        b.loc[mask, assignment_col] = b.loc[mask].index.to_series().map(nearest_assigned)

    b[assignment_col] = pd.NA
    if building_street_column and building_street_column in b.columns:
        raw_to_keys = s_work.groupby(street_id_column, dropna=False)[street_key_col].apply(list).to_dict()
        by_id_mask = b[building_street_column].isin(raw_to_keys.keys())
        if bool(by_id_mask.any()):
            b.loc[by_id_mask, assignment_col] = b.loc[by_id_mask, building_street_column].map(lambda rid: raw_to_keys.get(rid, [None])[0])
    else:
        street_zones = s_work[[street_key_col, 'geometry']].copy()
        street_zones['geometry'] = street_zones.geometry.buffer(float(street_buffer_m))
        joined = gpd.sjoin(b[['geometry']], street_zones, how='left', predicate='intersects')
        assigned = joined.groupby(level=0)[street_key_col].first()
        b[assignment_col] = b.index.to_series().map(assigned)
    _fill_nearest_street_assignment(b[assignment_col].isna())
    if b[assignment_col].isna().all():
        raise ValueError('No buildings could be assigned to streets')
    polygon_demand_column = demand_value_column
    b[polygon_demand_column] = 0.0
    if demand_data is not None:
        d = pd.DataFrame(demand_data).copy()
        if demand_value_column not in d.columns:
            raise ValueError(f"Missing demand value column '{demand_value_column}' in demand_data")
        demand_series = None
        if demand_street_column and demand_street_column in d.columns:
            demand_by_street = d.groupby(demand_street_column, dropna=False)[demand_value_column].sum(min_count=1).fillna(0.0)
            demand_series = b[assignment_col].map(street_key_to_raw_id).map(demand_by_street)
        elif demand_building_column and building_id_column and (demand_building_column in d.columns) and (building_id_column in b.columns):
            demand_by_building = d.groupby(demand_building_column, dropna=False)[demand_value_column].sum(min_count=1).fillna(0.0)
            demand_series = b[building_id_column].map(demand_by_building)
        if demand_series is None:
            raise ValueError('demand_data mapping failed')
        b[polygon_demand_column] = demand_series.fillna(0.0)
    max_demand = None if cap_cfg['max_demand_mwh'] is None else float(cap_cfg['max_demand_mwh'])
    max_street_length_m = None if cap_cfg['max_street_length_km'] is None else float(cap_cfg['max_street_length_km']) * 1000.0
    max_nondemand_street_m = None if cap_cfg['max_nondemand_street_km'] is None else float(cap_cfg['max_nondemand_street_km']) * 1000.0
    max_inter_region_connector_len_m = None if max_inter_region_connector_m is None else float(max_inter_region_connector_m)
    max_nondemand_ratio = _max_nondemand_to_demand_ratio_from_share(demand_share_pct)
    min_seed_demand = float(min_seed_demand_mwh)
    if min_seed_demand < 0:
        raise ValueError('min_seed_demand_mwh must be >= 0')
    min_demand_indicator = float(demand_street_indicator_min)
    street_lengths_m = s_work.set_index(street_key_col).geometry.length.fillna(0.0)
    demand_indicator_by_street = None
    if demand_street_indicator_column and demand_street_indicator_column in s_work.columns:
        demand_indicator_by_street = pd.to_numeric(s_work[demand_street_indicator_column], errors='coerce').fillna(0.0)
        demand_indicator_by_street.index = s_work[street_key_col].astype(int)
    group_cols = [assignment_col]
    if district_column and district_column in b.columns:
        group_cols.append(district_column)
    node_rows: list[dict[str, Any]] = []
    node_id = 0
    grouped = b.dropna(subset=[assignment_col]).groupby(group_cols, dropna=False)
    for group_key, subset in grouped:
        subset_demand = float(subset[polygon_demand_column].sum())
        street_key = group_key[0] if isinstance(group_key, tuple) else group_key
        if demand_data is not None:
            if subset_demand <= min_seed_demand:
                continue
            if demand_indicator_by_street is not None:
                if float(demand_indicator_by_street.get(int(street_key), 0.0)) <= min_demand_indicator:
                    continue
        buffered = [geom.buffer(float(building_buffer_m)) for geom in subset.geometry if geom is not None]
        if not buffered:
            continue
        poly = unary_union(buffered).buffer(0)
        if smooth_distance_m > 0:
            poly = poly.buffer(float(smooth_distance_m)).buffer(-float(smooth_distance_m))
        if poly is None or poly.is_empty:
            continue
        street_len_m = float(street_lengths_m.get(street_key, 0.0))
        rec: dict[str, Any] = {'_node_id': node_id, street_id_column: street_key_to_raw_id.get(street_key), 'street_length_m': street_len_m, 'building_count': int(len(subset)), polygon_demand_column: subset_demand, '_street_members': [street_key], 'geometry': poly}
        if isinstance(group_key, tuple) and district_column and (len(group_key) > 1):
            rec[district_column] = group_key[1]
        node_rows.append(rec)
        node_id += 1
    if not node_rows:
        raise ValueError('polygon_builder produced no polygons')
    nodes = gpd.GeoDataFrame(node_rows, geometry='geometry', crs=b.crs)
    street_geom = s_work.set_index(street_key_col)['geometry'].to_dict()
    full_adj = _build_street_adjacency(s_work[[street_key_col, 'geometry']], street_key_col=street_key_col, tolerance_m=float(street_connect_tolerance_m))
    street_comp: dict[int, int] = {int(sid): int(comp_id) for comp_id, street_nodes in enumerate(_connected_components(full_adj)) for sid in street_nodes}
    demand_street_to_node: dict[int, int] = {}
    for _, row in nodes.iterrows():
        node_id_i = int(row['_node_id'])
        s_members = row.get('_street_members', [])
        if not isinstance(s_members, list) or not s_members:
            continue
        sid0 = int(s_members[0])
        cid = street_comp.get(sid0)
        if cid is None:
            continue
        demand_street_to_node[sid0] = node_id_i
    demand_street_keys = set(demand_street_to_node.keys())
    node_by_id: dict[int, dict[str, Any]] = {int(row['_node_id']): row.to_dict() for _, row in nodes.iterrows()}
    demand_by_sid: dict[int, float] = {int(sid): float(node_by_id[int(nid)].get(polygon_demand_column, 0.0)) for sid, nid in demand_street_to_node.items()}
    node_by_sid: dict[int, int] = {int(sid): int(nid) for sid, nid in demand_street_to_node.items()}

    def _fits_caps(*, demand_sum: float, street_len_sum: float) -> bool:
        return (max_demand is None or demand_sum <= max_demand) and (max_street_length_m is None or street_len_sum <= max_street_length_m)
    demand_adj: dict[int, set[int]] = {int(sid): {int(nb) for nb in full_adj.get(int(sid), set()) if nb in demand_street_keys and nb != sid} for sid in demand_street_keys}

    def _bfs_dist(seed: int, region: set[int]) -> dict[int, int]:
        dist: dict[int, int] = {int(seed): 0}
        q = deque([int(seed)])
        while q:
            cur = q.popleft()
            for nb in demand_adj.get(cur, set()):
                if nb not in region or nb in dist:
                    continue
                dist[nb] = dist[cur] + 1
                q.append(nb)
        return dist

    def _split_region(region_demand_sids: set[int]) -> list[set[int]]:
        region = set((int(s) for s in region_demand_sids))
        if not region:
            return []
        dsum = float(sum((demand_by_sid.get(sid, 0.0) for sid in region)))
        lsum = float(sum((float(street_lengths_m.get(sid, 0.0)) for sid in region)))
        if _fits_caps(demand_sum=dsum, street_len_sum=lsum):
            return [region]
        if len(region) <= 1:
            raise RuntimeError('polygon_builder infeasible: single demand street exceeds caps')
        seed_a = max(region, key=lambda sid: demand_by_sid.get(sid, 0.0))
        dist_a = _bfs_dist(seed_a, region)
        seed_b = max(region - {seed_a}, key=lambda sid: dist_a.get(sid, -1)) if len(region) > 1 else seed_a
        da = _bfs_dist(seed_a, region)
        db = _bfs_dist(seed_b, region)
        part_a: set[int] = set()
        part_b: set[int] = set()
        dem_a = 0.0
        dem_b = 0.0
        for sid in sorted(region):
            va = da.get(sid, 10 ** 9)
            vb = db.get(sid, 10 ** 9)
            if va < vb:
                part_a.add(sid)
                dem_a += demand_by_sid.get(sid, 0.0)
            elif vb < va:
                part_b.add(sid)
                dem_b += demand_by_sid.get(sid, 0.0)
            elif dem_a <= dem_b:
                part_a.add(sid)
                dem_a += demand_by_sid.get(sid, 0.0)
            else:
                part_b.add(sid)
                dem_b += demand_by_sid.get(sid, 0.0)
        if not part_a or not part_b:
            vals = sorted(region)
            half = len(vals) // 2
            part_a, part_b = (set(vals[:half]), set(vals[half:]))
        out: list[set[int]] = []
        out.extend(_split_region(part_a))
        out.extend(_split_region(part_b))
        return out
    initial_regions: list[dict[str, set[int]]] = []
    for comp in _connected_components(demand_adj, set(demand_street_keys)):
        for part in _split_region(set((int(s) for s in comp))):
            initial_regions.append({'demand': set(part), 'connectors': set()})
    if not initial_regions:
        raise ValueError('polygon_builder produced no regions')

    def _region_streets(r: dict[str, set[int]]) -> set[int]:
        return set((int(s) for s in r['demand'])) | set((int(s) for s in r['connectors']))

    def _region_stats(r: dict[str, set[int]]) -> tuple[float, float, float, float]:
        demand_len = float(sum((float(street_lengths_m.get(sid, 0.0)) for sid in r['demand'])))
        nondemand_len = float(sum((float(street_lengths_m.get(sid, 0.0)) for sid in r['connectors'])))
        demand_sum = float(sum((demand_by_sid.get(sid, 0.0) for sid in r['demand'])))
        total_len = demand_len + nondemand_len
        return (demand_sum, total_len, demand_len, nondemand_len)
    regions: list[dict[str, set[int]] | None] = [dict(demand=set(r['demand']), connectors=set()) for r in initial_regions]

    def _try_merge_from(src_idx: int) -> tuple[int, set[int]] | None:
        src = regions[src_idx]
        if src is None:
            return None
        src_streets = _region_streets(src)
        if not src_streets:
            return None
        owner: dict[int, int] = {int(sid): int(idx_r) for idx_r, rr in enumerate(regions) if rr is not None for sid in _region_streets(rr)}
        dist: dict[int, float] = {sid: 0.0 for sid in src_streets}
        prev: dict[int, int | None] = {sid: None for sid in src_streets}
        pq: list[tuple[float, int]] = [(0.0, sid) for sid in src_streets]
        heapq.heapify(pq)
        hit_cost: dict[int, float] = {}
        hit_prev: dict[int, int] = {}
        while pq:
            cur_cost, cur = heapq.heappop(pq)
            if cur_cost > dist.get(cur, float('inf')):
                continue
            for nb in full_adj.get(cur, set()):
                nb_owner = owner.get(int(nb))
                if nb_owner is not None and nb_owner != src_idx:
                    if cur_cost < hit_cost.get(nb_owner, float('inf')):
                        hit_cost[nb_owner] = cur_cost
                        hit_prev[nb_owner] = int(cur)
                    continue
                if nb_owner is not None and nb_owner == src_idx:
                    step = 0.0
                else:
                    step = float(street_lengths_m.get(nb, 0.0))
                cand = cur_cost + step
                if max_inter_region_connector_len_m is not None and cand > max_inter_region_connector_len_m:
                    continue
                if cand < dist.get(int(nb), float('inf')):
                    dist[int(nb)] = cand
                    prev[int(nb)] = int(cur)
                    heapq.heappush(pq, (cand, int(nb)))
        src_demand, _, src_demand_len, src_non_len = _region_stats(src)
        best: tuple[float, int, int, set[int]] | None = None
        for tgt_idx, cost in hit_cost.items():
            tgt = regions[tgt_idx]
            if tgt is None:
                continue
            path_nodes = _trace_prev_chain(prev, hit_prev.get(tgt_idx))
            connector_add = {sid for sid in path_nodes if owner.get(int(sid)) is None}
            tgt_demand, _, tgt_demand_len, tgt_non_len = _region_stats(tgt)
            merged_demand = src_demand + tgt_demand
            merged_demand_len = src_demand_len + tgt_demand_len
            merged_non_len = src_non_len + tgt_non_len + float(sum((float(street_lengths_m.get(sid, 0.0)) for sid in connector_add)))
            merged_total_len = merged_demand_len + merged_non_len
            if not _fits_caps(demand_sum=merged_demand, street_len_sum=merged_total_len):
                continue
            if max_nondemand_street_m is not None and merged_non_len > max_nondemand_street_m:
                continue
            if enforce_demand_gt_nondemand and merged_non_len >= merged_demand_len and (merged_demand_len > 0):
                continue
            if max_nondemand_ratio is not None and merged_demand_len > 0 and (merged_non_len / merged_demand_len >= max_nondemand_ratio):
                continue
            tgt_size = len(_region_streets(tgt))
            key = (float(cost), int(tgt_size), int(tgt_idx))
            if best is None or key < (best[0], best[1], best[2]):
                best = (float(cost), int(tgt_size), int(tgt_idx), set(connector_add))
        if best is None:
            return None
        return (best[2], best[3])
    while True:
        active = [(i, r) for i, r in enumerate(regions) if r is not None]
        if not active:
            break
        size_levels = sorted({len(_region_streets(r)) for _, r in active})
        changed_any = False
        for size_k in size_levels:
            while True:
                merged_this_size = False
                candidates = [i for i, r in enumerate(regions) if r is not None and len(_region_streets(r)) == size_k]
                for src_idx in candidates:
                    best = _try_merge_from(src_idx)
                    if best is None:
                        continue
                    tgt_idx, add_conn = best
                    if regions[tgt_idx] is None or regions[src_idx] is None:
                        continue
                    regions[tgt_idx]['demand'] = set(regions[tgt_idx]['demand']) | set(regions[src_idx]['demand'])
                    regions[tgt_idx]['connectors'] = set(regions[tgt_idx]['connectors']) | set(regions[src_idx]['connectors']) | set(add_conn)
                    regions[src_idx] = None
                    merged_this_size = True
                    changed_any = True
                    break
                if not merged_this_size:
                    break
        if not changed_any:
            break
    cluster_records: list[dict[str, Any]] = []
    for reg in [r for r in regions if r is not None]:
        reg_streets = sorted((int(sid) for sid in _region_streets(reg)))
        if not reg_streets:
            continue
        demand_set = set((int(sid) for sid in reg['demand']))
        member_nodes = [node_by_sid[sid] for sid in demand_set if sid in node_by_sid]
        geom_inputs: list[Any] = []
        for nid in member_nodes:
            g = node_by_id[nid].get('geometry')
            if g is not None and (not g.is_empty):
                geom_inputs.append(g)
        corridor_buffer = float(street_corridor_buffer_m)
        if corridor_buffer > 0:
            for sid in reg_streets:
                sg = street_geom.get(sid)
                if sg is None or sg.is_empty:
                    continue
                geom_inputs.append(sg.buffer(corridor_buffer))
        emit_geom = unary_union(geom_inputs).buffer(0) if geom_inputs else None
        if emit_geom is None or emit_geom.is_empty:
            continue
        if enforce_contiguous_polygons:
            emit_geom = emit_geom.convex_hull
        demand_len_m = float(sum((float(street_lengths_m.get(sid, 0.0)) for sid in reg_streets if sid in demand_set)))
        nondemand_len_m = float(sum((float(street_lengths_m.get(sid, 0.0)) for sid in reg_streets if sid not in demand_set)))
        total_len_m = demand_len_m + nondemand_len_m
        total_demand = float(sum((float(demand_by_sid.get(sid, 0.0)) for sid in demand_set)))
        if not _fits_caps(demand_sum=total_demand, street_len_sum=total_len_m):
            continue
        out_row: dict[str, Any] = {street_id_column: '|'.join(map(str, list(dict.fromkeys((street_key_to_raw_id.get(sid) for sid in reg_streets))))), 'street_count': len(reg_streets), 'street_length_m': total_len_m, 'nondemand_street_length_m': nondemand_len_m, 'building_count': int(sum((float(node_by_id[n].get('building_count', 0.0)) for n in member_nodes))), polygon_demand_column: total_demand, '_street_members': reg_streets, '_demand_street_members': sorted((int(sid) for sid in demand_set)), 'geometry': emit_geom}
        if district_column and district_column in nodes.columns and member_nodes:
            out_row[district_column] = node_by_id[member_nodes[0]].get(district_column)
        cluster_records.append(out_row)
    polygons = gpd.GeoDataFrame(cluster_records, geometry='geometry', crs=b.crs)
    if '_street_members' in polygons.columns:
        polygons['_sort_min_street_key'] = polygons['_street_members'].map(lambda v: min((int(x) for x in v)) if isinstance(v, list) and len(v) > 0 else 10 ** 15)
        polygons = polygons.sort_values(['_sort_min_street_key'], kind='mergesort')
        polygons = polygons.drop(columns=['_sort_min_street_key'], errors='ignore')
    polygons = polygons.reset_index(drop=True)
    polygons[id_column] = range(len(polygons))
    if not keep_internal_columns and '_street_members' in polygons.columns:
        polygons = polygons.drop(columns=['_street_members'])
    return polygons

def filter_streets_for_buildings(buildings: gpd.GeoDataFrame, streets: gpd.GeoDataFrame, *, clip_buffer_m: float=250.0) -> gpd.GeoDataFrame:
    if buildings.crs is None or streets.crs is None:
        raise ValueError('buildings and streets must both have CRS')
    s = streets if buildings.crs == streets.crs else streets.to_crs(buildings.crs)
    if buildings.empty:
        return s.iloc[0:0].copy()
    area = buildings.geometry.union_all().convex_hull.buffer(float(clip_buffer_m))
    return s[s.geometry.intersects(area)].copy()

def _street_segments_with_region(streets: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame, *, buildings: gpd.GeoDataFrame | None, segment_streets_by_building_projections: bool, segment_projection_buffer_m: float, region_id_column: str, street_id_column: str) -> gpd.GeoDataFrame:
    s = streets[[street_id_column, 'geometry']].copy().explode(index_parts=False).reset_index(drop=True)
    if segment_streets_by_building_projections and buildings is not None and (not buildings.empty):
        s = _segment_streets_by_building_projections(s, buildings, street_id_column=street_id_column, projection_buffer_m=float(segment_projection_buffer_m))
    s['_street_key'] = range(len(s))
    if '_street_members' not in polygons.columns:
        raise ValueError("Expected '_street_members' in polygons")
    rid_by_key: dict[int, int] = {}
    demand_keys: set[int] = set()
    for _, r in polygons[[region_id_column, '_street_members']].iterrows():
        if not isinstance(r['_street_members'], list):
            continue
        for k in r['_street_members']:
            rid_by_key[int(k)] = int(r[region_id_column])
    if '_demand_street_members' in polygons.columns:
        for vals in polygons['_demand_street_members'].tolist():
            if isinstance(vals, list):
                demand_keys.update((int(v) for v in vals))
    s[region_id_column] = s['_street_key'].map(rid_by_key)
    s['_is_demand_street'] = s['_street_key'].isin(demand_keys)
    return s

def _count_disconnected_regions(streets: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame, *, tolerance_m: float, buildings: gpd.GeoDataFrame | None, segment_streets_by_building_projections: bool, segment_projection_buffer_m: float, street_id_column: str) -> int:
    if '_street_members' not in polygons.columns:
        return 0
    s = streets[[street_id_column, 'geometry']].copy().explode(index_parts=False).reset_index(drop=True)
    if segment_streets_by_building_projections and buildings is not None and (not buildings.empty):
        s = _segment_streets_by_building_projections(s, buildings, street_id_column=street_id_column, projection_buffer_m=float(segment_projection_buffer_m))
    s['_street_key'] = range(len(s))
    adj = _build_street_adjacency(s[['_street_key', 'geometry']], street_key_col='_street_key', tolerance_m=float(tolerance_m))
    bad = 0
    for _, prow in polygons.iterrows():
        m = prow.get('_street_members', [])
        if not isinstance(m, list) or len(m) <= 1:
            continue
        ms = set((int(x) for x in m))
        seen, stack = (set(), [next(iter(ms))])
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend((nb for nb in adj.get(cur, set()) if nb in ms and nb not in seen))
        bad += int(seen != ms)
    return bad

def _merge_small_regions_by_street_graph(*, polygons: gpd.GeoDataFrame, streets_with_region: gpd.GeoDataFrame, region_id_column: str, small_islands_max_segments: int, tolerance_m: float, max_connector_m: float, max_demand_mwh: float | None, max_street_length_km: float | None, demand_share_pct: float | None, enable_final_resort: bool) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    if region_id_column not in streets_with_region.columns:
        return (polygons, streets_with_region)
    p = polygons.copy()
    s = streets_with_region.copy()
    max_nondemand_to_demand_ratio = _max_nondemand_to_demand_ratio_from_share(demand_share_pct)
    split_region_min_segments = max(2, int(small_islands_max_segments))
    street_demand_mwh: dict[int, float] = {}
    for _, row in p[['_demand_street_members', 'annual_demand_mwh']].iterrows():
        members = row.get('_demand_street_members', [])
        if not isinstance(members, list) or not members:
            continue
        dsum = _to_float(row.get('annual_demand_mwh', 0.0), 0.0)
        per = dsum / float(len(members)) if members else 0.0
        for sid in members:
            sid_i = int(sid)
            street_demand_mwh[sid_i] = float(street_demand_mwh.get(sid_i, 0.0) + per)
    base = s[['_street_key', 'geometry', region_id_column]].copy()
    base['_street_key'] = base['_street_key'].astype(int)
    lengths = {int(r['_street_key']): float(r.geometry.length if r.geometry is not None else 0.0) for _, r in base.iterrows()}
    adj = _build_street_adjacency(base[['_street_key', 'geometry']], street_key_col='_street_key', tolerance_m=float(tolerance_m))
    overloaded_junctions = _build_overloaded_junction_segments(base[['_street_key', 'geometry']], street_key_col='_street_key', tolerance_m=float(tolerance_m), min_degree=int(_JUNCTION_NEIGHBOR_ONLY_MIN_DEGREE))
    overloaded_by_sid: dict[int, set[int]] = {}
    for jidx, segs in enumerate(overloaded_junctions):
        for sid in segs:
            overloaded_by_sid.setdefault(int(sid), set()).add(int(jidx))

    def _has_overloaded_multi_region_conflict(owner_by_sid: dict[int, int], *, street_scope: set[int] | None=None) -> bool:
        if not overloaded_junctions:
            return False
        jidxs: set[int]
        if street_scope is None:
            jidxs = set(range(len(overloaded_junctions)))
        else:
            jidxs = set()
            for sid in street_scope:
                jidxs |= set((int(v) for v in overloaded_by_sid.get(int(sid), set())))
        for jidx in jidxs:
            segs = overloaded_junctions[int(jidx)]
            regs = set((int(owner_by_sid[int(sid)]) for sid in segs if int(sid) in owner_by_sid))
            if len(regs) > 1:
                return True
        return False

    def _is_connected_nodes(nodes: set[int]) -> bool:
        if len(nodes) <= 1:
            return True
        seed = next(iter(nodes))
        seen: set[int] = set()
        stack: list[int] = [int(seed)]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for nb in adj.get(cur, set()):
                if nb in nodes and nb not in seen:
                    stack.append(int(nb))
        return seen == nodes

    def _streets_by_region_from_assigned(assigned_df: pd.DataFrame) -> dict[int, set[int]]:
        out: dict[int, set[int]] = {}
        for _, rr in assigned_df[['_street_key', region_id_column]].iterrows():
            out.setdefault(int(rr[region_id_column]), set()).add(int(rr['_street_key']))
        return out

    def _demand_totals(streets_by_region_map: dict[int, set[int]]) -> dict[int, float]:
        return {rid_i: float(sum((float(street_demand_mwh.get(int(sid), 0.0)) for sid in sids))) for rid_i, sids in streets_by_region_map.items()}

    def _length_totals(streets_by_region_map: dict[int, set[int]]) -> dict[int, float]:
        return {rid_i: float(sum((float(lengths.get(sid, 0.0)) for sid in sids))) for rid_i, sids in streets_by_region_map.items()}

    def _is_region_connected_under_owner(owner_by_sid: dict[int, int], rid: int) -> bool:
        nodes = set((int(sid) for sid, owner_rid in owner_by_sid.items() if int(owner_rid) == int(rid)))
        return _is_connected_nodes(nodes)

    def _collapse_overloaded_junction_regions() -> None:
        if not overloaded_junctions:
            return
        assigned_now = s.dropna(subset=[region_id_column]).copy()
        if assigned_now.empty:
            return
        owner_now = _owner_map_from_assigned(assigned_now, street_key_col='_street_key', region_id_col=region_id_column)
        changed_local = False
        for segs in overloaded_junctions:
            counts: dict[int, int] = {}
            for sid in segs:
                rid = owner_now.get(int(sid))
                if rid is None:
                    continue
                counts[int(rid)] = int(counts.get(int(rid), 0) + 1)
            if len(counts) <= 1:
                continue
            keep_rid = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            for sid in segs:
                rid = owner_now.get(int(sid))
                if rid is None or int(rid) == int(keep_rid):
                    continue
                s.loc[s['_street_key'] == int(sid), region_id_column] = int(keep_rid)
                owner_now[int(sid)] = int(keep_rid)
                changed_local = True
        if changed_local:
            target_rows = p[p[region_id_column].notna()].copy()
            for idx_row, row in target_rows.iterrows():
                members = row.get('_street_members', [])
                if not isinstance(members, list) or not members:
                    continue
                counts: dict[int, int] = {}
                for sid in members:
                    rid = owner_now.get(int(sid))
                    if rid is None:
                        continue
                    counts[int(rid)] = int(counts.get(int(rid), 0) + 1)
                if counts:
                    keep_rid = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
                    p.loc[idx_row, region_id_column] = int(keep_rid)

    def _region_member_keys(rid: int, *, demand_only: bool=False) -> set[int]:
        mask = s[region_id_column] == int(rid)
        if demand_only:
            mask &= s['_is_demand_street'] == True
        return set((int(v) for v in s.loc[mask, '_street_key'].dropna().astype(int).tolist()))

    def _reassign_polygon_rows(source_rid: int, *, move_set: set[int], keep_set: set[int], new_region: int | None) -> None:
        target_rows = p[p[region_id_column] == int(source_rid)].copy()
        for idx_row, row in target_rows.iterrows():
            members_row = row.get('_street_members', [])
            if not isinstance(members_row, list):
                continue
            mset = set((int(x) for x in members_row))
            if len(mset & set(move_set)) > len(mset & set(keep_set)):
                p.loc[idx_row, region_id_column] = pd.NA if new_region is None else int(new_region)

    def _drop_region(rid: int) -> None:
        s.loc[s[region_id_column] == int(rid), region_id_column] = pd.NA
        p.loc[p[region_id_column] == int(rid), region_id_column] = pd.NA

    def _enforce_connected_assignments() -> None:
        while True:
            assigned_now = s.dropna(subset=[region_id_column]).copy()
            if assigned_now.empty:
                return
            assigned_now[region_id_column] = assigned_now[region_id_column].astype(int)
            existing_ids = sorted((int(v) for v in assigned_now[region_id_column].unique().tolist()))
            next_id = max(existing_ids) + 1 if existing_ids else 0
            changed = False
            for rid in existing_ids:
                members = _region_member_keys(int(rid))
                if len(members) <= 1:
                    continue
                comps = _connected_components(adj, members)
                if len(comps) <= 1:
                    continue
                demand_members = _region_member_keys(int(rid), demand_only=True)
                if not demand_members:
                    _drop_region(int(rid))
                    changed = True
                    continue
                comps_sorted = sorted((set((int(v) for v in c)) for c in comps), key=lambda c: len(c), reverse=True)
                demand_comps = [c for c in comps_sorted if c & demand_members]
                if not demand_comps:
                    _drop_region(int(rid))
                    changed = True
                    continue
                main_comp = max(demand_comps, key=lambda c: len(c))
                for comp_set in comps_sorted:
                    if comp_set == main_comp:
                        continue
                    comp_has_demand = bool(comp_set & demand_members)
                    if comp_has_demand:
                        new_id = int(next_id)
                        next_id += 1
                        s.loc[(s[region_id_column] == rid) & s['_street_key'].isin(comp_set), region_id_column] = new_id
                        _reassign_polygon_rows(int(rid), move_set=set(comp_set), keep_set=set(main_comp), new_region=int(new_id))
                    else:
                        s.loc[(s[region_id_column] == rid) & s['_street_key'].isin(comp_set), region_id_column] = pd.NA
                        _reassign_polygon_rows(int(rid), move_set=set(comp_set), keep_set=set(main_comp), new_region=None)
                    changed = True
            if not changed:
                return

    def _search_targets(*, seeds: set[int], source_rid: int, owner: dict[int, int], max_cost: float | None=None) -> tuple[dict[int, int | None], dict[int, float], dict[int, int]]:
        dist: dict[int, float] = {int(sid): 0.0 for sid in seeds}
        prev: dict[int, int | None] = {int(sid): None for sid in seeds}
        pq: list[tuple[float, int]] = [(0.0, int(sid)) for sid in seeds]
        heapq.heapify(pq)
        hit_cost: dict[int, float] = {}
        hit_node: dict[int, int] = {}
        while pq:
            cur_cost, cur = heapq.heappop(pq)
            if cur_cost > dist.get(cur, float('inf')):
                continue
            if max_cost is not None and cur_cost > float(max_cost):
                break
            for nb in adj.get(cur, set()):
                nb_owner = owner.get(int(nb))
                if nb_owner is not None and nb_owner != int(source_rid):
                    if cur_cost < hit_cost.get(int(nb_owner), float('inf')):
                        hit_cost[int(nb_owner)] = float(cur_cost)
                        hit_node[int(nb_owner)] = int(cur)
                    continue
                step = 0.0 if nb_owner == int(source_rid) else float(lengths.get(nb, 0.0))
                cand = cur_cost + step
                if max_cost is not None and cand > float(max_cost):
                    continue
                if cand < dist.get(int(nb), float('inf')):
                    dist[int(nb)] = cand
                    prev[int(nb)] = int(cur)
                    heapq.heappush(pq, (cand, int(nb)))
        return (prev, hit_cost, hit_node)

    _collapse_overloaded_junction_regions()
    _enforce_connected_assignments()
    for _ in range(500):
        assigned = s.dropna(subset=[region_id_column]).copy()
        if assigned.empty:
            break
        assigned[region_id_column] = assigned[region_id_column].astype(int)
        counts = assigned.groupby(region_id_column).size().astype(int)
        streets_by_region = _streets_by_region_from_assigned(assigned)
        demand_by_region = _demand_totals(streets_by_region)
        len_by_region = _length_totals(streets_by_region)
        candidates = sorted([int(rid) for rid in counts.index.tolist()], key=lambda rid: int(counts.get(rid, 0)))
        if not candidates:
            break
        owner = _owner_map_from_assigned(assigned, street_key_col='_street_key', region_id_col=region_id_column)
        changed = False
        for rid in candidates:
            seeds = [int(r['_street_key']) for _, r in assigned[assigned[region_id_column] == rid][['_street_key']].iterrows()]
            if not seeds:
                continue
            prev, hit_cost, hit_node = _search_targets(seeds=set((int(v) for v in seeds)), source_rid=int(rid), owner=owner, max_cost=float(max_connector_m))
            feasible_targets: list[tuple[float, int]] = []
            for target, cost in hit_cost.items():
                path = _trace_prev_chain(prev, hit_node.get(int(target)))
                path_set = set(path)
                added_unassigned_len = float(sum((float(lengths.get(sid, 0.0)) for sid in path_set if owner.get(sid) is None)))
                if max_demand_mwh is not None:
                    dsum = float(demand_by_region.get(rid, 0.0) + demand_by_region.get(int(target), 0.0))
                    if dsum > float(max_demand_mwh):
                        continue
                if max_street_length_km is not None:
                    lsum = float(len_by_region.get(rid, 0.0) + len_by_region.get(int(target), 0.0) + added_unassigned_len)
                    if lsum > float(max_street_length_km) * 1000.0:
                        continue
                hypo_owner = dict(owner)
                for sid in streets_by_region.get(int(rid), set()):
                    hypo_owner[int(sid)] = int(target)
                for sid in path_set:
                    if owner.get(int(sid)) is None:
                        hypo_owner[int(sid)] = int(target)
                conflict_scope = set(streets_by_region.get(int(rid), set()))
                conflict_scope |= set(streets_by_region.get(int(target), set()))
                conflict_scope |= set((int(sid) for sid in path_set))
                if _has_overloaded_multi_region_conflict(hypo_owner, street_scope=conflict_scope):
                    continue
                if not _is_region_connected_under_owner(hypo_owner, int(target)):
                    continue
                feasible_targets.append((float(cost), int(target)))
            if not feasible_targets:
                continue
            target = min(feasible_targets, key=lambda x: (x[0], counts.get(x[1], 0), x[1]))[1]
            connector_path = _trace_prev_chain(prev, hit_node.get(int(target)))
            if connector_path:
                mask_path_unassigned = s['_street_key'].isin(connector_path) & s[region_id_column].isna()
                s.loc[mask_path_unassigned, region_id_column] = int(target)
            s.loc[s[region_id_column] == rid, region_id_column] = int(target)
            p.loc[p[region_id_column] == rid, region_id_column] = int(target)
            _enforce_connected_assignments()
            changed = True
            break
        if not changed:
            break
    if enable_final_resort and max_demand_mwh is not None and (float(max_demand_mwh) > 0.0):
        small_region_demand_threshold_mwh = 0.20 * float(max_demand_mwh)
        assigned_for_dem = s.dropna(subset=[region_id_column]).copy()
        demand_total_by_region: dict[int, float] = {}
        if not assigned_for_dem.empty:
            assigned_for_dem[region_id_column] = assigned_for_dem[region_id_column].astype(int)
            streets_by_region_dem = _streets_by_region_from_assigned(assigned_for_dem)
            demand_total_by_region = _demand_totals(streets_by_region_dem)
        demand_by_sid_local: dict[int, float] = {}
        for _, row in p[['_street_members', 'annual_demand_mwh']].iterrows():
            members = row.get('_street_members', [])
            if not isinstance(members, list) or not members:
                continue
            dsum = _to_float(row.get('annual_demand_mwh', 0.0), 0.0)
            per = dsum / float(len(members)) if len(members) > 0 else 0.0
            for sid in members:
                sid_i = int(sid)
                demand_by_sid_local[sid_i] = demand_by_sid_local.get(sid_i, 0.0) + per
        demand_street_keys_local: set[int] = {int(sid) for sid, val in demand_by_sid_local.items() if float(val) > 0.0}

        def _region_streets(rid: int) -> set[int]:
            return _region_member_keys(int(rid))

        def _region_len(rid: int) -> float:
            return float(sum((float(lengths.get(sid, 0.0)) for sid in _region_streets(rid))))

        def _connected_ok(rid: int) -> bool:
            rset = _region_streets(int(rid))
            return _is_connected_nodes(rset)

        def _caps_ok(rid: int) -> bool:
            d = float(demand_total_by_region.get(rid, 0.0))
            l = float(_region_len(rid))
            if max_demand_mwh is not None and d > float(max_demand_mwh):
                return False
            if max_street_length_km is not None and l > float(max_street_length_km) * 1000.0:
                return False
            return True

        def _ratio_ok_region(rid: int) -> bool:
            if float(demand_total_by_region.get(int(rid), 0.0)) < 500.0:
                return True
            rset = _region_streets(rid)
            if not rset:
                return True
            demand_len = float(sum((float(lengths.get(int(sid), 0.0)) for sid in rset if int(sid) in demand_street_keys_local)))
            nondemand_len = float(sum((float(lengths.get(int(sid), 0.0)) for sid in rset if int(sid) not in demand_street_keys_local)))
            if demand_len <= 0.0:
                return False
            if max_nondemand_to_demand_ratio is not None:
                if nondemand_len / demand_len >= float(max_nondemand_to_demand_ratio):
                    return False
            return True

        def _region_ok_with_flags(rid: int, *, enforce_caps: bool, enforce_ratio: bool) -> bool:
            if enforce_caps and (not _caps_ok(int(rid))):
                return False
            if enforce_ratio and (not _ratio_ok_region(int(rid))):
                return False
            if not _connected_ok(int(rid)):
                return False
            return True

        def _rank_nearest_targets(rid: int, *, max_targets: int | None=None) -> list[tuple[float, int, int, list[int]]]:
            assigned = s.dropna(subset=[region_id_column]).copy()
            if assigned.empty:
                return []
            assigned[region_id_column] = assigned[region_id_column].astype(int)
            counts = assigned.groupby(region_id_column).size().astype(int)
            seeds = sorted(_region_streets(int(rid)))
            if not seeds:
                return []
            owner = _owner_map_from_assigned(assigned, street_key_col='_street_key', region_id_col=region_id_column)
            prev, hit_cost, hit_node = _search_targets(seeds=set((int(v) for v in seeds)), source_rid=int(rid), owner=owner)
            ranked_targets: list[tuple[float, int, int, list[int]]] = []
            for target, cost in hit_cost.items():
                tsize = int(counts.get(int(target), 10 ** 9))
                path = _trace_prev_chain(prev, hit_node.get(int(target)))
                ranked_targets.append((float(cost), int(tsize), int(target), path))
            ranked_targets.sort(key=lambda x: (x[0], x[1], x[2]))
            if max_targets is not None:
                ranked_targets = ranked_targets[:int(max_targets)]
            return ranked_targets

        def _split_overlap_area(comp_a: set[int], comp_b: set[int]) -> float:
            geoms_a = [g for g in base.loc[base['_street_key'].isin(list(comp_a)), 'geometry'].tolist() if g is not None and (not g.is_empty)]
            geoms_b = [g for g in base.loc[base['_street_key'].isin(list(comp_b)), 'geometry'].tolist() if g is not None and (not g.is_empty)]
            if not geoms_a or not geoms_b:
                return 0.0
            try:
                ua = unary_union(geoms_a)
                ub = unary_union(geoms_b)
                if ua is None or ub is None or ua.is_empty or ub.is_empty:
                    return 0.0
                return float(ua.intersection(ub).area)
            except Exception:
                return 0.0

        def _try_merge_and_fix(rid: int, target: int, path_try: list[int], *, enforce_caps: bool, enforce_ratio: bool, allow_split: bool) -> bool:
            s_snapshot = s[region_id_column].copy()
            p_snapshot = p[region_id_column].copy()
            demand_snapshot = dict(demand_total_by_region)

            def _rollback() -> bool:
                s[region_id_column] = s_snapshot
                p[region_id_column] = p_snapshot
                demand_total_by_region.clear()
                demand_total_by_region.update(demand_snapshot)
                return False

            rid_mask_s = s[region_id_column] == int(rid)
            rid_mask_p = p[region_id_column] == int(rid)
            path_mask_s = s['_street_key'].isin(path_try) & s[region_id_column].isna()
            affected_streets = _region_streets(int(rid)) | _region_streets(int(target)) | set((int(v) for v in path_try))
            old_rid_dem = float(demand_total_by_region.get(int(rid), 0.0))
            old_target_dem = float(demand_total_by_region.get(int(target), 0.0))
            s.loc[path_mask_s, region_id_column] = int(target)
            s.loc[rid_mask_s, region_id_column] = int(target)
            p.loc[rid_mask_p, region_id_column] = int(target)
            demand_total_by_region[int(target)] = old_target_dem + old_rid_dem
            demand_total_by_region.pop(int(rid), None)
            needs_split_attempt = (allow_split and (not _region_ok_with_flags(int(target), enforce_caps=enforce_caps, enforce_ratio=enforce_ratio))) or (not _caps_ok(int(target)))
            if needs_split_attempt:
                _split_over_cap_region(int(target))
            affected_regions = set((int(v) for v in s.loc[s['_street_key'].isin(list(affected_streets)) & s[region_id_column].notna(), region_id_column].dropna().astype(int).tolist()))
            if not affected_regions:
                return _rollback()
            is_valid = all((_region_ok_with_flags(int(r), enforce_caps=enforce_caps, enforce_ratio=enforce_ratio) for r in affected_regions))
            owner_now = _owner_map_from_assigned(s.dropna(subset=[region_id_column]), street_key_col='_street_key', region_id_col=region_id_column)
            if is_valid and (not _has_overloaded_multi_region_conflict(owner_now, street_scope=affected_streets)):
                return True
            return _rollback()

        def _split_over_cap_region(rid: int) -> bool:
            rset = _region_streets(rid)
            if len(rset) < split_region_min_segments:
                return False
            radj: dict[int, set[int]] = {sid: set((nb for nb in adj.get(sid, set()) if nb in rset)) for sid in rset}
            idx = 0
            tin: dict[int, int] = {}
            low: dict[int, int] = {}
            parent: dict[int, int | None] = {}
            bridges: list[tuple[int, int]] = []
            children_count: dict[int, int] = {}

            def _dfs(u: int) -> None:
                nonlocal idx
                idx += 1
                tin[u] = low[u] = idx
                for v in radj.get(u, set()):
                    if v == parent.get(u):
                        continue
                    if v in tin:
                        low[u] = min(low[u], tin[v])
                        continue
                    parent[v] = u
                    children_count[u] = children_count.get(u, 0) + 1
                    _dfs(v)
                    low[u] = min(low[u], low[v])
                    if low[v] > tin[u]:
                        bridges.append((u, v))
            for sid in rset:
                if sid in tin:
                    continue
                parent[sid] = None
                _dfs(sid)
            articulation: set[int] = set()
            for u in rset:
                pu = parent.get(u)
                if pu is None:
                    if children_count.get(u, 0) > 1:
                        articulation.add(int(u))
                else:
                    for v in radj.get(u, set()):
                        if parent.get(v) == u and low.get(v, 10 ** 9) >= tin.get(u, 10 ** 9):
                            articulation.add(int(u))
                            break
            total_dem = float(demand_total_by_region.get(rid, 0.0))

            def _ratio_ok(comp: set[int]) -> bool:
                comp_dem_mwh = float(sum((float(demand_by_sid_local.get(int(sid), 0.0)) for sid in comp)))
                if comp_dem_mwh < 500.0:
                    return True
                demand_len = float(sum((float(lengths.get(int(sid), 0.0)) for sid in comp if int(sid) in demand_street_keys_local)))
                nondemand_len = float(sum((float(lengths.get(int(sid), 0.0)) for sid in comp if int(sid) not in demand_street_keys_local)))
                if demand_len <= 0.0:
                    return False
                if max_nondemand_to_demand_ratio is not None:
                    if nondemand_len / demand_len >= float(max_nondemand_to_demand_ratio):
                        return False
                return True

            def _apply_split(comp_a: set[int], comp_b: set[int]) -> bool:
                existing_ids = [int(v) for v in s[region_id_column].dropna().astype(int).tolist()]
                new_id = max(existing_ids) + 1 if existing_ids else rid + 1
                s.loc[(s[region_id_column] == rid) & s['_street_key'].isin(comp_b), region_id_column] = int(new_id)
                dem_a = float(sum((float(demand_by_sid_local.get(int(sid), 0.0)) for sid in comp_a)))
                dem_b = float(sum((float(demand_by_sid_local.get(int(sid), 0.0)) for sid in comp_b)))
                demand_total_by_region[rid] = dem_a
                demand_total_by_region[int(new_id)] = dem_b
                _reassign_polygon_rows(int(rid), move_set=set(comp_b), keep_set=set(comp_a), new_region=int(new_id))
                return True
            crossing_candidates = [u for u in articulation if len(radj.get(u, set())) >= int(_JUNCTION_NEIGHBOR_ONLY_MIN_DEGREE)]
            best: dict[str, tuple[tuple[float, int, float], set[int], set[int]] | None] = {'cross_any': None, 'cross_ratio': None, 'bridge_any': None, 'bridge_ratio': None}

            def _consider_split(*, key_any: str, key_ratio: str, comp_a: set[int], comp_b: set[int]) -> None:
                scored = _score_split_candidate(comp_a, comp_b)
                if scored is None:
                    return
                score, ratio_ok = scored
                if best[key_any] is None or score < best[key_any][0]:
                    best[key_any] = (score, set(comp_a), set(comp_b))
                if ratio_ok and (best[key_ratio] is None or score < best[key_ratio][0]):
                    best[key_ratio] = (score, set(comp_a), set(comp_b))

            def _score_split_candidate(comp_a: set[int], comp_b: set[int]) -> tuple[tuple[float, int, float], bool] | None:
                if not comp_a or not comp_b:
                    return None
                dem_a = float(sum((float(demand_by_sid_local.get(int(sid), 0.0)) for sid in comp_a)))
                dem_b = float(sum((float(demand_by_sid_local.get(int(sid), 0.0)) for sid in comp_b)))
                if dem_a < float(small_region_demand_threshold_mwh) or dem_b < float(small_region_demand_threshold_mwh):
                    return None
                if not set(comp_a) & demand_street_keys_local:
                    return None
                if not set(comp_b) & demand_street_keys_local:
                    return None
                len_a = float(sum((float(lengths.get(sid, 0.0)) for sid in comp_a)))
                len_b = float(sum((float(lengths.get(sid, 0.0)) for sid in comp_b)))
                if max_street_length_km is not None:
                    cap_m = float(max_street_length_km) * 1000.0
                    if len_a > cap_m or len_b > cap_m:
                        return None
                if max_demand_mwh is not None:
                    if dem_a > float(max_demand_mwh) or dem_b > float(max_demand_mwh):
                        return None
                half = total_dem / 2.0
                diff = abs(dem_a - half)
                within10 = 0 if total_dem <= 0 or diff <= 0.1 * total_dem else 1
                score = (float(_split_overlap_area(comp_a, comp_b)), int(within10), float(diff))
                return (score, bool(_ratio_ok(comp_a) and _ratio_ok(comp_b)))

            for c in crossing_candidates:
                seen_global: set[int] = set()
                comps: list[set[int]] = []
                for seed in set(rset) - {int(c)}:
                    if seed in seen_global:
                        continue
                    comp: set[int] = set()
                    stack: list[int] = [int(seed)]
                    while stack:
                        cur = stack.pop()
                        if cur in comp or cur == int(c):
                            continue
                        comp.add(cur)
                        for nb in radj.get(cur, set()):
                            if nb == int(c):
                                continue
                            if nb not in comp:
                                stack.append(nb)
                    if comp:
                        comps.append(comp)
                        seen_global |= comp
                if len(comps) < 2:
                    continue
                for comp_b in comps:
                    comp_a = set(rset) - set(comp_b)
                    _consider_split(key_any='cross_any', key_ratio='cross_ratio', comp_a=set(comp_a), comp_b=set(comp_b))
            for a, b in bridges:
                if a not in demand_street_keys_local or b not in demand_street_keys_local:
                    continue
                seen: set[int] = set()
                stack: list[int] = [a]
                while stack:
                    cur = stack.pop()
                    if cur in seen:
                        continue
                    seen.add(cur)
                    for nb in radj.get(cur, set()):
                        if cur == a and nb == b or (cur == b and nb == a):
                            continue
                        if nb not in seen:
                            stack.append(nb)
                comp_a = seen
                comp_b = set(rset) - set(comp_a)
                _consider_split(key_any='bridge_any', key_ratio='bridge_ratio', comp_a=set(comp_a), comp_b=set(comp_b))
            for key in ['cross_ratio', 'bridge_ratio', 'cross_any', 'bridge_any']:
                cand = best.get(key)
                if cand is not None:
                    _, comp_a, comp_b = cand
                    return _apply_split(comp_a, comp_b)
            return False

        def _candidate_ids() -> list[int]:
            assigned = s[region_id_column].dropna()
            if assigned.empty:
                return []
            return sorted([int(rid) for rid in assigned.astype(int).unique().tolist() if float(demand_total_by_region.get(int(rid), 0.0)) < float(small_region_demand_threshold_mwh)], key=lambda rid: float(demand_total_by_region.get(int(rid), 0.0)))

        def _assigned_region_ids() -> set[int]:
            assigned = s[region_id_column].dropna()
            return set() if assigned.empty else set((int(v) for v in assigned.astype(int).unique().tolist()))
        round_candidates = _candidate_ids()
        unresolved_after_rounds: list[int] = []
        merge_stages = [(True, True, True), (True, False, False), (False, False, False)]

        def _is_active_small_region(rid: int) -> bool:
            return int(rid) in _assigned_region_ids() and float(demand_total_by_region.get(int(rid), 0.0)) < float(small_region_demand_threshold_mwh)

        for _round_idx in range(3):
            if not round_candidates:
                unresolved_after_rounds = []
                break
            queue: deque[int] = deque((int(v) for v in round_candidates))
            queued: set[int] = set((int(v) for v in round_candidates))
            small_islands: list[int] = []
            while queue:
                rid = int(queue.popleft())
                queued.discard(int(rid))
                if not _is_active_small_region(int(rid)):
                    continue
                ranked_targets = _rank_nearest_targets(int(rid))
                if not ranked_targets:
                    small_islands.append(int(rid))
                    continue
                merged_ok = False
                for enforce_caps, enforce_ratio, allow_split in merge_stages:
                    if merged_ok:
                        break
                    for _, _, target, path_try in ranked_targets:
                        if _try_merge_and_fix(int(rid), int(target), path_try, enforce_caps=enforce_caps, enforce_ratio=enforce_ratio, allow_split=allow_split):
                            merged_ok = True
                            if float(demand_total_by_region.get(int(target), 0.0)) < float(small_region_demand_threshold_mwh) and int(target) not in queued:
                                queue.append(int(target))
                                queued.add(int(target))
                            break
                if not merged_ok:
                    small_islands.append(int(rid))
            round_candidates = sorted(set((int(v) for v in small_islands)))
            unresolved_after_rounds = list(round_candidates)
        if unresolved_after_rounds:
            for rid in list(unresolved_after_rounds):
                if not _is_active_small_region(int(rid)):
                    continue
                ranked_targets = _rank_nearest_targets(int(rid), max_targets=1)
                if not ranked_targets:
                    continue
                _, _, target, path_try = ranked_targets[0]
                _try_merge_and_fix(int(rid), int(target), path_try, enforce_caps=False, enforce_ratio=False, allow_split=False)
    _enforce_connected_assignments()
    assigned_final = s.dropna(subset=[region_id_column]).copy()
    if assigned_final.empty:
        return (polygons, streets_with_region)
    old_ids = sorted((int(v) for v in assigned_final[region_id_column].unique()))
    id_map = {old: new for new, old in enumerate(old_ids)}
    s[region_id_column] = s[region_id_column].map(lambda v: id_map.get(int(v)) if pd.notna(v) else v)
    p[region_id_column] = p[region_id_column].map(lambda v: id_map.get(int(v)) if pd.notna(v) else v)
    rows: list[dict[str, Any]] = []
    assigned_ids = sorted((int(v) for v in s[region_id_column].dropna().astype(int).unique().tolist()))
    for rid_i in assigned_ids:
        grp = p[p[region_id_column] == rid_i]
        row: dict[str, Any] = {region_id_column: int(rid_i)}
        members_from_s = sorted((int(v) for v in s.loc[s[region_id_column] == rid_i, '_street_key'].dropna().astype(int).tolist()))
        if not members_from_s:
            continue
        seg_geoms = [g for g in base.loc[base['_street_key'].isin(members_from_s), 'geometry'].tolist() if g is not None and (not g.is_empty)]
        if not seg_geoms:
            continue
        corridor_buffer_m = max(20.0, 2.0 * float(tolerance_m))
        geoms = [g.buffer(corridor_buffer_m) for g in seg_geoms]
        row['geometry'] = unary_union(geoms).convex_hull.buffer(0)
        row['_street_members'] = members_from_s
        row['_demand_street_members'] = sorted((int(v) for v in s.loc[(s[region_id_column] == rid_i) & (s['_is_demand_street'] == True), '_street_key'].dropna().astype(int).tolist()))
        row['street_count'] = float(len(members_from_s))
        row['street_length_m'] = float(sum((float(lengths.get(int(sid), 0.0)) for sid in members_from_s)))
        for col in ['nondemand_street_length_m', 'building_count']:
            if col in grp.columns:
                row[col] = float(pd.to_numeric(grp[col], errors='coerce').fillna(0).sum())
        demand_val = float(sum((float(street_demand_mwh.get(int(sid), 0.0)) for sid in members_from_s)))
        row['annual_demand_mwh'] = float(demand_val)
        rows.append(row)
    p_out = gpd.GeoDataFrame(rows, geometry='geometry', crs=p.crs).sort_values(region_id_column).reset_index(drop=True)
    return (p_out, s)

def build_region_topology(*, buildings: gpd.GeoDataFrame, streets: gpd.GeoDataFrame, demand_data: pd.DataFrame | gpd.GeoDataFrame, city_column: str | None=None, city_value: str | None=None, clip_buffer_m: float=250.0, connect_tolerance_m: float=10.0, max_demand_mwh: float=30000.0, max_street_length_km: float=15.0, demand_share_pct: float=80.0, polynesia: bool=True, small_islands: bool=True, small_islands_max_segments: int=60, segment_streets_by_building_projections: bool=True, segment_projection_buffer_m: float=12.0, region_id_column: str='id', street_id_column: str='street_id', building_id_column: str='building_objectid', demand_building_column: str='building_objectid', demand_value_column: str='annual_demand_mwh', demand_street_indicator_column: str | None='total_heat_demand', demand_street_indicator_min: float=0.0) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[str, int]]:
    """Build region polygons from street/building demand inputs.

    Optionally filters to one city, clips the street network to relevant buildings,
    and delegates partitioning to polygon_builder with demand/length caps. It then
    maps region IDs back to street segments, merges tiny disconnected islands with
    graph constraints and returns polygons.
    """
    b, s = (buildings.copy(), streets.copy())
    if city_column and city_column in b.columns and (city_column in s.columns):
        if city_value is None:
            vals = b[city_column].dropna().astype(str)
            if vals.empty:
                raise ValueError(f"No values in city column '{city_column}'")
            city_value = str(vals.mode().iloc[0])
        b = b[b[city_column].astype(str) == str(city_value)].copy()
        s = s[s[city_column].astype(str) == str(city_value)].copy()
    s = filter_streets_for_buildings(b, s, clip_buffer_m=float(clip_buffer_m))
    polygons = polygon_builder(buildings=b, streets=s, demand_data=demand_data, demand_value_column=demand_value_column, street_id_column=street_id_column, segment_streets_by_building_projections=segment_streets_by_building_projections, segment_projection_buffer_m=segment_projection_buffer_m, demand_street_indicator_column=demand_street_indicator_column, demand_street_indicator_min=float(demand_street_indicator_min), building_street_column=None, building_id_column=building_id_column, demand_building_column=demand_building_column, id_column=region_id_column, caps={'max_demand_mwh': float(max_demand_mwh), 'max_street_length_km': float(max_street_length_km), 'max_nondemand_street_km': None}, demand_share_pct=float(demand_share_pct) if demand_share_pct is not None else None, enforce_demand_gt_nondemand=False, max_inter_region_connector_m=600.0, street_connect_tolerance_m=float(connect_tolerance_m), street_corridor_buffer_m=20.0, enforce_contiguous_polygons=True, keep_internal_columns=True)
    streets_with_region = _street_segments_with_region(streets=s, polygons=polygons, buildings=b, segment_streets_by_building_projections=segment_streets_by_building_projections, segment_projection_buffer_m=segment_projection_buffer_m, region_id_column=region_id_column, street_id_column=street_id_column)
    polygons, streets_with_region = _merge_small_regions_by_street_graph(polygons=polygons, streets_with_region=streets_with_region, region_id_column=region_id_column, small_islands_max_segments=int(small_islands_max_segments), tolerance_m=float(connect_tolerance_m), max_connector_m=600.0, max_demand_mwh=float(max_demand_mwh) if max_demand_mwh is not None else None, max_street_length_km=float(max_street_length_km) if max_street_length_km is not None else None, demand_share_pct=float(demand_share_pct) if demand_share_pct is not None else None, enable_final_resort=not bool(polynesia))
    mantra = {'raw_street_ids_split_across_regions': int((streets_with_region.dropna(subset=[region_id_column]).groupby(street_id_column)[region_id_column].nunique() > 1).sum()), 'unassigned_street_segments': int(streets_with_region[region_id_column].isna().sum()), 'cross_region_crossings': 0, 'junction_overload_points': 0, 'disconnected_regions': int(_count_disconnected_regions(streets=s, polygons=polygons, tolerance_m=connect_tolerance_m, buildings=b, segment_streets_by_building_projections=segment_streets_by_building_projections, segment_projection_buffer_m=segment_projection_buffer_m, street_id_column=street_id_column))}
    return (polygons, streets_with_region, mantra)

