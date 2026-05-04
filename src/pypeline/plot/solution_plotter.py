# coding=utf-8
from __future__ import annotations

from typing import Any, Iterable

import contextily as ctx
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import pandas as pd

from ..energy_system.region import Region
from ..energy_system.technology import GridTechnology

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..optimization.solver import Solution



_INACTIVE_COLOR = "#cccccc"
_PIPE_COLOR = "#8b4513"  # brown


def plot_grid(solution: "Solution", grid_name: str, year: int) -> None:
    """
    Plot the energy_system._system_topology on a map. Color the edges of region where grid_name has active capacity.
    Each region should be colored differently.
    Color pipes with active capacity connecting regions in brown. The direction of the pipe/energy flow should be indicated by an arrow.
    Regions and pipes without active capacity can be colored in a light gray.
    In regions with active capacity for central technologies, add a text box with the name of the technology and its active capacity.
    Each region with demands supplied by this grid should have a text box with the name of the demand and the amount of energy supplied by the grid.

    """
    if solution.energy_system is None or solution.results is None:
        raise ValueError("solution must have both energy_system and results set")
    es = solution.energy_system
    results = solution.results
    if es.system_topology is None:
        raise ValueError("energy_system.system_topology is None — nothing to plot")

    grid_tech = _find_grid(es.regions, grid_name)
    grid_commodity_in = grid_tech.commodity_in
    demand_commodity = grid_tech.commodity_out

    grid_slice = _slice_df(
        results.grids_per_commodity_in.get(grid_commodity_in, pd.DataFrame()),
        year=year, technology=grid_name,
    )
    active_regions: set[int] = {
        int(row["region_id"]) for _, row in grid_slice.iterrows() if float(row["capacity"]) > 0
    }
    grid_energy_by_region: dict[int, float] = {
        int(row["region_id"]): float(row["energy_output"]) for _, row in grid_slice.iterrows()
    }

    central_slice = _slice_df(
        results.central_technologies_per_commodity_out.get(grid_commodity_in, pd.DataFrame()),
        year=year,
    )
    central_by_region: dict[int, list[tuple[str, float]]] = {}
    for _, row in central_slice.iterrows():
        cap = float(row["capacity"])
        if cap > 0:
            central_by_region.setdefault(int(row["region_id"]), []).append((str(row["technology"]), cap))

    pipes_slice = _slice_df(
        results.pipes_per_commodity_out.get(grid_commodity_in, pd.DataFrame()),
        year=year,
    )
    active_pipes: list[tuple[int, int, float]] = [
        (int(row["region_id_from"]), int(row["region_id_to"]), float(row["capacity"]))
        for _, row in pipes_slice.iterrows() if float(row["capacity"]) > 0
    ]

    region_color_map = _build_color_map(sorted(active_regions))
    unit = results.unit

    decentral_supplied = _decentral_supplied_per_region(es, results, demand_commodity, year)
    demand_values = _demand_values_per_region(es, demand_commodity, solution.scenario, year)
    grid_length_per_region = _grid_length_per_region(es, grid_name)

    fig, ax = plt.subplots(figsize=(12, 12))
    _draw_system_edges(ax, es.system_topology, active_regions=active_regions, color_map=region_color_map)
    _draw_pipes(ax, es, active_pipes=active_pipes, unit_power=unit.power)
    _draw_region_text_boxes(
        ax,
        regions=es.regions,
        active_regions=active_regions,
        central_by_region=central_by_region,
        grid_energy_by_region=grid_energy_by_region,
        decentral_supplied=decentral_supplied,
        demand_values=demand_values,
        grid_length_per_region=grid_length_per_region,
        unit_power=unit.power,
        unit_energy=unit.energy,
    )

    legend_handles = [mpatches.Patch(color=color, label=f"region {rid}") for rid, color in region_color_map.items()]
    legend_handles.append(mpatches.Patch(color=_INACTIVE_COLOR, label="inactive"))
    legend_handles.append(mpatches.Patch(color=_PIPE_COLOR, label="active pipe"))
    ax.legend(handles=legend_handles, loc="upper right", fontsize=8)

    ax.set_aspect("equal", adjustable="box")
    _zoom_to_regions(ax, es.regions)
    _add_basemap(ax, es)
    ax.set_title(f"Grid '{grid_name}' — {year}")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    fig.tight_layout()
    plt.show()


def _zoom_to_regions(ax, regions: Iterable[Region], *, margin_frac: float = 0.05) -> None:
    bounds = [region.boundary.bounds for region in regions]
    if not bounds:
        return
    minx = min(b[0] for b in bounds)
    miny = min(b[1] for b in bounds)
    maxx = max(b[2] for b in bounds)
    maxy = max(b[3] for b in bounds)
    span = max(maxx - minx, maxy - miny, 1.0)
    pad = span * margin_frac
    ax.set_xlim(minx - pad, maxx + pad)
    ax.set_ylim(miny - pad, maxy + pad)


def _find_grid(regions: Iterable[Region], grid_name: str) -> GridTechnology:
    for region in regions:
        for grid in region.grids:
            if grid.name == grid_name:
                return grid
    raise ValueError(f"Grid '{grid_name}' not found in any region")


def _slice_df(df: pd.DataFrame, *, year: int, technology: str | None = None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    mask = df["year"] == year
    if technology is not None:
        mask &= df["technology"] == technology
    return df[mask]


def _build_color_map(region_ids: list[int]) -> dict[int, tuple]:
    if not region_ids:
        return {}
    palette = plt.get_cmap("tab20", max(len(region_ids), 1))
    return {rid: palette(idx) for idx, rid in enumerate(region_ids)}


def _system_graph(system_topology):
    return system_topology.graph if hasattr(system_topology, "graph") else system_topology


def _edge_region_id(edge_data: dict) -> int | None:
    raw = edge_data.get("id")
    if raw is None:
        return None
    try:
        if pd.isna(raw):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _draw_system_edges(ax, system_topology, *, active_regions: set[int], color_map: dict[int, tuple]) -> None:
    graph = _system_graph(system_topology)
    for u, v, data in graph.edges(data=True):
        rid = _edge_region_id(data)
        is_active = rid is not None and rid in active_regions
        color = color_map.get(rid, _INACTIVE_COLOR) if is_active else _INACTIVE_COLOR
        ax.plot(
            [float(u[0]), float(v[0])],
            [float(u[1]), float(v[1])],
            color=color,
            linewidth=1.2 if is_active else 0.8,
            zorder=2 if is_active else 1,
        )


def _region_centroids(regions: Iterable[Region]) -> dict[int, tuple[float, float]]:
    centroids: dict[int, tuple[float, float]] = {}
    for region in regions:
        c = region.boundary.centroid
        centroids[int(region.id)] = (float(c.x), float(c.y))
    return centroids


def _draw_pipes(
    ax,
    energy_system,
    *,
    active_pipes: list[tuple[int, int, float]],
    unit_power: str,
) -> None:
    centroids = _region_centroids(energy_system.regions)
    active_pairs: set[tuple[int, int]] = {(a, b) for a, b, _ in active_pipes}
    pipe_by_pair: dict[tuple[int, int], Any] = {
        (int(p.region_id_in), int(p.region_id_out)): p for p in energy_system.pipes
    }

    drawn_inactive: set[tuple[int, int]] = set()
    for pipe in energy_system.pipes:
        a, b = int(pipe.region_id_in), int(pipe.region_id_out)
        if (a, b) in active_pairs:
            continue
        canonical: tuple[int, int] = (a, b) if a <= b else (b, a)
        if canonical in drawn_inactive:
            continue
        if a not in centroids or b not in centroids:
            continue
        xa, ya = centroids[a]
        xb, yb = centroids[b]
        ax.plot([xa, xb], [ya, yb], color=_INACTIVE_COLOR, linewidth=1.0, linestyle="--", zorder=2)
        drawn_inactive.add(canonical)

    for region_in, region_out, capacity in active_pipes:
        if region_in not in centroids or region_out not in centroids:
            continue
        xa, ya = centroids[region_in]
        xb, yb = centroids[region_out]
        ax.annotate(
            "",
            xy=(xb, yb),
            xytext=(xa, ya),
            arrowprops={"arrowstyle": "->", "color": _PIPE_COLOR, "lw": 2.0, "shrinkA": 10, "shrinkB": 10},
            zorder=4,
        )
        pipe = pipe_by_pair.get((region_in, region_out))
        if pipe is None:
            continue
        ax.text(
            (xa + xb) / 2.0,
            (ya + yb) / 2.0,
            f"{pipe.name}\n{pipe.pipe_length_km:.2f} km\n{capacity:.2f} {unit_power}",
            ha="center",
            va="center",
            fontsize=7,
            color=_PIPE_COLOR,
            zorder=5,
            bbox={"facecolor": "white", "edgecolor": _PIPE_COLOR, "alpha": 0.9, "boxstyle": "round,pad=0.3"},
        )


def _draw_region_text_boxes(
    ax,
    *,
    regions: Iterable[Region],
    active_regions: set[int],
    central_by_region: dict[int, list[tuple[str, float]]],
    grid_energy_by_region: dict[int, float],
    decentral_supplied: dict[int, dict[str, float]],
    demand_values: dict[int, dict[str, float]],
    grid_length_per_region: dict[int, float],
    unit_power: str,
    unit_energy: str,
) -> None:
    for region in regions:
        rid = int(region.id)
        if rid not in active_regions:
            continue
        lines: list[str] = [f"Region {rid}"]
        length_km = grid_length_per_region.get(rid)
        if length_km is not None:
            lines.append(f"Grid length: {length_km:.2f} km")
        demands = demand_values.get(rid, {})
        if demands:
            lines.append("Demand:")
            grid_energy = grid_energy_by_region.get(rid)
            for name, val in sorted(demands.items()):
                supplied = (
                    f", from grid: {grid_energy:.2f} {unit_energy}"
                    if grid_energy is not None
                    else ""
                )
                lines.append(f"  {name}: {val:.2f} {unit_energy}{supplied}")
        centrals = sorted(central_by_region.get(rid, []), key=lambda kv: kv[0])
        if centrals:
            lines.append("Central:")
            for tech_name, capacity in centrals:
                lines.append(f"  {tech_name}: {capacity:.2f} {unit_power}")
        decentrals = decentral_supplied.get(rid, {})
        if decentrals:
            lines.append("Decentral (from grid):")
            for tech_name, capacity in sorted(decentrals.items()):
                lines.append(f"  {tech_name}: {capacity:.2f} {unit_power}")
        if len(lines) == 1:
            continue
        c = region.boundary.centroid
        ax.text(
            float(c.x),
            float(c.y),
            "\n".join(lines),
            ha="center",
            va="center",
            fontsize=8,
            zorder=5,
            bbox={"facecolor": "white", "edgecolor": "black", "alpha": 0.85, "boxstyle": "round,pad=0.4"},
        )


def _grid_length_per_region(energy_system, grid_name: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for region in energy_system.regions:
        for grid in region.grids:
            if grid.name == grid_name:
                out[int(region.id)] = float(grid.length_km)
                break
    return out


def _decentral_supplied_per_region(
    energy_system,
    results,
    demand_commodity: str,
    year: int,
) -> dict[int, dict[str, float]]:
    grid_supplied_names_per_region: dict[int, set[str]] = {
        int(r.id): {t.name for t in r.decentral_techs if t.commodity_in == demand_commodity}
        for r in energy_system.regions
    }
    out: dict[int, dict[str, float]] = {}
    for df in results.decentral_technologies_per_demand.values():
        sliced = _slice_df(df, year=year)
        for _, row in sliced.iterrows():
            rid = int(row["region_id"])
            tech = str(row["technology"])
            if tech not in grid_supplied_names_per_region.get(rid, set()):
                continue
            cap = float(row["capacity"])
            if cap <= 0:
                continue
            region_caps = out.setdefault(rid, {})
            region_caps[tech] = region_caps.get(tech, 0.0) + cap
    return out


def _demand_values_per_region(
    energy_system,
    demand_commodity: str,
    scenario,
    year: int,
) -> dict[int, dict[str, float]]:
    if scenario is None:
        return {}
    year_period = max(0, int(year) - int(scenario.start_year))
    out: dict[int, dict[str, float]] = {}
    for region in energy_system.regions:
        rid = int(region.id)
        for demand in region.demands:
            if demand.demand_type.commodity_in != demand_commodity:
                continue
            out.setdefault(rid, {})[demand.name] = float(demand.value(year_period))
    return out


def _add_basemap(ax, energy_system) -> None:
    if not energy_system.regions:
        return
    crs = energy_system.regions[0].crs
    if crs is None:
        return
    try:
        ctx.add_basemap(ax, crs=crs, source=ctx.providers.OpenStreetMap.Mapnik, alpha=0.7, zorder=0)
    except Exception:
        # Tile fetching can fail (offline, rate-limited); plotting should still succeed.
        pass