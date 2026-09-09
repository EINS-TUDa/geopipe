# coding=utf-8
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

import math

import contextily as ctx
import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd
from matplotlib.patches import Circle
from shapely import voronoi_polygons
from shapely.geometry import MultiPoint
from shapely.ops import unary_union

from ..energy_system.region import Region
from ..energy_system.technology import GridTechnology

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..optimization.solver import Solution



_INACTIVE_COLOR = "#787a7d"
_PIPE_COLOR = "#eac282"  # brown
_REGION_FILL_COLOR = "#656870"

_ACTIVE_THRESHOLD = 1e-3  # values <= this are considered inactive for plotting purposes

_DECENTRAL_METRIC_COLUMNS: dict[str, str] = {
    "energy_output": "energy_output",
    "new_capacity": "new_capacity",
    "active_capacity": "capacity",
}
# (display title, unit kind on Unit dataclass: "energy" or "power")
_DECENTRAL_METRIC_DISPLAY: dict[str, tuple[str, str]] = {
    "energy_output": ("Energy output", "energy"),
    "new_capacity": ("New capacity", "power"),
    "active_capacity": ("Active capacity", "power"),
}

_GRID_METRIC_COLUMNS: dict[str, str] = {
    "capacity": "capacity",
    "energy_output": "energy_output",
}
_GRID_METRIC_DISPLAY: dict[str, tuple[str, str]] = {
    "capacity": ("Active capacity", "power"),
    "energy_output": ("Energy output", "energy"),
}


def plot_grid(
    solution: "Solution",
    grid_name: str,
    year: int,
    metric: str = "capacity",
    output_path: Optional[str | Path] = None,
) -> None:
    """
    Plot the energy_system._system_topology on a map for ``grid_name`` in ``year``.

    The numerical annotations (active filter, region text boxes, pipe labels) are
    driven by ``metric``:

    - ``"capacity"`` — active capacity of grids/centrals/decentrals/pipes (power unit).
    - ``"energy_output"`` — total yearly energy output (energy unit).

    Regions and pipes whose chosen metric is zero are drawn in light grey; active
    ones are coloured per region (edges) and brown (pipes).
    """
    if solution.energy_system is None or solution.results is None:
        raise ValueError("solution must have both energy_system and results set")
    if metric not in _GRID_METRIC_COLUMNS:
        raise ValueError(
            f"Unknown metric '{metric}'. Choose from {sorted(_GRID_METRIC_COLUMNS)}."
        )
    es = solution.energy_system
    results = solution.results
    if es.system_topology is None:
        raise ValueError("energy_system.system_topology is None — nothing to plot")

    column = _GRID_METRIC_COLUMNS[metric]
    metric_label, unit_kind = _GRID_METRIC_DISPLAY[metric]
    unit = results.unit
    metric_unit = unit.power if unit_kind == "power" else unit.energy

    grid_tech = _find_grid(es.regions, grid_name)
    grid_commodity_in = grid_tech.commodity_in
    demand_commodity = grid_tech.commodity_out

    grid_slice = _slice_df(
        results.grids_per_commodity_in.get(grid_commodity_in, pd.DataFrame()),
        year=year, technology=grid_name,
    )
    active_regions: set[int] = {
        int(row["region_id"]) for _, row in grid_slice.iterrows() if float(row[column]) > _ACTIVE_THRESHOLD
    }
    grid_value_by_region: dict[int, float] = {
        int(row["region_id"]): float(row[column]) for _, row in grid_slice.iterrows()
    }

    central_slice = _slice_df(
        results.central_technologies_per_commodity_out.get(grid_commodity_in, pd.DataFrame()),
        year=year,
    )
    central_by_region: dict[int, list[tuple[str, float]]] = {}
    for _, row in central_slice.iterrows():
        val = float(row[column])
        if val > _ACTIVE_THRESHOLD:
            central_by_region.setdefault(int(row["region_id"]), []).append((str(row["technology"]), val))

    pipes_slice = _slice_df(
        results.pipes_per_commodity_out.get(grid_commodity_in, pd.DataFrame()),
        year=year,
    )
    active_pipes: list[tuple[int, int, float]] = [
        (int(row["region_id_from"]), int(row["region_id_to"]), float(row[column]))
        for _, row in pipes_slice.iterrows() if float(row[column]) > _ACTIVE_THRESHOLD
    ]

    region_color_map = _build_color_map(sorted(active_regions))

    decentral_supplied = _decentral_supplied_per_region(
        es, results, demand_commodity, year, column=column,
    )
    demand_values = _demand_values_per_region(es, demand_commodity, solution.scenario, year)
    grid_length_per_region = _grid_length_per_region(es, grid_name)

    fig, ax = plt.subplots(figsize=(12, 12))
    _draw_system_edges(ax, es.system_topology, active_regions=active_regions, color_map=region_color_map)
    _draw_pipes(ax, es, active_pipes=active_pipes, value_unit=metric_unit)
    _draw_region_text_boxes(
        ax,
        regions=es.regions,
        active_regions=active_regions,
        central_by_region=central_by_region,
        grid_value_by_region=grid_value_by_region,
        decentral_supplied=decentral_supplied,
        demand_values=demand_values,
        grid_length_per_region=grid_length_per_region,
        metric_label=metric_label,
        metric_unit=metric_unit,
        unit_energy=unit.energy,
    )

    legend_handles = [mpatches.Patch(color=color, label=f"region {rid}") for rid, color in region_color_map.items()]
    legend_handles.append(mpatches.Patch(color=_INACTIVE_COLOR, label="inactive"))
    legend_handles.append(mpatches.Patch(color=_PIPE_COLOR, label=f"active pipe ({metric_label.lower()})"))
    ax.legend(handles=legend_handles, loc="upper right", fontsize=8)

    ax.set_aspect("equal", adjustable="box")
    _zoom_to_regions(ax, es.regions)
    _add_basemap(ax, es)
    ax.set_title(f"Grid '{grid_name}' — {year} — {metric_label}")
    ax.set_xlabel("x")
    ax.set_ylabel("y")
    fig.tight_layout()
    _save_figure(fig, output_path)
    plt.show()


def plot_decentral_shares(
    solution: "Solution",
    demand_name: str,
    year: int | list[int],
    metric: str = "energy_output",
    technology_style: dict[str, dict[str, Any]] | None = None,
    output_path: Optional[str | Path] = None,
) -> None:
    """
    Plot a donut chart per region showing the share of decentral technologies that
    supply ``demand_name``. Each region is drawn as a Voronoi cell built from its
    topology nodes.

    Parameters
    ----------
    solution
        Solution containing energy_system and results.
    demand_name
        Key into ``results.decentral_technologies_per_demand``.
    year
        A single year (``int``) renders one figure; a list of years renders a
        subplot grid with two columns and as many rows as needed.
    metric
        One of ``"energy_output"`` (eouttot), ``"new_capacity"`` or
        ``"active_capacity"``. Selects which column of the results dataframe is
        aggregated into the donut shares.
    technology_style
        Optional mapping ``{decentral_technology.name: {"color": ..., "label": ...}}``.
        Missing entries fall back to a tab20 palette colour and the technology
        name as legend label.
    """
    if solution.energy_system is None or solution.results is None:
        raise ValueError("solution must have both energy_system and results set")
    if metric not in _DECENTRAL_METRIC_COLUMNS:
        raise ValueError(
            f"Unknown metric '{metric}'. Choose from {sorted(_DECENTRAL_METRIC_COLUMNS)}."
        )

    es = solution.energy_system
    results = solution.results

    df = results.decentral_technologies_per_demand.get(demand_name)
    if df is None:
        raise ValueError(
            f"Demand '{demand_name}' not found in results.decentral_technologies_per_demand"
        )

    years = [int(year)] if isinstance(year, int) else [int(y) for y in year]
    if not years:
        raise ValueError("`year` must be an int or a non-empty list of ints")

    column = _DECENTRAL_METRIC_COLUMNS[metric]
    shares_per_year: dict[int, dict[int, dict[str, float]]] = {
        y: _decentral_shares_per_region(_slice_df(df, year=y), column) for y in years
    }

    region_geoms = _region_polygons(es.regions)
    combined_shares: dict[int, dict[str, float]] = {}
    for shares_per_region in shares_per_year.values():
        for rid, shares in shares_per_region.items():
            combined_shares.setdefault(rid, {}).update(shares)
    style_lookup = _resolve_technology_style(combined_shares, technology_style)

    metric_title, unit_kind = _DECENTRAL_METRIC_DISPLAY[metric]
    unit_str = results.unit.energy if unit_kind == "energy" else results.unit.power

    ncols = 1 if len(years) == 1 else 2
    nrows = (len(years) + ncols - 1) // ncols
    fig, axes = plt.subplots(
        nrows, ncols, figsize=(12 * ncols, 12 * nrows), squeeze=False,
    )
    axes_flat = axes.ravel().tolist()

    for ax, y in zip(axes_flat, years):
        _draw_decentral_shares_axis(
            ax,
            energy_system=es,
            region_geoms=region_geoms,
            shares_per_region=shares_per_year[y],
            style_lookup=style_lookup,
            metric_title=metric_title,
            unit_str=unit_str,
            demand_name=demand_name,
            year=y,
        )
    for ax in axes_flat[len(years):]:
        ax.axis("off")

    used_techs = sorted({tech for shares in combined_shares.values() for tech in shares})
    legend_handles = [
        mpatches.Patch(color=style_lookup[tech]["color"], label=style_lookup[tech]["label"])
        for tech in used_techs
    ]
    if legend_handles:
        fig.legend(
            handles=legend_handles,
            loc="upper right",
            fontsize=14,
            title="Decentral technologies",
            title_fontsize=15,
        )

    fig.tight_layout()
    _save_figure(fig, output_path)
    plt.show()


def _draw_decentral_shares_axis(
    ax,
    *,
    energy_system,
    region_geoms: list[Any],
    shares_per_region: dict[int, dict[str, float]],
    style_lookup: dict[str, dict[str, Any]],
    metric_title: str,
    unit_str: str,
    demand_name: str,
    year: int,
) -> None:
    _draw_region_polygons(ax, energy_system.regions, region_geoms)
    pie_radius, pie_centers = _draw_region_donuts(
        ax,
        regions=energy_system.regions,
        region_geoms=region_geoms,
        shares_per_region=shares_per_region,
        style_lookup=style_lookup,
        metric_title=metric_title,
        unit_str=unit_str,
    )
    ax.set_aspect("equal", adjustable="box")
    _zoom_to_donuts(ax, energy_system.regions, pie_centers, pie_radius)
    _add_basemap(ax, energy_system)
    ax.set_title(f"Decentral shares — {metric_title} — '{demand_name}' — {year}", fontsize=16)
    ax.set_xlabel("x", fontsize=14)
    ax.set_ylabel("y", fontsize=14)
    ax.tick_params(axis="both", labelsize=12)


def _decentral_shares_per_region(sliced: pd.DataFrame, column: str) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    if sliced is None or sliced.empty:
        return out
    if column not in sliced.columns:
        raise ValueError(f"Column '{column}' missing from decentral results dataframe")
    for _, row in sliced.iterrows():
        value = float(row[column])
        if value <= 0:
            continue
        rid = int(row["region_id"])
        tech = str(row["technology"])
        region_map = out.setdefault(rid, {})
        region_map[tech] = region_map.get(tech, 0.0) + value
    return out


def _region_polygons(regions: Iterable[Region]) -> list[Any]:
    """
    Build one polygon per region by Voronoi-tessellating *all* topology nodes
    (each tagged with its region) and dissolving the cells per region.

    The result hugs each region's actual node footprint — like a convex hull
    bent along the perpendicular bisector where two regions meet — and is
    non-overlapping by construction.
    """
    region_list = list(regions)
    if not region_list:
        raise ValueError("Energy system has no regions for region polygons")

    seeds: list[tuple[float, float]] = []
    seed_region_idx: list[int] = []
    region_hulls: list[Any] = []
    for idx, region in enumerate(region_list):
        nodes = list(region.topology.graph.nodes) if region.topology is not None else []
        if not nodes:
            raise ValueError(f"Region {region.id} has no topology nodes")
        points = [(float(n[0]), float(n[1])) for n in nodes]
        seeds.extend(points)
        seed_region_idx.extend([idx] * len(points))
        region_hulls.append(MultiPoint(points).convex_hull)

    # Voronoi requires unique seeds; drop duplicates (shared boundary nodes).
    seen: set[tuple[float, float]] = set()
    unique_seeds: list[tuple[float, float]] = []
    unique_region_idx: list[int] = []
    for pt, ridx in zip(seeds, seed_region_idx):
        key = (round(pt[0], 9), round(pt[1], 9))
        if key in seen:
            continue
        seen.add(key)
        unique_seeds.append(pt)
        unique_region_idx.append(ridx)

    envelope = unary_union(region_hulls)
    minx, miny, maxx, maxy = envelope.bounds
    map_span = max(float(maxx - minx), float(maxy - miny), 1.0)
    clip_geom = envelope.buffer(map_span * 0.05)

    cells = voronoi_polygons(MultiPoint(unique_seeds), extend_to=clip_geom, ordered=True)
    cell_list = list(getattr(cells, "geoms", []))
    if len(cell_list) != len(unique_seeds):
        raise ValueError("Voronoi output cell count does not match seed count")

    cells_per_region: list[list[Any]] = [[] for _ in region_list]
    for cell, ridx in zip(cell_list, unique_region_idx):
        cells_per_region[ridx].append(cell)

    geoms: list[Any] = []
    for region, cells_for_region in zip(region_list, cells_per_region):
        if not cells_for_region:
            raise ValueError(f"Region {region.id} produced no Voronoi cells")
        merged = unary_union(cells_for_region).intersection(clip_geom).buffer(0)
        if merged is None or merged.is_empty:
            raise ValueError(f"Region polygon is empty for region {region.id}")
        geoms.append(merged)
    return geoms


def _resolve_technology_style(
    shares_per_region: dict[int, dict[str, float]],
    technology_style: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    used = sorted({tech for shares in shares_per_region.values() for tech in shares})
    palette = plt.get_cmap("tab20", max(len(used), 1))
    out: dict[str, dict[str, Any]] = {}
    for idx, tech in enumerate(used):
        provided = (technology_style or {}).get(tech, {})
        out[tech] = {
            "color": provided.get("color", palette(idx)),
            "label": provided.get("label", tech),
        }
    return out


def _draw_region_polygons(ax, regions: Iterable[Region], region_geoms: list[Any]) -> None:
    crs = next((r.crs for r in regions if r.crs is not None), None)
    gpd.GeoSeries(region_geoms, crs=crs).plot(
        ax=ax,
        facecolor=_REGION_FILL_COLOR,
        edgecolor="black",
        linewidth=0.8,
        alpha=0.5,
        zorder=1,
    )


def _draw_region_donuts(
    ax,
    *,
    regions: Iterable[Region],
    region_geoms: list[Any],
    shares_per_region: dict[int, dict[str, float]],
    style_lookup: dict[str, dict[str, Any]],
    metric_title: str,
    unit_str: str,
) -> tuple[float, list[tuple[float, float]]]:
    region_list = list(regions)
    # Size pies relative to the topology footprint (not the cell extent), so a
    # small region in a large map doesn't get an oversized donut.
    boundary_bounds = [r.boundary.bounds for r in region_list]
    minx = min(b[0] for b in boundary_bounds)
    miny = min(b[1] for b in boundary_bounds)
    maxx = max(b[2] for b in boundary_bounds)
    maxy = max(b[3] for b in boundary_bounds)
    map_span = max(maxx - minx, maxy - miny, 1.0)
    pie_radius = map_span * 0.05

    centers: list[tuple[float, float]] = []
    for region, geom in zip(region_list, region_geoms):
        rid = int(region.id)
        # Place pies on the topology centroid so they sit over the actual region.
        c = region.boundary.centroid
        cx, cy = float(c.x), float(c.y)
        centers.append((cx, cy))

        shares = shares_per_region.get(rid, {})
        total = sum(shares.values())
        if total <= 0:
            ax.text(
                cx,
                cy,
                f"Region {rid}\nno {metric_title.lower()}",
                ha="center",
                va="center",
                fontsize=14,
                zorder=4,
                bbox={"facecolor": "white", "edgecolor": "black", "alpha": 0.85, "boxstyle": "round,pad=0.3"},
            )
            continue

        labels = sorted(shares.keys())
        sizes = [shares[t] for t in labels]
        colors = [style_lookup[t]["color"] for t in labels]
        wedges, _ = ax.pie(
            sizes,
            colors=colors,
            radius=pie_radius,
            center=(cx, cy),
            frame=True,
            wedgeprops={"width": pie_radius * 0.5, "linewidth": 0.5, "edgecolor": "white"},
        )
        for w in wedges:
            w.set_zorder(3)

        ax.add_patch(Circle((cx, cy), radius=pie_radius * 0.5, facecolor="white", edgecolor="none", zorder=3.5))
        ax.text(
            cx,
            cy,
            f"{rid}\n{total:.1f} {unit_str}",
            ha="center",
            va="center",
            fontsize=14,
            fontweight="bold",
            zorder=4,
        )

    return pie_radius, centers


def _zoom_to_donuts(
    ax,
    regions: Iterable[Region],
    pie_centers: list[tuple[float, float]],
    pie_radius: float,
    *,
    margin_frac: float = 0.05,
) -> None:
    bounds = [region.boundary.bounds for region in regions]
    if not bounds:
        return
    minx = min(b[0] for b in bounds)
    miny = min(b[1] for b in bounds)
    maxx = max(b[2] for b in bounds)
    maxy = max(b[3] for b in bounds)
    if pie_centers:
        minx = min(minx, min(cx - pie_radius for cx, _ in pie_centers))
        maxx = max(maxx, max(cx + pie_radius for cx, _ in pie_centers))
        miny = min(miny, min(cy - pie_radius for _, cy in pie_centers))
        maxy = max(maxy, max(cy + pie_radius for _, cy in pie_centers))
    span = max(maxx - minx, maxy - miny, 1.0)
    pad = span * margin_frac
    ax.set_xlim(minx - pad, maxx + pad)
    ax.set_ylim(miny - pad, maxy + pad)


def _save_figure(fig, output_path: Optional[str | Path]) -> None:
    if output_path is None:
        return
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=300, bbox_inches="tight")


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
            linewidth=4 if is_active else 2,
            zorder=2 if is_active else 1,
        )


def _draw_pipes(
    ax,
    energy_system,
    *,
    active_pipes: list[tuple[int, int, float]],
    value_unit: str,
) -> None:
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
        _draw_pipe_path(
            ax, pipe, energy_system,
            color=_INACTIVE_COLOR, linestyle="--", linewidth=1.0,
            with_arrow=False, zorder=2,
        )
        drawn_inactive.add(canonical)

    for region_in, region_out, value in active_pipes:
        pipe = pipe_by_pair.get((region_in, region_out))
        if pipe is None:
            continue
        ordered = _draw_pipe_path(
            ax, pipe, energy_system,
            color=_PIPE_COLOR, linestyle="-", linewidth=2.0,
            with_arrow=True, zorder=4,
        )
        label_xy = _path_midpoint(ordered)
        if label_xy is None:
            continue
        ax.text(
            label_xy[0],
            label_xy[1],
            f"{pipe.name}\n{pipe.pipe_length_km:.2f} km\n{value:.2f} {value_unit}",
            ha="center",
            va="center",
            fontsize=7,
            color=_PIPE_COLOR,
            zorder=5,
            bbox={"facecolor": "white", "edgecolor": _PIPE_COLOR, "alpha": 0.9, "boxstyle": "round,pad=0.3"},
        )


def _ordered_pipe_path(pipe, energy_system) -> list[tuple]:
    """Return nodes of pipe.topology ordered from region_id_in towards region_id_out.

    Falls back to the topology's nodes in arbitrary order if a clean ordering
    cannot be determined. Touching pipes (no edges) keep their node list as is.
    """
    topology = getattr(pipe, "topology", None)
    if topology is None:
        return []
    nodes = list(topology.nodes())
    if not nodes or topology.number_of_edges() == 0:
        return nodes

    region_in = next((r for r in energy_system.regions if int(r.id) == int(pipe.region_id_in)), None)
    region_out = next((r for r in energy_system.regions if int(r.id) == int(pipe.region_id_out)), None)
    if (region_in is None or region_out is None
            or region_in.topology is None or region_out.topology is None):
        return nodes

    in_nodes = set(region_in.topology.graph.nodes)
    out_nodes = set(region_out.topology.graph.nodes)
    start_candidates = [n for n in nodes if n in in_nodes]
    end_candidates = [n for n in nodes if n in out_nodes]
    if not start_candidates or not end_candidates:
        return nodes

    try:
        return nx.shortest_path(topology, source=start_candidates[0], target=end_candidates[0])
    except nx.NetworkXNoPath:
        return nodes


def _draw_pipe_path(
    ax,
    pipe,
    energy_system,
    *,
    color,
    linestyle: str = "-",
    linewidth: float = 2.0,
    with_arrow: bool,
    zorder: int,
) -> list[tuple]:
    """Draw a single pipe along its actual topology.

    Returns the ordered list of nodes that was drawn (useful for placing labels).
    For touching regions (topology has no edges), highlights the touching
    node(s) with a marker so the connection remains visible.
    """
    topology = getattr(pipe, "topology", None)
    if topology is None or topology.number_of_nodes() == 0:
        return []

    nodes = list(topology.nodes())
    if topology.number_of_edges() == 0:
        for node in nodes:
            ax.plot(
                float(node[0]),
                float(node[1]),
                marker="o",
                markersize=10,
                markerfacecolor=color,
                markeredgecolor="white",
                markeredgewidth=1.5,
                linestyle="none",
                zorder=zorder + 1,
            )
        return nodes

    for u, v in topology.edges():
        ax.plot(
            [float(u[0]), float(v[0])],
            [float(u[1]), float(v[1])],
            color=color,
            linewidth=linewidth,
            linestyle=linestyle,
            zorder=zorder,
        )

    ordered = _ordered_pipe_path(pipe, energy_system)
    if with_arrow and len(ordered) >= 2:
        u = ordered[-2]
        v = ordered[-1]
        ax.annotate(
            "",
            xy=(float(v[0]), float(v[1])),
            xytext=(float(u[0]), float(u[1])),
            arrowprops={"arrowstyle": "->", "color": color, "lw": linewidth, "shrinkA": 0, "shrinkB": 0},
            zorder=zorder + 1,
        )
    return ordered


def _path_midpoint(nodes) -> tuple[float, float] | None:
    """Geometric midpoint along a polyline expressed as a list of (x, y) nodes."""
    if not nodes:
        return None
    if len(nodes) == 1:
        return float(nodes[0][0]), float(nodes[0][1])
    cum = [0.0]
    for i in range(len(nodes) - 1):
        a = nodes[i]
        b = nodes[i + 1]
        cum.append(cum[-1] + math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1])))
    total = cum[-1]
    if total <= 0:
        return float(nodes[0][0]), float(nodes[0][1])
    half = total / 2.0
    for i in range(1, len(cum)):
        if cum[i] >= half:
            t = (half - cum[i - 1]) / (cum[i] - cum[i - 1])
            a = nodes[i - 1]
            b = nodes[i]
            return (
                float(a[0]) + t * (float(b[0]) - float(a[0])),
                float(a[1]) + t * (float(b[1]) - float(a[1])),
            )
    return float(nodes[-1][0]), float(nodes[-1][1])


def _draw_region_text_boxes(
    ax,
    *,
    regions: Iterable[Region],
    active_regions: set[int],
    central_by_region: dict[int, list[tuple[str, float]]],
    grid_value_by_region: dict[int, float],
    decentral_supplied: dict[int, dict[str, float]],
    demand_values: dict[int, dict[str, float]],
    grid_length_per_region: dict[int, float],
    metric_label: str,
    metric_unit: str,
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
            for name, val in sorted(demands.items()):
                lines.append(f"  {name}: {val:.2f} {unit_energy}")
        grid_value = grid_value_by_region.get(rid)
        if grid_value is not None:
            lines.append(f"Grid {metric_label.lower()}: {grid_value:.2f} {metric_unit}")
        centrals = sorted(central_by_region.get(rid, []), key=lambda kv: kv[0])
        if centrals:
            lines.append("Central:")
            for tech_name, value in centrals:
                lines.append(f"  {tech_name}: {value:.2f} {metric_unit}")
        decentrals = decentral_supplied.get(rid, {})
        if decentrals:
            lines.append("Decentral (from grid):")
            for tech_name, value in sorted(decentrals.items()):
                lines.append(f"  {tech_name}: {value:.2f} {metric_unit}")
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
    *,
    column: str = "capacity",
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
            value = float(row[column])
            if value <= 0:
                continue
            region_values = out.setdefault(rid, {})
            region_values[tech] = region_values.get(tech, 0.0) + value
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
        ctx.add_basemap(
            ax,
            crs=crs,
            source=ctx.providers.OpenStreetMap.Mapnik,
            headers={"User-Agent": "geopipe"},
            alpha=0.7,
            zorder=0,
        )
    except Exception:
        # Tile fetching can fail (offline, rate-limited); plotting should still succeed.
        pass