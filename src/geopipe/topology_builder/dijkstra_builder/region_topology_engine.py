"""Internal algorithm engine for region topology graph/geometry operations.

Only owns geometry helpers and post-seed assignment reconciliation logic.
"""

from __future__ import annotations
from collections import deque
from itertools import combinations
import heapq
import math
from typing import Any
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from shapely.ops import substring, unary_union

from geopipe.topology_builder.region_topology_core import RegionTopologyBase

_JUNCTION_NEIGHBOR_ONLY_MIN_DEGREE = 4

__all__ = [
    "RegionTopologyBase",
    "RegionTopologyGeometry",
    "segment_streets_by_building_projections",
    "count_disconnected_regions",
    "collapse_overloaded_junction_conflicts",
    "merge_small_regions_by_street_graph",
]

class RegionTopologyGeometry(RegionTopologyBase):

    @staticmethod
    def iter_intersection_points(geom: Any) -> list[Point]:
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
                out.extend(RegionTopologyGeometry.iter_intersection_points(gg))
            return out
        return []

    @staticmethod
    def line_tangent_angle_at_point(line: Any, pt: Point) -> float | None:
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

    @staticmethod
    def street_intersection_context(streets: gpd.GeoDataFrame, *, street_key_col: str, tolerance_m: float) -> tuple[dict[int, Any], set[tuple[int, int]], dict[tuple[float, float], set[int]], dict[tuple[float, float], Point]]:
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
            for pt in RegionTopologyGeometry.iter_intersection_points(inter):
                key = (round(float(pt.x), 3), round(float(pt.y), 3))
                point_geom_by_key[key] = pt
                junction_segments.setdefault(key, set()).update({int(a), int(b)})
        return (geom_by_sid, candidate_edges, junction_segments, point_geom_by_key)

    @staticmethod
    def build_street_adjacency(streets: gpd.GeoDataFrame, *, street_key_col: str, tolerance_m: float, junction_neighbor_only_min_degree: int=_JUNCTION_NEIGHBOR_ONLY_MIN_DEGREE) -> dict[int, set[int]]:
        if streets.empty:
            return {}
        base = streets[[street_key_col, 'geometry']].copy().dropna(subset=[street_key_col, 'geometry'])
        if base.empty:
            return {}
        base[street_key_col] = base[street_key_col].astype(int)
        adjacency: dict[int, set[int]] = {int(v): set() for v in base[street_key_col].tolist()}
        geom_by_sid, candidate_edges, junction_segments, point_geom_by_key = RegionTopologyGeometry.street_intersection_context(base, street_key_col=street_key_col, tolerance_m=float(tolerance_m))
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
                ang = RegionTopologyGeometry.line_tangent_angle_at_point(geom_by_sid.get(int(sid)), pt)
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

    @staticmethod
    def build_overloaded_junction_segments(streets: gpd.GeoDataFrame, *, street_key_col: str, tolerance_m: float, min_degree: int=_JUNCTION_NEIGHBOR_ONLY_MIN_DEGREE) -> list[set[int]]:
        if streets.empty:
            return []
        base = streets[[street_key_col, 'geometry']].copy().dropna(subset=[street_key_col, 'geometry'])
        if base.empty:
            return []
        base[street_key_col] = base[street_key_col].astype(int)
        _, candidate_edges, junction_segments, _ = RegionTopologyGeometry.street_intersection_context(base, street_key_col=street_key_col, tolerance_m=float(tolerance_m))
        if not candidate_edges:
            return []
        return [set((int(v) for v in segs)) for segs in junction_segments.values() if len(segs) >= int(min_degree)]

def segment_streets_by_building_projections(streets: gpd.GeoDataFrame, buildings: gpd.GeoDataFrame, *, street_id_column: str, projection_buffer_m: float) -> gpd.GeoDataFrame:
    if streets.empty or buildings.empty or projection_buffer_m <= 0:
        return streets
    lines = streets.copy().reset_index(drop=True)
    if street_id_column not in lines.columns:
        raise ValueError(f"Missing street id column '{street_id_column}' in streets")
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


def count_disconnected_regions(*, streets_with_region: gpd.GeoDataFrame, region_id_column: str, tolerance_m: float) -> int:
    if streets_with_region.empty or region_id_column not in streets_with_region.columns:
        return 0

    if '_street_key' not in streets_with_region.columns:
        return 0

    assigned = streets_with_region[['_street_key', 'geometry', region_id_column]].copy()
    assigned = assigned.dropna(subset=['_street_key', 'geometry', region_id_column])
    if assigned.empty:
        return 0

    assigned['_street_key'] = assigned['_street_key'].astype(int)
    assigned[region_id_column] = assigned[region_id_column].astype(int)

    adj = RegionTopologyGeometry.build_street_adjacency(
        assigned[['_street_key', 'geometry']],
        street_key_col='_street_key',
        tolerance_m=float(tolerance_m),
    )

    bad = 0
    for _, grp in assigned.groupby(region_id_column, dropna=False):
        members = set((int(v) for v in grp['_street_key'].tolist()))
        if len(members) <= 1:
            continue
        seen, stack = (set(), [next(iter(members))])
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend((nb for nb in adj.get(cur, set()) if nb in members and nb not in seen))
        bad += int(seen != members)

    return bad

def collapse_overloaded_junction_conflicts(*, streets_with_region: gpd.GeoDataFrame, region_id_column: str, tolerance_m: float) -> gpd.GeoDataFrame:
    if streets_with_region.empty or region_id_column not in streets_with_region.columns:
        return streets_with_region
    if '_street_key' not in streets_with_region.columns:
        return streets_with_region

    s = streets_with_region.copy()
    base = s[['_street_key', 'geometry', region_id_column]].copy()
    base = base.dropna(subset=['_street_key', 'geometry'])
    if base.empty:
        return s
    base['_street_key'] = base['_street_key'].astype(int)

    def _strict_overloaded_junction_groups() -> list[set[int]]:
        probe = base[['_street_key', 'geometry']].copy()
        if float(tolerance_m) > 0.0:
            probe['geometry'] = probe.geometry.buffer(float(tolerance_m))

        joined = gpd.sjoin(
            probe[['_street_key', 'geometry']],
            probe[['_street_key', 'geometry']],
            how='inner',
            predicate='intersects',
        )

        pairs: set[tuple[int, int]] = set()
        for _, row in joined.iterrows():
            a = int(row['_street_key_left'])
            b = int(row['_street_key_right'])
            if a == b:
                continue
            if a > b:
                a, b = (b, a)
            pairs.add((int(a), int(b)))

        geom_by_sid = {int(r['_street_key']): r.geometry for _, r in base[['_street_key', 'geometry']].iterrows()}
        junction_segments: dict[tuple[float, float], set[int]] = {}

        for a, b in pairs:
            ga = geom_by_sid.get(int(a))
            gb = geom_by_sid.get(int(b))
            if ga is None or gb is None or ga.is_empty or gb.is_empty:
                continue
            inter = ga.intersection(gb)
            for pt in RegionTopologyGeometry.iter_intersection_points(inter):
                key = (round(float(pt.x), 3), round(float(pt.y), 3))
                junction_segments.setdefault(key, set()).update({int(a), int(b)})

        return [
            set((int(v) for v in segs))
            for segs in junction_segments.values()
            if len(segs) >= int(_JUNCTION_NEIGHBOR_ONLY_MIN_DEGREE)
        ]

    overloaded_junctions = _strict_overloaded_junction_groups()
    if not overloaded_junctions:
        return s

    for _ in range(8):
        assigned = s.dropna(subset=[region_id_column]).copy()
        if assigned.empty:
            break
        assigned[region_id_column] = assigned[region_id_column].astype(int)
        owner = RegionTopologyBase.owner_map_from_assigned(
            assigned,
            street_key_col='_street_key',
            region_id_col=region_id_column,
        )
        changed = False
        for segs in overloaded_junctions:
            counts: dict[int, int] = {}
            for sid in segs:
                rid = owner.get(int(sid))
                if rid is None:
                    continue
                counts[int(rid)] = int(counts.get(int(rid), 0) + 1)
            if len(counts) <= 1:
                continue
            keep_rid = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            for sid in segs:
                rid = owner.get(int(sid))
                if rid is None or int(rid) == int(keep_rid):
                    continue
                s.loc[s['_street_key'] == int(sid), region_id_column] = int(keep_rid)
                owner[int(sid)] = int(keep_rid)
                changed = True
        if not changed:
            break

    return s

def merge_small_regions_by_street_graph(*, streets_with_region: gpd.GeoDataFrame, street_demand_mwh: dict[int, float], region_id_column: str, small_islands_max_segments: int, tolerance_m: float, max_connector_m: float, max_demand_mwh: float | None, max_street_length_km: float | None, demand_share_pct: float | None, enable_final_resort: bool) -> gpd.GeoDataFrame:
    if region_id_column not in streets_with_region.columns:
        return streets_with_region
    s = streets_with_region.copy()
    max_nondemand_to_demand_ratio = RegionTopologyBase.max_nondemand_to_demand_ratio_from_share(demand_share_pct)
    split_region_min_segments = max(2, int(small_islands_max_segments))
    street_demand_mwh = {int(k): float(v) for k, v in dict(street_demand_mwh or {}).items()}
    base = s[['_street_key', 'geometry', region_id_column]].copy()
    base['_street_key'] = base['_street_key'].astype(int)
    lengths = {int(r['_street_key']): float(r.geometry.length if r.geometry is not None else 0.0) for _, r in base.iterrows()}
    adj = RegionTopologyGeometry.build_street_adjacency(base[['_street_key', 'geometry']], street_key_col='_street_key', tolerance_m=float(tolerance_m))
    overloaded_junctions = RegionTopologyGeometry.build_overloaded_junction_segments(base[['_street_key', 'geometry']], street_key_col='_street_key', tolerance_m=float(tolerance_m), min_degree=int(_JUNCTION_NEIGHBOR_ONLY_MIN_DEGREE))
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

    def _is_region_connected_under_owner(owner_by_sid: dict[int, int], rid: int) -> bool:
        nodes = set((int(sid) for sid, owner_rid in owner_by_sid.items() if int(owner_rid) == int(rid)))
        return _is_connected_nodes(nodes)

    def _collapse_overloaded_junction_regions() -> None:
        if not overloaded_junctions:
            return
        assigned_now = s.dropna(subset=[region_id_column]).copy()
        if assigned_now.empty:
            return
        owner_now = RegionTopologyBase.owner_map_from_assigned(assigned_now, street_key_col='_street_key', region_id_col=region_id_column)
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

    def _region_member_keys(rid: int, *, demand_only: bool=False) -> set[int]:
        mask = s[region_id_column] == int(rid)
        if demand_only:
            mask &= s['_is_demand_street'] == True
        return set((int(v) for v in s.loc[mask, '_street_key'].dropna().astype(int).tolist()))

    def _drop_region(rid: int) -> None:
        s.loc[s[region_id_column] == int(rid), region_id_column] = pd.NA

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
                comps = RegionTopologyBase.connected_components(adj, members)
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
                    else:
                        s.loc[(s[region_id_column] == rid) & s['_street_key'].isin(comp_set), region_id_column] = pd.NA
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
        streets_by_region = RegionTopologyBase.streets_by_region(
            assigned,
            street_key_col='_street_key',
            region_id_col=region_id_column,
        )
        demand_by_region = RegionTopologyBase.aggregate_region_values(streets_by_region, street_demand_mwh)
        len_by_region = RegionTopologyBase.aggregate_region_values(streets_by_region, lengths)
        candidates = sorted([int(rid) for rid in counts.index.tolist()], key=lambda rid: int(counts.get(rid, 0)))
        if not candidates:
            break
        owner = RegionTopologyBase.owner_map_from_assigned(assigned, street_key_col='_street_key', region_id_col=region_id_column)
        changed = False
        for rid in candidates:
            seeds = [int(r['_street_key']) for _, r in assigned[assigned[region_id_column] == rid][['_street_key']].iterrows()]
            if not seeds:
                continue
            prev, hit_cost, hit_node = _search_targets(seeds=set((int(v) for v in seeds)), source_rid=int(rid), owner=owner, max_cost=float(max_connector_m))
            feasible_targets: list[tuple[float, int]] = []
            for target, cost in hit_cost.items():
                path = RegionTopologyBase.trace_prev_chain(prev, hit_node.get(int(target)))
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
            connector_path = RegionTopologyBase.trace_prev_chain(prev, hit_node.get(int(target)))
            if connector_path:
                mask_path_unassigned = s['_street_key'].isin(connector_path) & s[region_id_column].isna()
                s.loc[mask_path_unassigned, region_id_column] = int(target)
            s.loc[s[region_id_column] == rid, region_id_column] = int(target)
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
            streets_by_region_dem = RegionTopologyBase.streets_by_region(
                assigned_for_dem,
                street_key_col='_street_key',
                region_id_col=region_id_column,
            )
            demand_total_by_region = RegionTopologyBase.aggregate_region_values(streets_by_region_dem, street_demand_mwh)
        demand_by_sid_local: dict[int, float] = dict(street_demand_mwh)
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
            owner = RegionTopologyBase.owner_map_from_assigned(assigned, street_key_col='_street_key', region_id_col=region_id_column)
            prev, hit_cost, hit_node = _search_targets(seeds=set((int(v) for v in seeds)), source_rid=int(rid), owner=owner)
            ranked_targets: list[tuple[float, int, int, list[int]]] = []
            for target, cost in hit_cost.items():
                tsize = int(counts.get(int(target), 10 ** 9))
                path = RegionTopologyBase.trace_prev_chain(prev, hit_node.get(int(target)))
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
            demand_snapshot = dict(demand_total_by_region)

            def _rollback() -> bool:
                s[region_id_column] = s_snapshot
                demand_total_by_region.clear()
                demand_total_by_region.update(demand_snapshot)
                return False
            rid_mask_s = s[region_id_column] == int(rid)
            path_mask_s = s['_street_key'].isin(path_try) & s[region_id_column].isna()
            affected_streets = _region_streets(int(rid)) | _region_streets(int(target)) | set((int(v) for v in path_try))
            old_rid_dem = float(demand_total_by_region.get(int(rid), 0.0))
            old_target_dem = float(demand_total_by_region.get(int(target), 0.0))
            s.loc[path_mask_s, region_id_column] = int(target)
            s.loc[rid_mask_s, region_id_column] = int(target)
            demand_total_by_region[int(target)] = old_target_dem + old_rid_dem
            demand_total_by_region.pop(int(rid), None)
            needs_split_attempt = (allow_split and (not _region_ok_with_flags(int(target), enforce_caps=enforce_caps, enforce_ratio=enforce_ratio))) or (not _caps_ok(int(target)))
            if needs_split_attempt:
                _split_over_cap_region(int(target))
            affected_regions = set((int(v) for v in s.loc[s['_street_key'].isin(list(affected_streets)) & s[region_id_column].notna(), region_id_column].dropna().astype(int).tolist()))
            if not affected_regions:
                return _rollback()
            is_valid = all((_region_ok_with_flags(int(r), enforce_caps=enforce_caps, enforce_ratio=enforce_ratio) for r in affected_regions))
            owner_now = RegionTopologyBase.owner_map_from_assigned(s.dropna(subset=[region_id_column]), street_key_col='_street_key', region_id_col=region_id_column)
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
        return streets_with_region
    old_ids = sorted((int(v) for v in assigned_final[region_id_column].unique()))
    id_map = {old: new for new, old in enumerate(old_ids)}
    s[region_id_column] = s[region_id_column].map(lambda v: id_map.get(int(v)) if pd.notna(v) else v)
    return s
