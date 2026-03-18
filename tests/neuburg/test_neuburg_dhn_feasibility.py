from __future__ import annotations
from pathlib import Path
import geopandas as gpd
from pypeline.energy_system.dhn import build_inter_dhn_pipes_from_street_segments, calculate_district_heat_grid_cost
from pypeline.energy_system.heating_shares import resolve_fernwaerme_districts


def neuburg_dhn_ok_t() -> None:
    """Checks Fernwärme districts have feasible local and remote DHN connectivity."""
    root = Path(__file__).resolve().parents[2]
    polygon_path = root / "examples" / "neuburg" / "output data" / "polygon_neuburg.geojson"
    street_segments_path = root / "examples" / "neuburg" / "output data" / "street_segments_neuburg.geojson"
    heating_shares_path = root / "examples" / "neuburg" / "input data" / "heating_shares_neuburg.geojson"

    polygons = gpd.read_file(polygon_path)
    street_segments = gpd.read_file(street_segments_path)
    heating_shares = gpd.read_file(heating_shares_path)

    fernwaerme_districts = resolve_fernwaerme_districts(polygons, heating_shares)
    assert fernwaerme_districts, "No Fernwärme-demand districts detected in Neuburg data"

    local_capex = 1_000_000.0
    pipe_capex = 1_000_000.0

    local_feasible: dict[int, bool] = {}
    local_errors: dict[int, str] = {}

    for district_id in fernwaerme_districts:
        try:
            result = calculate_district_heat_grid_cost(
                polygons=polygons,
                district_id=district_id,
                local_pipe_capex_eur_per_km=local_capex,
                street_segments_gdf=street_segments,
                street_length_column="street_length_m",
                region_id_column="id",
            )
            local_feasible[district_id] = float(result.get("min_pipe_km", 0.0)) > 0.0
        except Exception as exc:
            local_feasible[district_id] = False
            local_errors[district_id] = str(exc)

    local_failures = [d for d in fernwaerme_districts if not local_feasible.get(d, False)]
    assert not local_failures, f"Local DHN infeasible for Fernwärme districts: {local_failures}; errors={local_errors}"

    inter_pipes = build_inter_dhn_pipes_from_street_segments(
        polygons=polygons,
        street_segments_gdf=street_segments,
        pipe_capex_eur_per_km=pipe_capex,
        region_id_column="id",
    )

    remote_failures: list[int] = []
    for district_id in fernwaerme_districts:
        peers = [d for d in fernwaerme_districts if d != district_id and local_feasible.get(d, False)]
        has_remote_pipe = any((district_id, peer) in inter_pipes for peer in peers)
        if not has_remote_pipe:
            remote_failures.append(district_id)

    assert not remote_failures, (
        "No feasible inter-district DHN pipe from Fernwärme district to another feasible Fernwärme district: "
        f"{remote_failures}"
    )
