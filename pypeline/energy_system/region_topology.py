from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from collections import deque
import heapq
from itertools import combinations
import math
import geopandas as gpd
import pandas as pd
from shapely.ops import substring, unary_union, polygonize, snap
from shapely.geometry import Point, Polygon, LineString
_JUNCTION_NEIGHBOR_ONLY_MIN_DEGREE = 4

@dataclass(frozen=True)
class RegionCaps:
    max_demand_mwh: float | None
    max_street_length_km: float | None
    max_nondemand_street_km: float | None = None

    def as_polygon_builder_caps(self) -> dict[str, float | None]:
        return {
            'max_demand_mwh': None if self.max_demand_mwh is None else float(self.max_demand_mwh),
            'max_street_length_km': None if self.max_street_length_km is None else float(self.max_street_length_km),
            'max_nondemand_street_km': None if self.max_nondemand_street_km is None else float(self.max_nondemand_street_km),
        }

@dataclass(frozen=True)
class RegionTopologyConfig:
    max_demand_mwh: float
    max_street_length_km: float
    demand_share_pct: float
    city_column: str | None
    city_value: str | None
    clip_buffer_m: float
    connect_tolerance_m: float
    polynesia: bool
    small_islands: bool
    small_islands_max_segments: int
    segment_streets_by_building_projections: bool
    segment_projection_buffer_m: float
    region_id_column: str
    street_id_column: str
    building_id_column: str
    demand_building_column: str
    demand_value_column: str
    demand_street_indicator_column: str | None
    demand_street_indicator_min: float

    @classmethod
    def from_required_caps(
        cls,
        *,
        max_demand_mwh: float,
        max_street_length_km: float,
        demand_share_pct: float,
        city_column: str | None=None,
        city_value: str | None=None,
        clip_buffer_m: float | None=None,
        connect_tolerance_m: float | None=None,
        polynesia: bool | None=None,
        small_islands: bool | None=None,
        small_islands_max_segments: int | None=None,
        segment_streets_by_building_projections: bool | None=None,
        segment_projection_buffer_m: float | None=None,
        region_id_column: str | None=None,
        street_id_column: str | None=None,
        building_id_column: str | None=None,
        demand_building_column: str | None=None,
        demand_value_column: str | None=None,
        demand_street_indicator_column: str | None=None,
        demand_street_indicator_min: float | None=None,
    ) -> 'RegionTopologyConfig':
        defaults = REGION_TOPOLOGY_DEFAULTS
        return cls(
            max_demand_mwh=float(max_demand_mwh),
            max_street_length_km=float(max_street_length_km),
            demand_share_pct=float(demand_share_pct),
            city_column=defaults.city_column if city_column is None else city_column,
            city_value=defaults.city_value if city_value is None else city_value,
            clip_buffer_m=defaults.clip_buffer_m if clip_buffer_m is None else float(clip_buffer_m),
            connect_tolerance_m=defaults.connect_tolerance_m if connect_tolerance_m is None else float(connect_tolerance_m),
            polynesia=defaults.polynesia if polynesia is None else bool(polynesia),
            small_islands=defaults.small_islands if small_islands is None else bool(small_islands),
            small_islands_max_segments=defaults.small_islands_max_segments if small_islands_max_segments is None else int(small_islands_max_segments),
            segment_streets_by_building_projections=defaults.segment_streets_by_building_projections if segment_streets_by_building_projections is None else bool(segment_streets_by_building_projections),
            segment_projection_buffer_m=defaults.segment_projection_buffer_m if segment_projection_buffer_m is None else float(segment_projection_buffer_m),
            region_id_column=defaults.region_id_column if region_id_column is None else region_id_column,
            street_id_column=defaults.street_id_column if street_id_column is None else street_id_column,
            building_id_column=defaults.building_id_column if building_id_column is None else building_id_column,
            demand_building_column=defaults.demand_building_column if demand_building_column is None else demand_building_column,
            demand_value_column=defaults.demand_value_column if demand_value_column is None else demand_value_column,
            demand_street_indicator_column=defaults.demand_street_indicator_column if demand_street_indicator_column is None else demand_street_indicator_column,
            demand_street_indicator_min=defaults.demand_street_indicator_min if demand_street_indicator_min is None else float(demand_street_indicator_min),
        )

    def build_caps(self) -> RegionCaps:
        return RegionCaps(
            max_demand_mwh=self.max_demand_mwh,
            max_street_length_km=self.max_street_length_km,
            max_nondemand_street_km=None,
        )

    def polygon_builder_kwargs(
        self,
        *,
        caps: RegionCaps,
        segment_streets_by_building_projections: bool | None=None,
        keep_internal_columns: bool=True,
    ) -> dict[str, Any]:
        segment_street_flags = self.segment_streets_by_building_projections if segment_streets_by_building_projections is None else bool(segment_streets_by_building_projections)
        return {
            'demand_value_column': self.demand_value_column,
            'street_id_column': self.street_id_column,
            'segment_streets_by_building_projections': bool(segment_street_flags),
            'segment_projection_buffer_m': self.segment_projection_buffer_m,
            'demand_street_indicator_column': self.demand_street_indicator_column,
            'demand_street_indicator_min': float(self.demand_street_indicator_min),
            'building_street_column': None,
            'building_id_column': self.building_id_column,
            'demand_building_column': self.demand_building_column,
            'id_column': self.region_id_column,
            'caps': caps.as_polygon_builder_caps(),
            'demand_share_pct': float(self.demand_share_pct),
            'max_inter_region_connector_m': 600.0,
            'street_connect_tolerance_m': float(self.connect_tolerance_m),
            'street_corridor_buffer_m': 20.0,
            'enforce_contiguous_polygons': True,
            'keep_internal_columns': bool(keep_internal_columns),
        }

    def small_island_merge_kwargs(self, *, enable_final_resort: bool) -> dict[str, Any]:
        return {
            'region_id_column': self.region_id_column,
            'small_islands_max_segments': int(self.small_islands_max_segments),
            'tolerance_m': float(self.connect_tolerance_m),
            'max_connector_m': 600.0,
            'max_demand_mwh': float(self.max_demand_mwh) if self.max_demand_mwh is not None else None,
            'max_street_length_km': float(self.max_street_length_km) if self.max_street_length_km is not None else None,
            'demand_share_pct': float(self.demand_share_pct) if self.demand_share_pct is not None else None,
            'enable_final_resort': bool(enable_final_resort),
        }

@dataclass(frozen=True)
class RegionTopologyDefaults:
    city_column: str | None = None
    city_value: str | None = None
    clip_buffer_m: float = 250.0
    connect_tolerance_m: float = 10.0
    polynesia: bool = True
    small_islands: bool = True
    small_islands_max_segments: int = 60
    segment_streets_by_building_projections: bool = True
    segment_projection_buffer_m: float = 12.0
    region_id_column: str = 'id'
    street_id_column: str = 'street_id'
    building_id_column: str = 'building_objectid'
    demand_building_column: str = 'building_objectid'
    demand_value_column: str = 'annual_demand_mwh'
    demand_street_indicator_column: str | None = 'total_heat_demand'
    demand_street_indicator_min: float = 0.0

REGION_TOPOLOGY_DEFAULTS = RegionTopologyDefaults()

class RegionTopologyBase:

    @staticmethod
    def to_float(value: Any, default: float=0.0) -> float:
        num = pd.to_numeric(pd.Series([value]), errors='coerce').fillna(float(default)).iloc[0]
        return float(num)

    @staticmethod
    def owner_map_from_assigned(assigned: pd.DataFrame, *, street_key_col: str, region_id_col: str) -> dict[int, int]:
        return {int(r[street_key_col]): int(r[region_id_col]) for _, r in assigned[[street_key_col, region_id_col]].iterrows()}

    @staticmethod
    def trace_prev_chain(prev: dict[int, int | None], start: int | None) -> list[int]:
        path: list[int] = []
        cur = None if start is None else int(start)
        while cur is not None:
            path.append(int(cur))
            cur = prev.get(int(cur))
        return path

    @staticmethod
    def connected_components(adjacency: dict[int, set[int]], nodes: set[int] | None=None) -> list[set[int]]:
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

    @staticmethod
    def max_nondemand_to_demand_ratio_from_share(demand_share_pct: float | None) -> float | None:
        if demand_share_pct is None:
            return None
        share = float(demand_share_pct)
        if share <= 0.0 or share >= 100.0:
            raise ValueError('demand_share_pct must be between 0 and 100')
        return (100.0 - share) / share

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

@dataclass(frozen=True)
class _PolygonBuilderGeometryFactory:
    street_buffer_m: float

    def iter_polys(self, geom: Any) -> list[Any]:
        if geom is None or geom.is_empty:
            return []
        if geom.geom_type == 'Polygon':
            return [geom]
        if geom.geom_type == 'MultiPolygon':
            return [g for g in geom.geoms if g is not None and (not g.is_empty)]
        return []

    def line_endpoints(self, geom: Any) -> list[Point]:
        if geom is None or geom.is_empty:
            return []
        if geom.geom_type == 'LineString':
            coords = list(geom.coords)
            if len(coords) < 2:
                return []
            return [Point(coords[0]), Point(coords[-1])]
        if geom.geom_type == 'MultiLineString':
            pts: list[Point] = []
            for part in geom.geoms:
                pts.extend(self.line_endpoints(part))
            return pts
        return []

    def interior_holes_as_polys(self, geom: Any) -> list[Any]:
        holes: list[Any] = []
        for poly in self.iter_polys(geom):
            try:
                for ring in getattr(poly, 'interiors', []):
                    hp = Polygon(ring)
                    if hp is not None and (not hp.is_empty) and float(hp.area) > 0.0:
                        holes.append(hp)
            except Exception:
                continue
        return holes

    def merge_touching_or_overlapping_faces(self, polys: list[Any], *, merge_within_m: float=0.0) -> list[Any]:
        clean = [p.buffer(0) for p in polys if p is not None and (not p.is_empty)]
        clean = [p for p in clean if p is not None and (not p.is_empty)]
        n = len(clean)
        if n <= 1:
            return clean
        parent = list(range(n))

        def _find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def _union(i: int, j: int) -> None:
            ri = _find(i)
            rj = _find(j)
            if ri != rj:
                parent[rj] = ri
        for i in range(n):
            pi = clean[i]
            for j in range(i + 1, n):
                pj = clean[j]
                try:
                    if pi.overlaps(pj) or pi.touches(pj) or pi.intersects(pj) or (float(merge_within_m) > 0.0 and float(pi.distance(pj)) <= float(merge_within_m)):
                        _union(i, j)
                except Exception:
                    continue
        groups: dict[int, list[Any]] = {}
        for idx, poly in enumerate(clean):
            root = _find(idx)
            groups.setdefault(root, []).append(poly)
        merged: list[Any] = []
        for grp in groups.values():
            try:
                if float(merge_within_m) > 0.0:
                    grow = float(merge_within_m) / 2.0
                    ug = unary_union([g.buffer(grow) for g in grp]).buffer(0).buffer(-grow).buffer(0)
                else:
                    ug = unary_union(grp).buffer(0)
            except Exception:
                ug = None
            if ug is None or ug.is_empty:
                merged.extend(grp)
            else:
                merged.extend(self.iter_polys(ug) or grp)
        return [p for p in merged if p is not None and (not p.is_empty)]

    def enclosed_area_from_lines_exact(self, lines: list[Any]) -> Any:
        clean_lines = [g for g in lines if g is not None and (not g.is_empty)]
        if not clean_lines:
            return None
        try:
            merged_lines = unary_union(clean_lines)
        except Exception:
            merged_lines = clean_lines
        try:
            snap_tol = 1.0
            snapped_lines = [snap(g, merged_lines, snap_tol) for g in clean_lines]
            merged_lines = unary_union(snapped_lines)
        except Exception:
            pass
        try:
            faces = [p for p in polygonize(merged_lines) if p is not None and (not p.is_empty)]
        except Exception:
            faces = []
        if not faces:
            return None
        min_face_area = max(0.01, float(self.street_buffer_m) * float(self.street_buffer_m) * 0.01)
        faces = [p.buffer(0) for p in faces if p is not None and (not p.is_empty) and float(p.area) >= min_face_area]
        faces = [p for p in faces if p is not None and (not p.is_empty)]
        if not faces:
            return None
        faces = self.merge_touching_or_overlapping_faces(faces)
        if not faces:
            return None
        try:
            return unary_union(faces).buffer(0)
        except Exception:
            return None

    def scope_geom_from_lines(self, lines_now: list[Any]) -> Any:
        enclosed_now = self.enclosed_area_from_lines_exact(lines_now)
        if enclosed_now is None or enclosed_now.is_empty:
            return None
        shell_parts: list[Any] = []
        for poly in self.iter_polys(enclosed_now):
            try:
                shell_parts.append(Polygon(poly.exterior))
            except Exception:
                continue
        if not shell_parts:
            return None
        try:
            return unary_union(shell_parts).buffer(0)
        except Exception:
            return None

    def new_enclosed_area_gain(self, lines_before: list[Any], seg: Any, *, before_geom: Any | None=None) -> float:
        if seg is None or seg.is_empty:
            return 0.0
        if before_geom is None:
            before_geom = self.enclosed_area_from_lines_exact(lines_before)
        after_geom = self.enclosed_area_from_lines_exact(lines_before + [seg])
        if after_geom is None or after_geom.is_empty:
            return 0.0
        if before_geom is None or before_geom.is_empty:
            try:
                return float(after_geom.area)
            except Exception:
                return 0.0
        try:
            newly = after_geom.difference(before_geom).buffer(0)
            if newly is None or newly.is_empty:
                return 0.0
            return max(0.0, float(newly.area))
        except Exception:
            try:
                return max(0.0, float(after_geom.area) - float(before_geom.area))
            except Exception:
                return 0.0

    @staticmethod
    def is_enclosed_point(pt: Point, scope_geom: Any) -> bool:
        if pt is None or pt.is_empty or scope_geom is None or scope_geom.is_empty:
            return False
        try:
            if bool(scope_geom.contains(pt)):
                return True
            if bool(scope_geom.covers(pt)):
                bnd = scope_geom.boundary
                if bnd is None or bnd.is_empty:
                    return True
                return float(pt.distance(bnd)) > 1.0
            return False
        except Exception:
            return False

def polygon_builder(buildings: gpd.GeoDataFrame, streets: gpd.GeoDataFrame, *, demand_data: pd.DataFrame | gpd.GeoDataFrame | None=None, demand_value_column: str='annual_demand_mwh', street_id_column: str='street_id', building_street_column: str | None=None, building_id_column: str | None=None, demand_building_column: str | None=None, id_column: str='id', building_buffer_m: float=2.0, street_buffer_m: float=8.0, segment_streets_by_building_projections: bool=False, segment_projection_buffer_m: float | None=None, demand_street_indicator_column: str | None=None, demand_street_indicator_min: float=0.0, street_corridor_buffer_m: float=15.0, caps: dict[str, float | None] | None=None, demand_share_pct: float | None=None, max_inter_region_connector_m: float | None=100.0, street_connect_tolerance_m: float=3.0, enforce_contiguous_polygons: bool=False, keep_internal_columns: bool=False) -> gpd.GeoDataFrame:
    if buildings.empty:
        raise ValueError('polygon_builder requires non-empty buildings')
    if streets.empty:
        raise ValueError('polygon_builder requires non-empty streets')
    if street_id_column not in streets.columns:
        raise ValueError(f"Missing '{street_id_column}' in streets")
    indicator_col = demand_street_indicator_column if (demand_street_indicator_column and demand_street_indicator_column in streets.columns) else None
    b = buildings.copy()
    s = streets.copy()
    if b.crs is None or s.crs is None:
        raise ValueError('buildings and streets must have CRS')
    if b.crs != s.crs:
        s = s.to_crs(b.crs)
    s_cols = [street_id_column, 'geometry']
    if id_column in s.columns:
        s_cols.append(id_column)
    if indicator_col:
        s_cols.append(indicator_col)
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
        if demand_building_column and building_id_column and (demand_building_column in d.columns) and (building_id_column in b.columns):
            demand_by_building = d.groupby(demand_building_column, dropna=False)[demand_value_column].sum(min_count=1).fillna(0.0)
            demand_series = b[building_id_column].map(demand_by_building)
        if demand_series is None:
            raise ValueError('demand_data mapping failed')
        b[polygon_demand_column] = demand_series.fillna(0.0)
    max_demand = None if cap_cfg['max_demand_mwh'] is None else float(cap_cfg['max_demand_mwh'])
    max_street_length_m = None if cap_cfg['max_street_length_km'] is None else float(cap_cfg['max_street_length_km']) * 1000.0
    max_nondemand_street_m = None if cap_cfg['max_nondemand_street_km'] is None else float(cap_cfg['max_nondemand_street_km']) * 1000.0
    max_inter_region_connector_len_m = None if max_inter_region_connector_m is None else float(max_inter_region_connector_m)
    max_nondemand_ratio = RegionTopologyBase.max_nondemand_to_demand_ratio_from_share(demand_share_pct)
    min_demand_indicator = float(demand_street_indicator_min)
    street_lengths_m = s_work.set_index(street_key_col).geometry.length.fillna(0.0)
    demand_indicator_by_street = None
    if indicator_col and indicator_col in s_work.columns:
        demand_indicator_by_street = pd.to_numeric(s_work[indicator_col], errors='coerce').fillna(0.0)
        demand_indicator_by_street.index = s_work[street_key_col].astype(int)
    geometry_factory = _PolygonBuilderGeometryFactory(street_buffer_m=float(street_buffer_m))
    preassigned_district_mode = bool(id_column in s_work.columns and s_work[id_column].notna().any())
    if preassigned_district_mode:
        assigned_streets = s_work.dropna(subset=[id_column]).copy()
        assigned_streets[id_column] = assigned_streets[id_column].astype(int)
        assigned_touch = assigned_streets[[street_key_col, id_column, 'geometry']].copy().dropna(subset=['geometry'])
        assigned_touch[street_key_col] = assigned_touch[street_key_col].astype(int)
        assigned_touch[id_column] = assigned_touch[id_column].astype(int)
        assigned_sindex = assigned_touch.sindex
        dead_end_touch_tol = max(0.25, float(street_connect_tolerance_m))
        street_to_region = {int(r[street_key_col]): int(r[id_column]) for _, r in assigned_streets[[street_key_col, id_column]].iterrows()}
        b_region = b[assignment_col].map(lambda sid: street_to_region.get(int(sid)) if pd.notna(sid) else None)
        cluster_rows: list[dict[str, Any]] = []
        line_keep_buffer = max(0.25, float(street_connect_tolerance_m) * 0.15)
        street_base_buffer = max(line_keep_buffer, float(street_buffer_m), float(street_corridor_buffer_m))
        district_lines: dict[int, list[Any]] = {}
        district_sids: dict[int, list[int]] = {}
        district_base_poly: dict[int, Any] = {}
        district_center: dict[int, Point] = {}
        for rid, grp in assigned_streets.groupby(id_column, dropna=False):
            rid_i = int(rid)
            reg_street_keys = sorted((int(v) for v in grp[street_key_col].astype(int).tolist()))
            reg_lines = [g for g in grp.geometry.tolist() if g is not None and (not g.is_empty)]
            if not reg_lines:
                continue
            district_lines[rid_i] = reg_lines
            district_sids[rid_i] = reg_street_keys
            base_parts = [g.buffer(street_base_buffer, cap_style=2, join_style=2) for g in reg_lines]
            base_poly = unary_union(base_parts).buffer(0) if base_parts else None
            district_base_poly[rid_i] = base_poly
            try:
                lu = unary_union(reg_lines)
                district_center[rid_i] = lu.centroid if lu is not None and (not lu.is_empty) else Point(0.0, 0.0)
            except Exception:
                district_center[rid_i] = Point(0.0, 0.0)
        district_ids = sorted((int(v) for v in district_sids.keys()))
        neighbor_map: dict[int, set[int]] = {int(r): set() for r in district_ids}
        district_union_lines: dict[int, Any] = {}
        for rid_i in district_ids:
            try:
                district_union_lines[rid_i] = unary_union(district_lines.get(rid_i, []))
            except Exception:
                district_union_lines[rid_i] = None
        for i in range(len(district_ids)):
            a = int(district_ids[i])
            ga = district_union_lines.get(a)
            if ga is None or ga.is_empty:
                continue
            for j in range(i + 1, len(district_ids)):
                b_rid = int(district_ids[j])
                gb = district_union_lines.get(b_rid)
                if gb is None or gb.is_empty:
                    continue
                try:
                    if bool(ga.intersects(gb)) or float(ga.distance(gb)) <= float(dead_end_touch_tol):
                        neighbor_map[a].add(b_rid)
                        neighbor_map[b_rid].add(a)
                except Exception:
                    continue
        order: list[int] = []
        if district_ids:
            start = max(district_ids, key=lambda r: (len(neighbor_map.get(int(r), set())), -int(r)))
            q: deque[int] = deque([int(start)])
            seen: set[int] = set()
            while q:
                cur = int(q.popleft())
                if cur in seen:
                    continue
                seen.add(cur)
                order.append(cur)
                nbs = sorted((int(v) for v in neighbor_map.get(cur, set()) if int(v) not in seen), key=lambda r: (-len(neighbor_map.get(r, set())), int(r)))
                for nb in nbs:
                    q.append(nb)
            for rid_i in district_ids:
                if rid_i not in seen:
                    order.append(rid_i)

        def _endpoint_touches_other_assigned(endpoint: Point, sid: int) -> bool:
            if endpoint is None or endpoint.is_empty:
                return False
            try:
                probe = endpoint.buffer(float(dead_end_touch_tol))
                cand_idx = list(assigned_sindex.intersection(probe.bounds))
            except Exception:
                cand_idx = []
            for idx in cand_idx:
                try:
                    row = assigned_touch.iloc[int(idx)]
                except Exception:
                    continue
                other_sid = int(row[street_key_col])
                if int(other_sid) == int(sid):
                    continue
                og = row.geometry
                if og is None or og.is_empty:
                    continue
                try:
                    if float(endpoint.distance(og)) <= float(dead_end_touch_tol):
                        return True
                except Exception:
                    continue
            return False

        def _district_red_green(rid_i: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            red: list[dict[str, Any]] = []
            green: list[dict[str, Any]] = []
            rows = assigned_touch[assigned_touch[id_column] == int(rid_i)]
            center = district_center.get(int(rid_i), Point(0.0, 0.0))
            for _, row in rows.iterrows():
                sid = int(row[street_key_col])
                eps = geometry_factory.line_endpoints(row.geometry)
                for eidx, ep in enumerate(eps):
                    rec = {
                        'sid': int(sid),
                        'eidx': int(eidx),
                        'pt': ep,
                        'dist_center': float(ep.distance(center)) if center is not None and (not center.is_empty) else 0.0,
                        'ang': float(math.atan2(float(ep.y) - float(center.y), float(ep.x) - float(center.x))) if center is not None and (not center.is_empty) else 0.0,
                    }
                    if _endpoint_touches_other_assigned(ep, int(sid)):
                        green.append(rec)
                    else:
                        red.append(rec)
            return (red, green)
        connector_lines_by_rid: dict[int, list[Any]] = {int(r): [] for r in order}
        connector_lines_global: list[Any] = []
        final_pass_count_by_rid: dict[int, int] = {int(r): 0 for r in order}
        total_districts = int(len(order))
        print(f"[polygon_builder] preassigned mode: processing {total_districts} districts", flush=True)
        for rid_pos, rid_i in enumerate(order, start=1):
            red, green = _district_red_green(int(rid_i))
            print(f"[polygon_builder] district {rid_pos}/{total_districts} (id={int(rid_i)}): red={len(red)} green={len(green)}", flush=True)
            if not red:
                print(f"[polygon_builder] district {rid_pos}/{total_districts} (id={int(rid_i)}): skipped (no red endpoints)", flush=True)
                continue
            foreign_lines = [g for rr, lines in district_lines.items() if int(rr) != int(rid_i) for g in lines]
            foreign_union = unary_union(foreign_lines) if foreign_lines else None
            for rec in red:
                if foreign_union is not None and (not foreign_union.is_empty):
                    try:
                        rec['dist_foreign'] = float(rec['pt'].distance(foreign_union))
                    except Exception:
                        rec['dist_foreign'] = float('inf')
                else:
                    rec['dist_foreign'] = float('inf')
            clockwise = sorted(red, key=lambda r: float(r['ang']), reverse=True)
            start_pool = [r for r in clockwise if float(r.get('dist_foreign', float('inf'))) <= 200.0]
            if start_pool:
                start_red = min(start_pool, key=lambda r: float(r.get('dist_foreign', float('inf'))))
            else:
                start_red = max(clockwise, key=lambda r: float(r.get('dist_center', 0.0)))
            start_idx = next((i for i, r in enumerate(clockwise) if int(r['sid']) == int(start_red['sid']) and int(r['eidx']) == int(start_red['eidx'])), 0)
            ordered = clockwise[start_idx:] + clockwise[:start_idx]
            if not start_pool:
                ordered = sorted(ordered, key=lambda r: float(r.get('dist_center', 0.0)), reverse=True)
            reg_lines_i = district_lines.get(int(rid_i), [])
            other_boundaries = [district_base_poly.get(int(rr)).boundary for rr in district_ids if int(rr) != int(rid_i) and district_base_poly.get(int(rr)) is not None and (not district_base_poly.get(int(rr)).is_empty)]
            other_border_union = unary_union(other_boundaries) if other_boundaries else None
            connection_count: dict[tuple[int, int], int] = {(int(rr['sid']), int(rr['eidx'])): 0 for rr in ordered}
            blocked_sources: set[tuple[int, int]] = set()
            legal_static_cache: dict[tuple[float, float, float, float], bool] = {}

            def _pair_key(a: Point, b: Point) -> tuple[float, float, float, float]:
                ax, ay = float(a.x), float(a.y)
                bx, by = float(b.x), float(b.y)
                if (ax, ay) <= (bx, by):
                    return (round(ax, 3), round(ay, 3), round(bx, 3), round(by, 3))
                return (round(bx, 3), round(by, 3), round(ax, 3), round(ay, 3))

            def _legal(a: Point, b: Point) -> bool:
                try:
                    seg = LineString([(float(a.x), float(a.y)), (float(b.x), float(b.y))])
                except Exception:
                    return False
                if seg is None or seg.is_empty or float(seg.length) <= 1e-06 or float(seg.length) > 800.0:
                    return False
                endpoint_mask = a.buffer(float(dead_end_touch_tol)).union(b.buffer(float(dead_end_touch_tol)))
                body = seg.difference(endpoint_mask)
                if body is None or body.is_empty:
                    return True
                key = _pair_key(a, b)
                static_ok = legal_static_cache.get(key)
                if static_ok is None:
                    static_ok = True
                    try:
                        cand_idx = list(assigned_sindex.intersection(body.bounds))
                    except Exception:
                        cand_idx = []
                    try:
                        for idx in cand_idx:
                            og = assigned_touch.iloc[int(idx)].geometry
                            if og is None or og.is_empty:
                                continue
                            if bool(body.intersects(og)):
                                static_ok = False
                                break
                    except Exception:
                        static_ok = False
                    if static_ok:
                        try:
                            if other_border_union is not None and (not other_border_union.is_empty) and bool(body.intersects(other_border_union)):
                                static_ok = False
                        except Exception:
                            static_ok = False
                    legal_static_cache[key] = bool(static_ok)
                if not static_ok:
                    return False
                try:
                    for cseg in connector_lines_global:
                        if cseg is None or cseg.is_empty:
                            continue
                        if bool(body.intersects(cseg)):
                            return False
                except Exception:
                    return False
                return True

            def _status_sets() -> tuple[set[tuple[int, int]], set[tuple[int, int]], bool]:
                scope_geom = geometry_factory.scope_geom_from_lines(reg_lines_i + connector_lines_by_rid.get(int(rid_i), []))
                enclosed_keys: set[tuple[int, int]] = set()
                target_keys: set[tuple[int, int]] = set()
                source_keys: set[tuple[int, int]] = set()
                for rr in ordered:
                    key_rr = (int(rr['sid']), int(rr['eidx']))
                    is_enclosed = False
                    if scope_geom is not None and (not scope_geom.is_empty):
                        is_enclosed = geometry_factory.is_enclosed_point(rr['pt'], scope_geom)
                    if is_enclosed:
                        enclosed_keys.add(key_rr)
                        blocked_sources.discard(key_rr)
                        continue
                    target_keys.add(key_rr)
                    if int(connection_count.get(key_rr, 0)) < 2 and key_rr not in blocked_sources:
                        source_keys.add(key_rr)
                all_done = True
                for rr in ordered:
                    key_rr = (int(rr['sid']), int(rr['eidx']))
                    if key_rr in enclosed_keys:
                        continue
                    if int(connection_count.get(key_rr, 0)) >= 2:
                        continue
                    if key_rr in blocked_sources:
                        continue
                    all_done = False
                    break
                return (source_keys, target_keys, all_done)
            iter_guard = 0
            iter_guard_max = max(100, len(ordered) * 20)
            while True:
                iter_guard += 1
                if iter_guard > iter_guard_max:
                    break
                source_keys, target_red_keys, all_done = _status_sets()
                if all_done:
                    break
                if not source_keys or not target_red_keys:
                    break
                source_order = [rr for rr in sorted(ordered, key=lambda r: float(r.get('dist_center', 0.0)), reverse=True) if (int(rr['sid']), int(rr['eidx'])) in source_keys]
                red_best: tuple[float, int, dict[str, Any], dict[str, Any]] | None = None
                green_best: tuple[float, dict[str, Any], dict[str, Any]] | None = None
                dead_sources_now: list[tuple[int, int]] = []
                for src in source_order:
                    src_key = (int(src['sid']), int(src['eidx']))
                    src_idx = next((ii for ii, rr in enumerate(ordered) if int(rr['sid']) == int(src['sid']) and int(rr['eidx']) == int(src['eidx'])), -1)
                    if src_idx < 0:
                        continue
                    local_red: tuple[float, int, dict[str, Any], dict[str, Any]] | None = None
                    local_green: tuple[float, dict[str, Any], dict[str, Any]] | None = None
                    n_ord = len(ordered)
                    for step in range(1, n_ord):
                        dst = ordered[(src_idx + step) % n_ord]
                        dst_key = (int(dst['sid']), int(dst['eidx']))
                        if dst_key == src_key:
                            continue
                        if dst_key not in target_red_keys:
                            continue
                        dist = float(src['pt'].distance(dst['pt']))
                        if dist > 800.0:
                            continue
                        if not _legal(src['pt'], dst['pt']):
                            continue
                        if local_red is None or dist > local_red[0] or (abs(dist - local_red[0]) <= 1e-06 and int(step) < local_red[1]):
                            local_red = (dist, int(step), src, dst)
                    for gg in green:
                        distg = float(src['pt'].distance(gg['pt']))
                        if distg > 800.0:
                            continue
                        if not _legal(src['pt'], gg['pt']):
                            continue
                        if local_green is None or distg > local_green[0]:
                            local_green = (distg, src, gg)
                    if local_red is not None:
                        red_best = local_red
                        break
                    if green_best is None and local_green is not None:
                        green_best = local_green
                    if local_red is None and local_green is None:
                        dead_sources_now.append(src_key)
                if red_best is not None:
                    for key_dead in dead_sources_now:
                        blocked_sources.add(key_dead)
                    src = red_best[2]
                    dst = red_best[3]
                    src_key = (int(src['sid']), int(src['eidx']))
                    dst_key = (int(dst['sid']), int(dst['eidx']))
                    seg = LineString([(float(src['pt'].x), float(src['pt'].y)), (float(dst['pt'].x), float(dst['pt'].y))])
                    connector_lines_by_rid[int(rid_i)].append(seg)
                    connector_lines_global.append(seg)
                    connection_count[src_key] = int(connection_count.get(src_key, 0)) + 1
                    connection_count[dst_key] = int(connection_count.get(dst_key, 0)) + 1
                    continue
                if green_best is not None:
                    for key_dead in dead_sources_now:
                        blocked_sources.add(key_dead)
                    src = green_best[1]
                    gg = green_best[2]
                    src_key = (int(src['sid']), int(src['eidx']))
                    seg = LineString([(float(src['pt'].x), float(src['pt'].y)), (float(gg['pt'].x), float(gg['pt'].y))])
                    connector_lines_by_rid[int(rid_i)].append(seg)
                    connector_lines_global.append(seg)
                    connection_count[src_key] = int(connection_count.get(src_key, 0)) + 1
                    continue
                for key_dead in dead_sources_now:
                    blocked_sources.add(key_dead)
                break
            final_pass_added = 0
            all_dot_points: list[Point] = []
            for rr in ordered:
                all_dot_points.append(rr['pt'])
            for gg in green:
                all_dot_points.append(gg['pt'])

            def _enclosed_dot_count(scope_geom: Any) -> int:
                if scope_geom is None or scope_geom.is_empty:
                    return 0
                return int(sum((1 for pt in all_dot_points if geometry_factory.is_enclosed_point(pt, scope_geom))))
            final_blocked_sources: set[tuple[int, int]] = set()
            bridge_pool_limit = 40
            final_efficiency_ref: float | None = None
            final_efficiency_floor_ratio = 0.01
            final_neighbor_k = 18
            endpoint_records: list[dict[str, Any]] = []
            for rr in ordered:
                endpoint_records.append(rr)
            for gg in green:
                endpoint_records.append(gg)
            neighbor_targets_by_source: dict[tuple[int, int], list[tuple[tuple[int, int], float]]] = {}
            for rec_i in endpoint_records:
                key_i = (int(rec_i['sid']), int(rec_i['eidx']))
                near: list[tuple[tuple[int, int], float]] = []
                for rec_j in endpoint_records:
                    key_j = (int(rec_j['sid']), int(rec_j['eidx']))
                    if key_i >= key_j:
                        continue
                    dist_ij = float(rec_i['pt'].distance(rec_j['pt']))
                    if dist_ij <= 800.0:
                        near.append((key_j, dist_ij))
                near.sort(key=lambda t: t[1])
                if len(near) > final_neighbor_k:
                    near = near[:final_neighbor_k]
                neighbor_targets_by_source[key_i] = near
            final_iter_guard = 0
            final_iter_max = max(20, (len(ordered) + len(green)) * 3)
            while final_iter_guard < final_iter_max:
                final_iter_guard += 1
                if final_iter_guard % 25 == 0:
                    print(f"[polygon_builder] district {rid_pos}/{total_districts} (id={int(rid_i)}): final-pass iter {final_iter_guard}/{final_iter_max}", flush=True)
                conn_now = connector_lines_by_rid.get(int(rid_i), [])
                lines_before = reg_lines_i + conn_now
                before_geom = geometry_factory.enclosed_area_from_lines_exact(lines_before)
                scope_before = geometry_factory.scope_geom_from_lines(lines_before)
                enclosed_before = _enclosed_dot_count(scope_before)
                target_candidates: list[dict[str, Any]] = []
                source_candidates: list[dict[str, Any]] = []
                for rr in ordered:
                    key = (int(rr['sid']), int(rr['eidx']))
                    if geometry_factory.is_enclosed_point(rr['pt'], scope_before):
                        final_blocked_sources.add(key)
                        continue
                    target_candidates.append(rr)
                    if key not in final_blocked_sources:
                        source_candidates.append(rr)
                for gg in green:
                    key = (int(gg['sid']), int(gg['eidx']))
                    if geometry_factory.is_enclosed_point(gg['pt'], scope_before):
                        final_blocked_sources.add(key)
                        continue
                    target_candidates.append(gg)
                    if key not in final_blocked_sources:
                        source_candidates.append(gg)
                if len(target_candidates) < 2 or not source_candidates:
                    break
                target_by_key: dict[tuple[int, int], dict[str, Any]] = {(int(x['sid']), int(x['eidx'])): x for x in target_candidates}
                best_area_gain = 0.0
                best_dot_gain = -10**9
                best_dist = float('inf')
                best_pair: tuple[dict[str, Any], dict[str, Any]] | None = None
                legal_pairs_now: list[tuple[dict[str, Any], dict[str, Any], Any, float, float]] = []
                area_tie_eps = 1e-06
                n_c = len(source_candidates)
                for i in range(n_c):
                    src = source_candidates[i]
                    src_key = (int(src['sid']), int(src['eidx']))
                    src_has_legal = False
                    for dst_key, dist in neighbor_targets_by_source.get(src_key, []):
                        dst = target_by_key.get(dst_key)
                        if dst is None:
                            continue
                        if not _legal(src['pt'], dst['pt']):
                            continue
                        src_has_legal = True
                        seg = LineString([(float(src['pt'].x), float(src['pt'].y)), (float(dst['pt'].x), float(dst['pt'].y))])
                        if scope_before is not None and (not scope_before.is_empty):
                            try:
                                if bool(scope_before.covers(seg)):
                                    legal_pairs_now.append((src, dst, seg, dist, 0.0))
                                    continue
                            except Exception:
                                pass
                        area_gain = geometry_factory.new_enclosed_area_gain(lines_before, seg, before_geom=before_geom)
                        legal_pairs_now.append((src, dst, seg, dist, area_gain))
                        if area_gain <= 1e-06:
                            continue
                        if area_gain > best_area_gain + area_tie_eps:
                            best_area_gain = area_gain
                            best_dot_gain = -10**9
                            best_dist = dist
                            best_pair = (src, dst)
                            continue
                        if abs(area_gain - best_area_gain) <= area_tie_eps:
                            scope_after = geometry_factory.scope_geom_from_lines(lines_before + [seg])
                            enclosed_after = _enclosed_dot_count(scope_after)
                            dot_gain = int(enclosed_after - enclosed_before)
                            if dot_gain > best_dot_gain or (dot_gain == best_dot_gain and dist < best_dist):
                                best_area_gain = area_gain
                                best_dot_gain = dot_gain
                                best_dist = dist
                                best_pair = (src, dst)
                    if not src_has_legal:
                        final_blocked_sources.add(src_key)
                if best_pair is None or best_area_gain <= 1e-06:
                    bridge_best_total = 0.0
                    bridge_best_dist = float('inf')
                    bridge_best_first: tuple[dict[str, Any], dict[str, Any], Any] | None = None
                    if legal_pairs_now:
                        positive_pool = [t for t in legal_pairs_now if float(t[4]) > 1e-06]
                        zero_pool = [t for t in legal_pairs_now if float(t[4]) <= 1e-06]
                        positive_pool.sort(key=lambda t: (float(t[4]), -float(t[3])), reverse=True)
                        zero_pool.sort(key=lambda t: float(t[3]))
                        bridge_pool: list[tuple[dict[str, Any], dict[str, Any], Any, float, float]] = []
                        pos_keep = max(8, int(bridge_pool_limit * 0.6))
                        zero_keep = max(8, bridge_pool_limit - pos_keep)
                        bridge_pool.extend(positive_pool[:pos_keep])
                        bridge_pool.extend(zero_pool[:zero_keep])
                        if len(bridge_pool) > bridge_pool_limit:
                            bridge_pool = bridge_pool[:bridge_pool_limit]
                        seg_key_by_pair: dict[tuple[int, int, int, int], tuple[Any, Any, Any]] = {}
                        for src_x, dst_x, seg_x, _dist_x, _gx in bridge_pool:
                            key_x = (int(src_x['sid']), int(src_x['eidx']), int(dst_x['sid']), int(dst_x['eidx']))
                            endpoint_mask_x = src_x['pt'].buffer(float(dead_end_touch_tol)).union(dst_x['pt'].buffer(float(dead_end_touch_tol)))
                            body_x = seg_x.difference(endpoint_mask_x)
                            seg_key_by_pair[key_x] = (seg_x, body_x, endpoint_mask_x)
                        for src1, dst1, seg1, _dist1, g1 in bridge_pool:
                            connector_lines_global.append(seg1)
                            second_best = 0.0
                            second_best_dist = float('inf')
                            conn_plus_first = conn_now + [seg1]
                            before_geom_after_first = geometry_factory.enclosed_area_from_lines_exact(reg_lines_i + conn_plus_first)
                            key1 = (int(src1['sid']), int(src1['eidx']), int(dst1['sid']), int(dst1['eidx']))
                            seg1_geom, _body1, _mask1 = seg_key_by_pair.get(key1, (seg1, None, None))
                            for src2, dst2, seg2, _dist2, _g2 in bridge_pool:
                                if (int(src1['sid']), int(src1['eidx']), int(dst1['sid']), int(dst1['eidx'])) == (int(src2['sid']), int(src2['eidx']), int(dst2['sid']), int(dst2['eidx'])):
                                    continue
                                key2 = (int(src2['sid']), int(src2['eidx']), int(dst2['sid']), int(dst2['eidx']))
                                _seg2_geom, body2, _mask2 = seg_key_by_pair.get(key2, (seg2, None, None))
                                if body2 is not None and (not body2.is_empty):
                                    try:
                                        if bool(body2.intersects(seg1_geom)):
                                            continue
                                    except Exception:
                                        if not _legal(src2['pt'], dst2['pt']):
                                            continue
                                elif not _legal(src2['pt'], dst2['pt']):
                                    continue
                                g_after_first = geometry_factory.new_enclosed_area_gain(reg_lines_i + conn_plus_first, seg2, before_geom=before_geom_after_first)
                                if g_after_first > second_best + 1e-06 or (abs(g_after_first - second_best) <= 1e-06 and float(_dist2) < second_best_dist):
                                    second_best = g_after_first
                                    second_best_dist = float(_dist2)
                            connector_lines_global.pop()
                            total_two_step = float(g1) + float(second_best)
                            total_two_step_dist = float(_dist1) + (0.0 if not math.isfinite(second_best_dist) else float(second_best_dist))
                            if total_two_step > bridge_best_total + 1e-06 or (abs(total_two_step - bridge_best_total) <= 1e-06 and total_two_step_dist < bridge_best_dist):
                                bridge_best_total = total_two_step
                                bridge_best_dist = total_two_step_dist
                                bridge_best_first = (src1, dst1, seg1)
                    if bridge_best_first is None or bridge_best_total <= 1e-06:
                        break
                    if final_efficiency_ref is not None and math.isfinite(bridge_best_dist) and bridge_best_dist > 1e-06:
                        bridge_eff = float(bridge_best_total) / float(bridge_best_dist)
                        if bridge_eff < float(final_efficiency_ref) * float(final_efficiency_floor_ratio):
                            break
                    src_b, dst_b, seg_b = bridge_best_first
                    src_b_key = (int(src_b['sid']), int(src_b['eidx']))
                    dst_b_key = (int(dst_b['sid']), int(dst_b['eidx']))
                    connector_lines_by_rid[int(rid_i)].append(seg_b)
                    connector_lines_global.append(seg_b)
                    connection_count[src_b_key] = int(connection_count.get(src_b_key, 0)) + 1
                    connection_count[dst_b_key] = int(connection_count.get(dst_b_key, 0)) + 1
                    final_pass_added += 1
                    continue
                if math.isfinite(best_dist) and best_dist > 1e-06:
                    pair_eff = float(best_area_gain) / float(best_dist)
                    if final_efficiency_ref is None:
                        final_efficiency_ref = pair_eff
                    elif pair_eff < float(final_efficiency_ref) * float(final_efficiency_floor_ratio) and best_dot_gain <= 0:
                        break
                src = best_pair[0]
                dst = best_pair[1]
                src_key = (int(src['sid']), int(src['eidx']))
                dst_key = (int(dst['sid']), int(dst['eidx']))
                seg = LineString([(float(src['pt'].x), float(src['pt'].y)), (float(dst['pt'].x), float(dst['pt'].y))])
                connector_lines_by_rid[int(rid_i)].append(seg)
                connector_lines_global.append(seg)
                connection_count[src_key] = int(connection_count.get(src_key, 0)) + 1
                connection_count[dst_key] = int(connection_count.get(dst_key, 0)) + 1
                final_pass_added += 1
            final_pass_count_by_rid[int(rid_i)] = int(final_pass_added)
            print(f"[polygon_builder] district {rid_pos}/{total_districts} (id={int(rid_i)}): connectors={len(connector_lines_by_rid.get(int(rid_i), []))} final_pass_added={int(final_pass_added)}", flush=True)
        print("[polygon_builder] assembling output polygons", flush=True)
        for rid_pos, rid_i in enumerate(order, start=1):
            reg_street_keys = district_sids.get(int(rid_i), [])
            reg_lines = district_lines.get(int(rid_i), [])
            if not reg_street_keys or not reg_lines:
                continue
            base_poly = district_base_poly.get(int(rid_i))
            conn_lines = connector_lines_by_rid.get(int(rid_i), [])
            enclosed = geometry_factory.enclosed_area_from_lines_exact(reg_lines + conn_lines)
            holes = geometry_factory.interior_holes_as_polys(base_poly)
            enclosed_from_holes = unary_union(holes).buffer(0) if holes else None
            parts: list[Any] = []
            if base_poly is not None and (not base_poly.is_empty):
                parts.append(base_poly)
            if enclosed is not None and (not enclosed.is_empty):
                parts.append(enclosed)
            if enclosed_from_holes is not None and (not enclosed_from_holes.is_empty):
                parts.append(enclosed_from_holes)
            emit_geom = unary_union(parts).buffer(0) if parts else None
            if emit_geom is None or emit_geom.is_empty:
                continue
            reg_buildings = b[b_region == int(rid_i)]
            total_demand = float(reg_buildings[polygon_demand_column].sum()) if polygon_demand_column in reg_buildings.columns else 0.0
            total_len_m = float(sum((float(street_lengths_m.get(int(sid), 0.0)) for sid in reg_street_keys)))
            dead_end_members: list[int] = []
            rows_sid = assigned_touch[assigned_touch[id_column] == int(rid_i)]
            for _, rrow in rows_sid.iterrows():
                sid = int(rrow[street_key_col])
                eps = geometry_factory.line_endpoints(rrow.geometry)
                if any((not _endpoint_touches_other_assigned(ep, int(sid))) for ep in eps):
                    dead_end_members.append(int(sid))
            dead_end_members = sorted(set(dead_end_members))
            print(f"[polygon_builder] emit {rid_pos}/{total_districts} (id={int(rid_i)}): demand_mwh={float(total_demand):.2f} streets={len(reg_street_keys)}", flush=True)
            out_row: dict[str, Any] = {
                street_id_column: '|'.join(map(str, list(dict.fromkeys((street_key_to_raw_id.get(sid) for sid in reg_street_keys))))),
                'street_count': len(reg_street_keys),
                'street_length_m': total_len_m,
                'nondemand_street_length_m': total_len_m,
                'building_count': int(len(reg_buildings)),
                'dead_end_count': int(len(dead_end_members)),
                'connector_count': int(len(conn_lines)),
                'final_pass_connector_count': int(final_pass_count_by_rid.get(int(rid_i), 0)),
                polygon_demand_column: total_demand,
                '_street_members': reg_street_keys,
                'geometry': emit_geom,
                id_column: int(rid_i),
            }
            cluster_rows.append(out_row)
        polygons_pre = gpd.GeoDataFrame(cluster_rows, geometry='geometry', crs=b.crs)
        if polygons_pre.empty:
            raise ValueError('polygon_builder preassigned mode produced no polygons')
        polygons_pre = polygons_pre.sort_values(id_column).reset_index(drop=True)
        if not keep_internal_columns:
            polygons_pre = polygons_pre.drop(columns=['_street_members'], errors='ignore')
        return polygons_pre
    group_cols = [assignment_col]
    node_rows: list[dict[str, Any]] = []
    node_id = 0
    grouped = b.dropna(subset=[assignment_col]).groupby(group_cols, dropna=False)
    for group_key, subset in grouped:
        subset_demand = float(subset[polygon_demand_column].sum())
        street_key = group_key[0] if isinstance(group_key, tuple) else group_key
        if demand_data is not None:
            if subset_demand <= 0.0:
                continue
            if demand_indicator_by_street is not None:
                if float(demand_indicator_by_street.get(int(street_key), 0.0)) <= min_demand_indicator:
                    continue
        buffered = [geom.buffer(float(building_buffer_m)) for geom in subset.geometry if geom is not None]
        if not buffered:
            continue
        poly = unary_union(buffered).buffer(0)
        if poly is None or poly.is_empty:
            continue
        street_len_m = float(street_lengths_m.get(street_key, 0.0))
        rec: dict[str, Any] = {'_node_id': node_id, street_id_column: street_key_to_raw_id.get(street_key), 'street_length_m': street_len_m, 'building_count': int(len(subset)), polygon_demand_column: subset_demand, '_street_members': [street_key], 'geometry': poly}
        node_rows.append(rec)
        node_id += 1
    if not node_rows:
        raise ValueError('polygon_builder produced no polygons')
    nodes = gpd.GeoDataFrame(node_rows, geometry='geometry', crs=b.crs)
    street_geom = s_work.set_index(street_key_col)['geometry'].to_dict()
    full_adj = RegionTopologyGeometry.build_street_adjacency(s_work[[street_key_col, 'geometry']], street_key_col=street_key_col, tolerance_m=float(street_connect_tolerance_m))
    street_comp: dict[int, int] = {int(sid): int(comp_id) for comp_id, street_nodes in enumerate(RegionTopologyBase.connected_components(full_adj)) for sid in street_nodes}
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
    for comp in RegionTopologyBase.connected_components(demand_adj, set(demand_street_keys)):
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
            path_nodes = RegionTopologyBase.trace_prev_chain(prev, hit_prev.get(tgt_idx))
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
        cluster_records.append(out_row)
    polygons = gpd.GeoDataFrame(cluster_records, geometry='geometry', crs=b.crs)
    if '_street_members' in polygons.columns:
        polygons['_sort_min_street_key'] = polygons['_street_members'].map(lambda v: min((int(x) for x in v)) if isinstance(v, list) and len(v) > 0 else 10 ** 15)
        polygons = polygons.sort_values(['_sort_min_street_key'], kind='mergesort')
        polygons = polygons.drop(columns=['_sort_min_street_key'], errors='ignore')
    polygons = polygons.reset_index(drop=True)
    polygons[id_column] = range(len(polygons))
    if not keep_internal_columns:
        polygons = polygons.drop(columns=['_street_members'], errors='ignore')
    return polygons

def _count_disconnected_regions(streets: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame, *, tolerance_m: float, buildings: gpd.GeoDataFrame | None, segment_streets_by_building_projections: bool, segment_projection_buffer_m: float, street_id_column: str) -> int:
    if '_street_members' not in polygons.columns:
        return 0
    s = streets[[street_id_column, 'geometry']].copy().explode(index_parts=False).reset_index(drop=True)
    if segment_streets_by_building_projections and buildings is not None and (not buildings.empty):
        s = _segment_streets_by_building_projections(s, buildings, street_id_column=street_id_column, projection_buffer_m=float(segment_projection_buffer_m))
    s['_street_key'] = range(len(s))
    adj = RegionTopologyGeometry.build_street_adjacency(s[['_street_key', 'geometry']], street_key_col='_street_key', tolerance_m=float(tolerance_m))
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
    max_nondemand_to_demand_ratio = RegionTopologyBase.max_nondemand_to_demand_ratio_from_share(demand_share_pct)
    split_region_min_segments = max(2, int(small_islands_max_segments))
    street_demand_mwh: dict[int, float] = {}
    for _, row in p[['_demand_street_members', 'annual_demand_mwh']].iterrows():
        members = row.get('_demand_street_members', [])
        if not isinstance(members, list) or not members:
            continue
        dsum = RegionTopologyBase.to_float(row.get('annual_demand_mwh', 0.0), 0.0)
        per = dsum / float(len(members)) if members else 0.0
        for sid in members:
            sid_i = int(sid)
            street_demand_mwh[sid_i] = float(street_demand_mwh.get(sid_i, 0.0) + per)
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
        owner_now = RegionTopologyBase.owner_map_from_assigned(assigned_now, street_key_col='_street_key', region_id_col=region_id_column)
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
            dsum = RegionTopologyBase.to_float(row.get('annual_demand_mwh', 0.0), 0.0)
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
        poly_geoms = [g for g in grp.geometry.tolist() if g is not None and (not g.is_empty)] if 'geometry' in grp.columns else []
        if not poly_geoms:
            continue
        row['geometry'] = unary_union(poly_geoms).buffer(0)
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

def build_region_topology(*, buildings: gpd.GeoDataFrame, streets: gpd.GeoDataFrame, demand_data: pd.DataFrame | gpd.GeoDataFrame, config: RegionTopologyConfig) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[str, int]]:
    """Build region polygons from street/building demand inputs.
    Optionally filters to one city, clips the street network to relevant buildings,
    and delegates partitioning to polygon_builder with demand/length caps. It then
    maps region IDs back to street segments, merges tiny disconnected islands with
    graph constraints and returns polygons.
    """
    cfg = config
    caps = cfg.build_caps()
    b, s = (buildings.copy(), streets.copy())
    if cfg.city_column and cfg.city_column in b.columns and (cfg.city_column in s.columns):
        city_value_resolved = cfg.city_value
        if city_value_resolved is None:
            vals = b[cfg.city_column].dropna().astype(str)
            if vals.empty:
                raise ValueError(f"No values in city column '{cfg.city_column}'")
            city_value_resolved = str(vals.mode().iloc[0])
        b = b[b[cfg.city_column].astype(str) == str(city_value_resolved)].copy()
        s = s[s[cfg.city_column].astype(str) == str(city_value_resolved)].copy()
    if b.crs is None or s.crs is None:
        raise ValueError('buildings and streets must both have CRS')
    s = s if b.crs == s.crs else s.to_crs(b.crs)
    if b.empty:
        s = s.iloc[0:0].copy()
    else:
        area = b.geometry.union_all().convex_hull.buffer(float(cfg.clip_buffer_m))
        s = s[s.geometry.intersects(area)].copy()
    polygons = polygon_builder(buildings=b, streets=s, demand_data=demand_data, **cfg.polygon_builder_kwargs(caps=caps, keep_internal_columns=True))
    streets_with_region = s[[cfg.street_id_column, 'geometry']].copy().explode(index_parts=False).reset_index(drop=True)
    if cfg.segment_streets_by_building_projections and (not b.empty):
        streets_with_region = _segment_streets_by_building_projections(streets_with_region, b, street_id_column=cfg.street_id_column, projection_buffer_m=float(cfg.segment_projection_buffer_m))
    streets_with_region['_street_key'] = range(len(streets_with_region))
    if '_street_members' not in polygons.columns:
        raise ValueError("Expected '_street_members' in polygons")
    rid_by_key: dict[int, int] = {}
    demand_keys: set[int] = set()
    for _, r in polygons[[cfg.region_id_column, '_street_members']].iterrows():
        if not isinstance(r['_street_members'], list):
            continue
        for k in r['_street_members']:
            rid_by_key[int(k)] = int(r[cfg.region_id_column])
    if '_demand_street_members' in polygons.columns:
        for vals in polygons['_demand_street_members'].tolist():
            if isinstance(vals, list):
                demand_keys.update((int(v) for v in vals))
    streets_with_region[cfg.region_id_column] = streets_with_region['_street_key'].map(rid_by_key)
    streets_with_region['_is_demand_street'] = streets_with_region['_street_key'].isin(demand_keys)
    if cfg.small_islands:
        polygons, streets_with_region = _merge_small_regions_by_street_graph(polygons=polygons, streets_with_region=streets_with_region, **cfg.small_island_merge_kwargs(enable_final_resort=not bool(cfg.polynesia)))
    mantra = {'raw_street_ids_split_across_regions': int((streets_with_region.dropna(subset=[cfg.region_id_column]).groupby(cfg.street_id_column)[cfg.region_id_column].nunique() > 1).sum()), 'unassigned_street_segments': int(streets_with_region[cfg.region_id_column].isna().sum()), 'cross_region_crossings': 0, 'junction_overload_points': 0, 'disconnected_regions': int(_count_disconnected_regions(streets=s, polygons=polygons, tolerance_m=cfg.connect_tolerance_m, buildings=b, segment_streets_by_building_projections=cfg.segment_streets_by_building_projections, segment_projection_buffer_m=cfg.segment_projection_buffer_m, street_id_column=cfg.street_id_column))}
    return (polygons, streets_with_region, mantra)

class RegionTopologyEngine(RegionTopologyGeometry):

    @staticmethod
    def build(*, buildings: gpd.GeoDataFrame, streets: gpd.GeoDataFrame, demand_data: pd.DataFrame | gpd.GeoDataFrame, config: RegionTopologyConfig) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict[str, int]]:
        return build_region_topology(buildings=buildings, streets=streets, demand_data=demand_data, config=config)

    @staticmethod
    def build_polygons_from_assigned_streets(*, buildings: gpd.GeoDataFrame, assigned_streets: gpd.GeoDataFrame, demand_data: pd.DataFrame | gpd.GeoDataFrame | None, caps: RegionCaps, config: RegionTopologyConfig) -> gpd.GeoDataFrame:
        return polygon_builder(
            buildings=buildings,
            streets=assigned_streets,
            demand_data=demand_data,
            **config.polygon_builder_kwargs(caps=caps, segment_streets_by_building_projections=False, keep_internal_columns=True),
        )
