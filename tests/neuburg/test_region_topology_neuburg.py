from pathlib import Path

import geopandas as gpd
import pandas as pd

from pypeline.energy_system.region_topology import _iter_intersection_points, build_region_topology, RegionTopologyConfig


def _count_overloaded_junction_multi_region_violations(streets_with_region: gpd.GeoDataFrame, *, tolerance_m: float = 10.0) -> int:
    base = streets_with_region[["_street_key", "geometry", "id"]].copy().dropna(subset=["_street_key", "geometry"])
    if base.empty:
        return 0
    base["_street_key"] = base["_street_key"].astype(int)

    probe = base[["_street_key", "geometry"]].copy()
    if float(tolerance_m) > 0:
        probe["geometry"] = probe.geometry.buffer(float(tolerance_m))

    joined = gpd.sjoin(
        probe[["_street_key", "geometry"]],
        probe[["_street_key", "geometry"]],
        how="inner",
        predicate="intersects",
    )

    pairs: set[tuple[int, int]] = set()
    for _, row in joined.iterrows():
        a = int(row["_street_key_left"])
        b = int(row["_street_key_right"])
        if a == b:
            continue
        if a > b:
            a, b = b, a
        pairs.add((a, b))

    geom_by_sid = {int(r["_street_key"]): r.geometry for _, r in base[["_street_key", "geometry"]].iterrows()}
    rid_by_sid = {
        int(r["_street_key"]): (None if pd.isna(r["id"]) else int(r["id"]))
        for _, r in base[["_street_key", "id"]].iterrows()
    }

    junction_segments: dict[tuple[float, float], set[int]] = {}
    for a, b in pairs:
        ga = geom_by_sid.get(a)
        gb = geom_by_sid.get(b)
        if ga is None or gb is None or ga.is_empty or gb.is_empty:
            continue
        inter = ga.intersection(gb)
        for pt in _iter_intersection_points(inter):
            key = (round(float(pt.x), 3), round(float(pt.y), 3))
            junction_segments.setdefault(key, set()).update({a, b})

    violations = 0
    for _, segs in junction_segments.items():
        if len(segs) < 4:
            continue
        regs = {rid_by_sid.get(int(sid)) for sid in segs if rid_by_sid.get(int(sid)) is not None}
        if len(regs) > 1:
            violations += 1
    return int(violations)


def neuburg_topology_t() -> None:
    """Checks generated Neuburg regions respect multi-region overloaded junction constraints."""
    root = Path(__file__).resolve().parents[2]
    buildings = gpd.read_file(root / "examples/neuburg/buildings_heat_demand.geojson")
    streets = gpd.read_file(root / "examples/neuburg/street_segments_neuburg.geojson")

    demand = pd.DataFrame(
        {
            "building_objectid": buildings["building_objectid"],
            "annual_demand_mwh": pd.to_numeric(buildings["heating:demand[Wh]"], errors="coerce").fillna(0.0) / 1_000_000.0,
        }
    )

        cfg = RegionTopologyConfig(
            max_demand_mwh=30000.0,
            max_street_length_km=15.0,
            demand_share_pct=80.0,
            city_column="gemeindeschluessel",
            city_value=None,
            clip_buffer_m=200.0,
            connect_tolerance_m=10.0,
            polynesia=False,
            small_islands=True,
            small_islands_max_segments=60,
            segment_streets_by_building_projections=True,
            segment_projection_buffer_m=12.0,
            region_id_column="id",
            street_id_column="street_id",
            building_id_column="building_objectid",
            demand_building_column="building_objectid",
            demand_value_column="annual_demand_mwh",
            demand_street_indicator_column="total_heat_demand",
            demand_street_indicator_min=0.0,
        )

        _, streets_with_region, _ = build_region_topology(
            buildings=buildings,
            streets=streets,
            demand_data=demand,
            config=cfg,
        )

    assert int(_count_overloaded_junction_multi_region_violations(streets_with_region, tolerance_m=10.0)) == 0
