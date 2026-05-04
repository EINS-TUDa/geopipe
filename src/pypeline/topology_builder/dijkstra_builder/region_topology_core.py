"""Region topology core pipeline and seed construction.

Owns configuration/types, seed building, and orchestration. Geometry and
post-seed reconciliation algorithms live in `region_topology_engine`.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from collections import deque
import heapq
import geopandas as gpd
import pandas as pd


class RegionTopologyBase:

    @staticmethod
    def to_float(value: Any, default: float = 0.0) -> float:
        num = pd.to_numeric(pd.Series([value]), errors='coerce').fillna(float(default)).iloc[0]
        return float(num)

    @staticmethod
    def owner_map_from_assigned(assigned: pd.DataFrame, *, street_key_col: str, region_id_col: str) -> dict[int, int]:
        pairs = assigned[[street_key_col, region_id_col]].dropna(subset=[street_key_col, region_id_col])
        if pairs.empty:
            return {}
        streets = pairs[street_key_col].astype(int, copy=False).to_numpy()
        regions = pairs[region_id_col].astype(int, copy=False).to_numpy()
        return {int(sid): int(rid) for sid, rid in zip(streets, regions)}

    @staticmethod
    def owner_map_from_members_column(seed_rows: pd.DataFrame, *, owner_col: str, members_col: str) -> dict[int, int]:
        if owner_col not in seed_rows.columns or members_col not in seed_rows.columns:
            return {}
        exploded = seed_rows[[owner_col, members_col]].explode(members_col)
        exploded = exploded.dropna(subset=[owner_col, members_col])
        if exploded.empty:
            return {}
        owners = exploded[owner_col].astype(int, copy=False).to_numpy()
        members = exploded[members_col].astype(int, copy=False).to_numpy()
        return {int(member): int(owner) for owner, member in zip(owners, members)}

    @staticmethod
    def member_key_set_from_list_column(seed_rows: pd.DataFrame, *, members_col: str) -> set[int]:
        if members_col not in seed_rows.columns:
            return set()
        exploded = seed_rows[[members_col]].explode(members_col).dropna(subset=[members_col])
        if exploded.empty:
            return set()
        return set((int(v) for v in exploded[members_col].astype(int, copy=False).to_numpy()))

    @staticmethod
    def sum_street_values_from_dict_column(seed_rows: pd.DataFrame, *, dict_col: str) -> dict[int, float]:
        if dict_col not in seed_rows.columns:
            return {}
        out: dict[int, float] = {}
        for payload in seed_rows[dict_col].tolist():
            if not isinstance(payload, dict):
                continue
            for sid, val in payload.items():
                sid_i = int(sid)
                out[sid_i] = float(out.get(sid_i, 0.0) + float(val))
        return out

    @staticmethod
    def streets_by_region(assigned_df: pd.DataFrame, *, street_key_col: str, region_id_col: str) -> dict[int, set[int]]:
        pairs = assigned_df[[street_key_col, region_id_col]].dropna(subset=[street_key_col, region_id_col])
        if pairs.empty:
            return {}
        grouped = pairs.groupby(region_id_col, sort=False)[street_key_col].agg(list)
        return {int(rid): set((int(sid) for sid in sids)) for rid, sids in grouped.items()}

    @staticmethod
    def aggregate_region_values(streets_by_region: dict[int, set[int]], values_by_street: dict[int, float]) -> dict[int, float]:
        return {
            int(rid): float(sum((float(values_by_street.get(int(sid), 0.0)) for sid in sids)))
            for rid, sids in streets_by_region.items()
        }

    @staticmethod
    def trace_prev_chain(prev: dict[int, int | None], start: int | None) -> list[int]:
        path: list[int] = []
        cur = None if start is None else int(start)
        while cur is not None:
            path.append(int(cur))
            cur = prev.get(int(cur))
        return path

    @staticmethod
    def connected_components(adjacency: dict[int, set[int]], nodes: set[int] | None = None) -> list[set[int]]:
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

@dataclass(frozen=True)
class RegionCaps:
    max_demand_mwh: float | None
    max_street_length_km: float | None
    max_nondemand_street_km: float | None = None

    def as_region_seed_caps(self) -> dict[str, float | None]:
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

    def region_seed_builder_kwargs(
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
            'caps': caps.as_region_seed_caps(),
            'demand_share_pct': float(self.demand_share_pct),
            'max_inter_region_connector_m': 600.0,
            'street_connect_tolerance_m': float(self.connect_tolerance_m),
            'street_corridor_buffer_m': 20.0,
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


from pypeline.topology_builder.region_topology_engine import (
    RegionTopologyGeometry,
    count_disconnected_regions as _count_disconnected_regions,
    collapse_overloaded_junction_conflicts as _collapse_overloaded_junction_conflicts,
    merge_small_regions_by_street_graph as _merge_small_regions_by_street_graph,
    segment_streets_by_building_projections as _segment_streets_by_building_projections,
)

def _region_seed_builder_impl(
    buildings: gpd.GeoDataFrame,
    streets: gpd.GeoDataFrame,
    *,
    demand_data: pd.DataFrame | gpd.GeoDataFrame | None = None,
    demand_value_column: str = 'annual_demand_mwh',
    street_id_column: str = 'street_id',
    building_street_column: str | None = None,
    building_id_column: str | None = None,
    demand_building_column: str | None = None,
    id_column: str = 'id',
    building_buffer_m: float = 2.0,
    street_buffer_m: float = 8.0,
    segment_streets_by_building_projections: bool = False,
    segment_projection_buffer_m: float | None = None,
    demand_street_indicator_column: str | None = None,
    demand_street_indicator_min: float = 0.0,
    street_corridor_buffer_m: float = 15.0,
    caps: dict[str, float | None] | None = None,
    demand_share_pct: float | None = None,
    max_inter_region_connector_m: float | None = 100.0,
    street_connect_tolerance_m: float = 3.0,
    keep_internal_columns: bool = False,
) -> pd.DataFrame:
    # Kept for API compatibility; graph-only seeds do not construct corridor geometry.
    _ = street_corridor_buffer_m

    if buildings.empty:
        raise ValueError('region_seed_builder requires non-empty buildings')
    if streets.empty:
        raise ValueError('region_seed_builder requires non-empty streets')
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
        s_work = _segment_streets_by_building_projections(
            s_work,
            b,
            street_id_column=street_id_column,
            projection_buffer_m=seg_buffer_m,
        )

    s_work['_street_key'] = range(len(s_work))
    street_key_col = '_street_key'
    street_key_to_raw_id = s_work.set_index(street_key_col)[street_id_column].to_dict()

    cap_cfg = {
        'max_demand_mwh': None,
        'max_street_length_km': 10.0,
        'max_nondemand_street_km': None,
        **dict(caps or {}),
    }

    assignment_col = '_assigned_street'

    def _fill_nearest_street_assignment(mask: pd.Series) -> None:
        if not bool(mask.any()):
            return
        nearest = gpd.sjoin_nearest(
            b.loc[mask, ['geometry']],
            s_work[[street_key_col, 'geometry']],
            how='left',
        )
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

    region_demand_column = demand_value_column
    b[region_demand_column] = 0.0
    if demand_data is not None:
        d = pd.DataFrame(demand_data).copy()
        if demand_value_column not in d.columns:
            raise ValueError(f"Missing demand value column '{demand_value_column}' in demand_data")
        demand_series = None
        if (
            demand_building_column
            and building_id_column
            and (demand_building_column in d.columns)
            and (building_id_column in b.columns)
        ):
            demand_by_building = d.groupby(demand_building_column, dropna=False)[demand_value_column].sum(min_count=1).fillna(0.0)
            demand_series = b[building_id_column].map(demand_by_building)
        if demand_series is None:
            raise ValueError('demand_data mapping failed')
        b[region_demand_column] = demand_series.fillna(0.0)

    demand_indicator_by_street = None
    if indicator_col and indicator_col in s_work.columns:
        demand_indicator_by_street = pd.to_numeric(s_work[indicator_col], errors='coerce').fillna(0.0)
        demand_indicator_by_street.index = s_work[street_key_col].astype(int)

    min_demand_indicator = float(demand_street_indicator_min)

    def _indicator_ok(sid: int) -> bool:
        if demand_indicator_by_street is None:
            return True
        return float(demand_indicator_by_street.get(int(sid), 0.0)) > min_demand_indicator

    assigned_buildings = b.dropna(subset=[assignment_col]).copy()
    building_count_by_street: dict[int, int] = {}
    street_demand_mwh: dict[int, float] = {}
    if not assigned_buildings.empty:
        counts = assigned_buildings.groupby(assignment_col, dropna=False).size()
        building_count_by_street = {int(sid): int(cnt) for sid, cnt in counts.items() if pd.notna(sid)}
        sums = assigned_buildings.groupby(assignment_col, dropna=False)[region_demand_column].sum(min_count=1).fillna(0.0)
        street_demand_mwh = {int(sid): float(val) for sid, val in sums.items() if pd.notna(sid)}

    if demand_data is None:
        demand_street_keys = {int(sid) for sid in building_count_by_street.keys() if _indicator_ok(int(sid))}
    else:
        demand_street_keys = {
            int(sid)
            for sid, val in street_demand_mwh.items()
            if float(val) > 0.0 and _indicator_ok(int(sid))
        }

    length_series = s_work.set_index(street_key_col).geometry.length.fillna(0.0)
    length_by_sid = {int(sid): float(v) for sid, v in length_series.items()}

    def _street_ids_payload(street_keys: list[int]) -> str:
        vals = list(dict.fromkeys((street_key_to_raw_id.get(int(sid)) for sid in street_keys)))
        return '|'.join(map(str, vals))

    if not demand_street_keys:
        raise ValueError('region_seed_builder produced no demand streets')

    max_demand = None if cap_cfg['max_demand_mwh'] is None else float(cap_cfg['max_demand_mwh'])
    max_street_length_m = None if cap_cfg['max_street_length_km'] is None else float(cap_cfg['max_street_length_km']) * 1000.0
    max_nondemand_street_m = None if cap_cfg['max_nondemand_street_km'] is None else float(cap_cfg['max_nondemand_street_km']) * 1000.0
    max_inter_region_connector_len_m = None if max_inter_region_connector_m is None else float(max_inter_region_connector_m)
    max_nondemand_ratio = RegionTopologyBase.max_nondemand_to_demand_ratio_from_share(demand_share_pct)

    demand_by_sid: dict[int, float] = {
        int(sid): float(street_demand_mwh.get(int(sid), 0.0))
        for sid in demand_street_keys
    }

    full_adj = RegionTopologyGeometry.build_street_adjacency(
        s_work[[street_key_col, 'geometry']],
        street_key_col=street_key_col,
        tolerance_m=float(street_connect_tolerance_m),
    )

    def _fits_caps(*, demand_sum: float, street_len_sum: float) -> bool:
        return (max_demand is None or demand_sum <= max_demand) and (max_street_length_m is None or street_len_sum <= max_street_length_m)

    demand_adj: dict[int, set[int]] = {
        int(sid): {int(nb) for nb in full_adj.get(int(sid), set()) if int(nb) in demand_street_keys and int(nb) != int(sid)}
        for sid in demand_street_keys
    }

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
        dsum = float(sum((demand_by_sid.get(int(sid), 0.0) for sid in region)))
        lsum = float(sum((float(length_by_sid.get(int(sid), 0.0)) for sid in region)))
        if _fits_caps(demand_sum=dsum, street_len_sum=lsum):
            return [region]
        if len(region) <= 1:
            raise RuntimeError('region_seed_builder infeasible: single demand street exceeds caps')

        seed_a = max(region, key=lambda sid: demand_by_sid.get(int(sid), 0.0))
        dist_a = _bfs_dist(seed_a, region)
        seed_b = max(region - {seed_a}, key=lambda sid: dist_a.get(int(sid), -1)) if len(region) > 1 else seed_a
        da = _bfs_dist(seed_a, region)
        db = _bfs_dist(seed_b, region)

        part_a: set[int] = set()
        part_b: set[int] = set()
        dem_a = 0.0
        dem_b = 0.0
        for sid in sorted(region):
            va = da.get(int(sid), 10 ** 9)
            vb = db.get(int(sid), 10 ** 9)
            if va < vb:
                part_a.add(int(sid))
                dem_a += float(demand_by_sid.get(int(sid), 0.0))
            elif vb < va:
                part_b.add(int(sid))
                dem_b += float(demand_by_sid.get(int(sid), 0.0))
            elif dem_a <= dem_b:
                part_a.add(int(sid))
                dem_a += float(demand_by_sid.get(int(sid), 0.0))
            else:
                part_b.add(int(sid))
                dem_b += float(demand_by_sid.get(int(sid), 0.0))

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
        raise ValueError('region_seed_builder produced no regions')

    def _region_streets(region: dict[str, set[int]]) -> set[int]:
        return set((int(s) for s in region['demand'])) | set((int(s) for s in region['connectors']))

    def _region_stats(region: dict[str, set[int]]) -> tuple[float, float, float, float]:
        demand_len = float(sum((float(length_by_sid.get(int(sid), 0.0)) for sid in region['demand'])))
        nondemand_len = float(sum((float(length_by_sid.get(int(sid), 0.0)) for sid in region['connectors'])))
        demand_sum = float(sum((float(demand_by_sid.get(int(sid), 0.0)) for sid in region['demand'])))
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

        owner: dict[int, int] = {
            int(sid): int(idx_r)
            for idx_r, rr in enumerate(regions)
            if rr is not None
            for sid in _region_streets(rr)
        }

        dist: dict[int, float] = {int(sid): 0.0 for sid in src_streets}
        prev: dict[int, int | None] = {int(sid): None for sid in src_streets}
        pq: list[tuple[float, int]] = [(0.0, int(sid)) for sid in src_streets]
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
                    if cur_cost < hit_cost.get(int(nb_owner), float('inf')):
                        hit_cost[int(nb_owner)] = float(cur_cost)
                        hit_prev[int(nb_owner)] = int(cur)
                    continue
                if nb_owner is not None and nb_owner == src_idx:
                    step = 0.0
                else:
                    step = float(length_by_sid.get(int(nb), 0.0))
                cand = cur_cost + step
                if max_inter_region_connector_len_m is not None and cand > max_inter_region_connector_len_m:
                    continue
                if cand < dist.get(int(nb), float('inf')):
                    dist[int(nb)] = float(cand)
                    prev[int(nb)] = int(cur)
                    heapq.heappush(pq, (float(cand), int(nb)))

        src_demand, _, src_demand_len, src_non_len = _region_stats(src)
        best: tuple[float, int, int, set[int]] | None = None

        for tgt_idx, cost in hit_cost.items():
            tgt = regions[int(tgt_idx)]
            if tgt is None:
                continue

            path_nodes = RegionTopologyBase.trace_prev_chain(prev, hit_prev.get(int(tgt_idx)))
            connector_add = {int(sid) for sid in path_nodes if owner.get(int(sid)) is None}

            tgt_demand, _, tgt_demand_len, tgt_non_len = _region_stats(tgt)
            merged_demand = float(src_demand + tgt_demand)
            merged_demand_len = float(src_demand_len + tgt_demand_len)
            merged_non_len = float(src_non_len + tgt_non_len + sum((float(length_by_sid.get(int(sid), 0.0)) for sid in connector_add)))
            merged_total_len = float(merged_demand_len + merged_non_len)

            if not _fits_caps(demand_sum=merged_demand, street_len_sum=merged_total_len):
                continue
            if max_nondemand_street_m is not None and merged_non_len > max_nondemand_street_m:
                continue
            if max_nondemand_ratio is not None and merged_demand_len > 0.0 and (merged_non_len / merged_demand_len >= max_nondemand_ratio):
                continue

            tgt_size = int(len(_region_streets(tgt)))
            key = (float(cost), int(tgt_size), int(tgt_idx))
            if best is None or key < (best[0], best[1], best[2]):
                best = (float(cost), int(tgt_size), int(tgt_idx), set(connector_add))

        if best is None:
            return None
        return (int(best[2]), set(best[3]))

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
                    best = _try_merge_from(int(src_idx))
                    if best is None:
                        continue
                    tgt_idx, add_conn = best
                    if regions[int(tgt_idx)] is None or regions[int(src_idx)] is None:
                        continue
                    regions[int(tgt_idx)]['demand'] = set(regions[int(tgt_idx)]['demand']) | set(regions[int(src_idx)]['demand'])
                    regions[int(tgt_idx)]['connectors'] = set(regions[int(tgt_idx)]['connectors']) | set(regions[int(src_idx)]['connectors']) | set(add_conn)
                    regions[int(src_idx)] = None
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
        demand_len_m = float(sum((float(length_by_sid.get(int(sid), 0.0)) for sid in demand_set)))
        total_len_m = float(sum((float(length_by_sid.get(int(sid), 0.0)) for sid in reg_streets)))
        nondemand_len_m = max(0.0, float(total_len_m - demand_len_m))
        total_demand = float(sum((float(demand_by_sid.get(int(sid), 0.0)) for sid in demand_set)))

        if not _fits_caps(demand_sum=total_demand, street_len_sum=total_len_m):
            continue
        if max_nondemand_street_m is not None and nondemand_len_m > max_nondemand_street_m:
            continue
        if max_nondemand_ratio is not None and demand_len_m > 0.0 and (nondemand_len_m / demand_len_m >= max_nondemand_ratio):
            continue

        out_row: dict[str, Any] = {
            street_id_column: _street_ids_payload(reg_streets),
            'street_count': len(reg_streets),
            'street_length_m': total_len_m,
            'nondemand_street_length_m': nondemand_len_m,
            'building_count': int(sum((int(building_count_by_street.get(int(sid), 0)) for sid in reg_streets))),
            region_demand_column: total_demand,
            '_street_members': reg_streets,
            '_demand_street_members': sorted((int(sid) for sid in demand_set)),
            '_street_demand_mwh': {
                int(sid): float(demand_by_sid.get(int(sid), 0.0))
                for sid in demand_set
                if float(demand_by_sid.get(int(sid), 0.0)) > 0.0
            },
        }
        cluster_records.append(out_row)

    seed_rows = pd.DataFrame(cluster_records)
    if seed_rows.empty:
        raise ValueError('region_seed_builder produced no regions')

    if '_street_members' in seed_rows.columns:
        seed_rows['_sort_min_street_key'] = seed_rows['_street_members'].map(
            lambda v: min((int(x) for x in v)) if isinstance(v, list) and len(v) > 0 else 10 ** 15
        )
        seed_rows = seed_rows.sort_values(['_sort_min_street_key'], kind='mergesort')
        seed_rows = seed_rows.drop(columns=['_sort_min_street_key'], errors='ignore')

    seed_rows = seed_rows.reset_index(drop=True)
    seed_rows[id_column] = range(len(seed_rows))

    if not keep_internal_columns:
        seed_rows = seed_rows.drop(columns=['_street_members'], errors='ignore')

    return seed_rows

def _build_region_topology_impl(*, buildings: gpd.GeoDataFrame, streets: gpd.GeoDataFrame, demand_data: pd.DataFrame | gpd.GeoDataFrame | None, config: RegionTopologyConfig) -> tuple[gpd.GeoDataFrame, dict[str, int]]:
    """Build region assignment for street segments from building demand inputs.

    Small-island merge heuristics and disconnected-region diagnostics run on
    street assignments only.
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
    region_seed_rows = _region_seed_builder_impl(buildings=b, streets=s, demand_data=demand_data, **cfg.region_seed_builder_kwargs(caps=caps, keep_internal_columns=True))
    streets_with_region = s.copy().explode(index_parts=False).reset_index(drop=True)
    if cfg.segment_streets_by_building_projections and (not b.empty):
        streets_with_region = _segment_streets_by_building_projections(streets_with_region, b, street_id_column=cfg.street_id_column, projection_buffer_m=float(cfg.segment_projection_buffer_m))
    streets_with_region['_street_key'] = range(len(streets_with_region))
    if '_street_members' not in region_seed_rows.columns:
        raise ValueError("Expected '_street_members' in region seed rows")
    rid_by_key = RegionTopologyBase.owner_map_from_members_column(
        region_seed_rows,
        owner_col=cfg.region_id_column,
        members_col='_street_members',
    )
    demand_keys: set[int] = (
        RegionTopologyBase.member_key_set_from_list_column(
            region_seed_rows,
            members_col='_demand_street_members',
        )
        if '_demand_street_members' in region_seed_rows.columns
        else set()
    )
    street_demand_mwh: dict[int, float] = (
        RegionTopologyBase.sum_street_values_from_dict_column(
            region_seed_rows,
            dict_col='_street_demand_mwh',
        )
        if '_street_demand_mwh' in region_seed_rows.columns
        else {}
    )
    if not street_demand_mwh and ('_demand_street_members' in region_seed_rows.columns) and (cfg.demand_value_column in region_seed_rows.columns):
        for _, prow in region_seed_rows[[cfg.demand_value_column, '_demand_street_members']].iterrows():
            members = prow['_demand_street_members']
            if not isinstance(members, list) or (not members):
                continue
            total = float(prow[cfg.demand_value_column]) if pd.notna(prow[cfg.demand_value_column]) else 0.0
            if total <= 0.0:
                continue
            share = total / float(len(members))
            for sid in members:
                sid_i = int(sid)
                street_demand_mwh[sid_i] = float(street_demand_mwh.get(sid_i, 0.0) + share)
    if (not demand_keys) and street_demand_mwh:
        demand_keys = {int(sid) for sid, dem in street_demand_mwh.items() if float(dem) > 0.0}
    streets_with_region[cfg.region_id_column] = streets_with_region['_street_key'].map(rid_by_key)
    streets_with_region['_is_demand_street'] = streets_with_region['_street_key'].isin(demand_keys)
    if cfg.small_islands:
        streets_with_region = _merge_small_regions_by_street_graph(streets_with_region=streets_with_region, street_demand_mwh=street_demand_mwh, **cfg.small_island_merge_kwargs(enable_final_resort=not bool(cfg.polynesia)))
    streets_with_region = _collapse_overloaded_junction_conflicts(streets_with_region=streets_with_region, region_id_column=cfg.region_id_column, tolerance_m=cfg.connect_tolerance_m)
    mantra = {'raw_street_ids_split_across_regions': int((streets_with_region.dropna(subset=[cfg.region_id_column]).groupby(cfg.street_id_column)[cfg.region_id_column].nunique() > 1).sum()), 'unassigned_street_segments': int(streets_with_region[cfg.region_id_column].isna().sum()), 'cross_region_crossings': 0, 'junction_overload_points': 0, 'disconnected_regions': int(_count_disconnected_regions(streets_with_region=streets_with_region, region_id_column=cfg.region_id_column, tolerance_m=cfg.connect_tolerance_m))}
    return (streets_with_region, mantra)
