# coding=utf-8
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from shapely.geometry import MultiPoint

from ..energy_system.region import Region
from .solution_plotter import _add_basemap, _save_figure, _zoom_to_regions


_INACTIVE_EDGE_COLOR = "#cccccc"
_PIPE_COLOR = "#8b4513"  # brown


def plot_system_topology(energy_system, output_path: Optional[str | Path] = None) -> None:
    """
    Plot the system_topology of an energy system.

    Each region's edges are coloured uniquely. Region IDs are drawn on top of
    each region's representative point. A summary table beneath the map lists
    the total demand value and total grid length per region.

    Parameters
    ----------
    energy_system
        The EnergySystem whose ``system_topology`` should be plotted.
    output_path
        Optional path. When given, the figure is saved to that location.
    """
    if energy_system is None:
        raise ValueError("energy_system must not be None")
    if energy_system.system_topology is None:
        raise ValueError("energy_system.system_topology is None — nothing to plot")

    regions = list(energy_system.regions)
    if not regions:
        raise ValueError("energy_system has no regions to plot")

    region_ids = sorted({int(r.id) for r in regions})
    color_map = _build_region_color_map(region_ids)

    region_anchors = _region_anchor_points(regions)

    fig, ax = plt.subplots(figsize=(14, 14))
    _draw_topology_edges_by_region(ax, energy_system.system_topology, color_map=color_map)
    _draw_pipes(ax, energy_system, region_anchors=region_anchors)
    _draw_region_id_labels(ax, regions, anchors=region_anchors)

    legend_handles = [
        mpatches.Patch(color=color_map[rid], label=f"Region {rid}") for rid in region_ids
    ]
    if getattr(energy_system, "pipes", None):
        legend_handles.append(mpatches.Patch(color=_PIPE_COLOR, label="pipe"))
    ax.legend(
        handles=legend_handles,
        loc="upper right",
        fontsize=10,
        title="Regions",
        title_fontsize=11,
    )

    ax.set_aspect("equal", adjustable="box")
    _zoom_to_regions(ax, regions)
    _add_basemap(ax, energy_system)
    ax.set_title(f"System topology — {energy_system.name}", fontsize=16)
    ax.set_xlabel("x", fontsize=14)
    ax.set_ylabel("y", fontsize=14)
    ax.tick_params(axis="both", labelsize=12)

    fig.subplots_adjust(bottom=0.22)
    _add_region_summary_table(fig, regions, energy_system=energy_system, color_map=color_map)

    _save_figure(fig, output_path)
    plt.show()


def _build_region_color_map(region_ids: list[int]) -> dict[int, Any]:
    palette = plt.get_cmap("tab20", max(len(region_ids), 1))
    return {rid: palette(idx) for idx, rid in enumerate(region_ids)}


def _draw_topology_edges_by_region(ax, system_topology, *, color_map: dict[int, Any]) -> None:
    graph = system_topology.graph if hasattr(system_topology, "graph") else system_topology
    for u, v, data in graph.edges(data=True):
        raw = data.get("id")
        try:
            rid = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            rid = None
        color = color_map.get(rid, _INACTIVE_EDGE_COLOR) if rid is not None else _INACTIVE_EDGE_COLOR
        ax.plot(
            [float(u[0]), float(v[0])],
            [float(u[1]), float(v[1])],
            color=color,
            linewidth=1.4,
            zorder=2,
        )


def _region_anchor_points(regions: Iterable[Region]) -> dict[int, tuple[float, float]]:
    anchors: dict[int, tuple[float, float]] = {}
    for region in regions:
        topology = region.topology
        if topology is None:
            continue
        nodes = list(topology.graph.nodes)
        if not nodes:
            continue
        rep = MultiPoint([(float(n[0]), float(n[1])) for n in nodes]).representative_point()
        anchors[int(region.id)] = (float(rep.x), float(rep.y))
    return anchors


def _draw_region_id_labels(ax, regions: Iterable[Region], *, anchors: dict[int, tuple[float, float]]) -> None:
    for region in regions:
        rid = int(region.id)
        if rid not in anchors:
            continue
        x, y = anchors[rid]
        txt = ax.text(
            x,
            y,
            str(rid),
            ha="center",
            va="center",
            fontsize=14,
            fontweight="bold",
            color="black",
            zorder=5,
        )
        txt.set_path_effects([pe.Stroke(linewidth=3.0, foreground="white"), pe.Normal()])


def _draw_pipes(ax, energy_system, *, region_anchors: dict[int, tuple[float, float]]) -> None:
    pipes = getattr(energy_system, "pipes", None) or []
    drawn: set[tuple[int, int]] = set()
    for pipe in pipes:
        a = int(pipe.region_id_in)
        b = int(pipe.region_id_out)
        if a not in region_anchors or b not in region_anchors:
            continue
        canonical = (a, b) if a <= b else (b, a)
        if canonical in drawn:
            continue
        xa, ya = region_anchors[a]
        xb, yb = region_anchors[b]
        ax.plot(
            [xa, xb],
            [ya, yb],
            color=_PIPE_COLOR,
            linewidth=2.0,
            zorder=3,
        )
        drawn.add(canonical)


def _add_region_summary_table(
    fig,
    regions: Iterable[Region],
    *,
    energy_system,
    color_map: dict[int, Any],
) -> None:
    units = getattr(energy_system, "units", None)
    energy_unit = getattr(units, "energy", "")
    unit_suffix = f" [{energy_unit}]" if energy_unit else ""

    sorted_regions = sorted(regions, key=lambda r: int(r.id))
    if not sorted_regions:
        return

    demand_names: list[str] = []
    seen: set[str] = set()
    for region in sorted_regions:
        for demand in region.demands:
            if demand.name not in seen:
                seen.add(demand.name)
                demand_names.append(demand.name)

    rows: list[list[str]] = []
    for region in sorted_regions:
        rid = int(region.id)
        demand_by_name = {d.name: float(d.value(0)) for d in region.demands}
        grid_length_km = (
            region.topology.total_edge_length / 1000.0 if region.topology is not None else 0.0
        )
        row = [f"{rid}", f"{grid_length_km:.2f}"]
        for name in demand_names:
            row.append(f"{demand_by_name[name]:.1f}" if name in demand_by_name else "—")
        rows.append(row)

    col_labels = ["Region", "Grid length [km]"] + [f"{name}{unit_suffix}" for name in demand_names]

    table_ax = fig.add_axes((0.05, 0.04, 0.9, 0.16))
    table_ax.axis("off")
    table = table_ax.table(
        cellText=rows,
        colLabels=col_labels,
        cellLoc="center",
        colLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1.0, 1.4)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.8)
        if r == 0:
            cell.set_text_props(fontweight="bold", ha="center", va="center")
            cell.set_facecolor("#f7f7f7")
        else:
            cell.set_text_props(ha="center", va="center")
    for row_idx, region in enumerate(sorted_regions, start=1):
        rid = int(region.id)
        id_text = table[(row_idx, 0)].get_text()
        id_text.set_color(color_map.get(rid, "black"))
        id_text.set_fontweight("bold")