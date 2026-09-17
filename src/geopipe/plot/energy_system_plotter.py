# coding=utf-8
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from shapely.geometry import MultiPoint

from ..energy_system.region import Region
from ..topology_builder.topology import REGION_ID
from .solution_plotter import (_add_basemap, _draw_pipe_path, _save_figure, _show_or_close,
                               _zoom_to_regions)


_INACTIVE_EDGE_COLOR = "#787a7d"
_PIPE_COLOR = "#eac282"  # brown

# Summary-table figure layout, in inches
_TABLE_ROW_HEIGHT_IN = 0.3
_TABLE_TITLE_HEIGHT_IN = 0.5
_TABLE_COL_WIDTH_IN = 2.0
_TABLE_MARGIN_IN = 0.4
_TABLE_GAP_IN = 0.6


def plot_system_topology(energy_system, output_path: Optional[str | Path] = None,
                         show: bool = True) -> None:
    """
    Plot the system_topology of an energy system.

    Each region's edges are coloured uniquely. Region IDs are drawn on top of
    each region's representative point. A separate figure holds summary tables
    listing the total demand value and grid length per region and the length of
    each pipe; its size adapts to the number of regions and pipes.

    Parameters
    ----------
    energy_system
        The EnergySystem whose ``system_topology`` should be plotted.
    output_path
        Optional path. When given, the map is saved to that location and the
        summary tables next to it as ``<stem>_summary<suffix>``.
    show
        When ``True`` (default) both figures are opened in windows and execution
        blocks until they are closed. Pass ``False`` in scripts that only need
        the files written to ``output_path``.
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
    _draw_pipes(ax, energy_system)
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

    _save_figure(fig, output_path)

    summary_path = None
    if output_path is not None:
        path = Path(output_path)
        summary_path = path.with_name(f"{path.stem}_summary{path.suffix}")
    summary_fig = _plot_summary_tables(energy_system, regions, color_map=color_map,
                                       output_path=summary_path)

    _show_or_close(fig, summary_fig, show=show)


def _build_region_color_map(region_ids: list[int]) -> dict[int, Any]:
    palette = plt.get_cmap("tab20", max(len(region_ids), 1))
    return {rid: palette(idx) for idx, rid in enumerate(region_ids)}


def _draw_topology_edges_by_region(ax, system_topology, *, color_map: dict[int, Any]) -> None:
    graph = system_topology.graph if hasattr(system_topology, "graph") else system_topology
    for u, v, data in graph.edges(data=True):
        raw = data.get(REGION_ID)
        try:
            rid = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            rid = None
        is_active = rid is not None and rid in color_map
        color = color_map[rid] if is_active else _INACTIVE_EDGE_COLOR
        ax.plot(
            [float(u[0]), float(v[0])],
            [float(u[1]), float(v[1])],
            color=color,
            linewidth=4 if is_active else 2,
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


def _draw_pipes(ax, energy_system) -> None:
    pipes = getattr(energy_system, "pipes", None) or []
    drawn: set[tuple[int, int]] = set()
    for pipe in pipes:
        a = int(pipe.region_id_in)
        b = int(pipe.region_id_out)
        canonical = (a, b) if a <= b else (b, a)
        if canonical in drawn:
            continue
        _draw_pipe_path(
            ax, pipe, energy_system,
            color=_PIPE_COLOR, linestyle="-", linewidth=4.0,
            with_arrow=False, zorder=3,
        )
        drawn.add(canonical)


def _plot_summary_tables(
    energy_system,
    regions: Iterable[Region],
    *,
    color_map: dict[int, Any],
    output_path: Optional[Path],
) -> plt.Figure:
    region_labels, region_rows = _region_summary_rows(regions, energy_system)
    tables = [("Regions", region_labels, region_rows)]
    pipe_rows = _pipes_summary_rows(energy_system)
    if pipe_rows:
        tables.append(("Pipes", ["Pipe", "Regions", "Length [km]"], pipe_rows))

    heights = [_TABLE_TITLE_HEIGHT_IN + (len(rows) + 1) * _TABLE_ROW_HEIGHT_IN for _, _, rows in tables]
    widths = [len(labels) * _TABLE_COL_WIDTH_IN for _, labels, _ in tables]
    fig_width = sum(widths) + (len(tables) - 1) * _TABLE_GAP_IN + 2 * _TABLE_MARGIN_IN
    fig_height = max(heights) + 2 * _TABLE_MARGIN_IN

    fig = plt.figure(figsize=(fig_width, fig_height))
    fig.suptitle(f"System summary — {energy_system.name}", fontsize=14)
    top = fig_height - _TABLE_MARGIN_IN
    left = _TABLE_MARGIN_IN
    for (title, labels, rows), height, width in zip(tables, heights, widths):
        table_height = height - _TABLE_TITLE_HEIGHT_IN
        ax = fig.add_axes((
            left / fig_width,
            (top - height) / fig_height,
            width / fig_width,
            table_height / fig_height,
        ))
        ax.axis("off")
        ax.set_title(title, fontsize=12, fontweight="bold")
        table = _draw_table(ax, labels, rows)
        if title == "Regions":
            for row_idx, row in enumerate(rows, start=1):
                id_text = table[(row_idx, 0)].get_text()
                id_text.set_color(color_map.get(int(row[0]), "black"))
                id_text.set_fontweight("bold")
        left += width + _TABLE_GAP_IN

    _save_figure(fig, output_path)
    return fig


def _draw_table(ax, col_labels: list[str], rows: list[list[str]]):
    table = ax.table(
        cellText=rows,
        colLabels=col_labels,
        cellLoc="center",
        colLoc="center",
        bbox=(0.0, 0.0, 1.0, 1.0),
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    for (r, _c), cell in table.get_celld().items():
        cell.set_edgecolor("#bdbdbd")
        cell.set_linewidth(0.8)
        if r == 0:
            cell.set_text_props(fontweight="bold", ha="center", va="center")
            cell.set_facecolor("#f7f7f7")
        else:
            cell.set_text_props(ha="center", va="center")
    return table


def _region_summary_rows(regions: Iterable[Region], energy_system) -> tuple[list[str], list[list[str]]]:
    units = getattr(energy_system, "units", None)
    energy_unit = getattr(units, "energy", "")
    unit_suffix = f" [{energy_unit}]" if energy_unit else ""

    sorted_regions = sorted(regions, key=lambda r: int(r.id))

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
    return col_labels, rows


def _pipes_summary_rows(energy_system) -> list[list[str]]:
    pipes = getattr(energy_system, "pipes", None) or []
    unique_pipes: dict[tuple[str, int, int], float] = {}
    for pipe in pipes:
        a = int(pipe.region_id_in)
        b = int(pipe.region_id_out)
        lo, hi = (a, b) if a <= b else (b, a)
        unique_pipes.setdefault((str(pipe.name), lo, hi), pipe.pipe_length_km)
    return [
        [name, f"{lo} ↔ {hi}", f"{length_km:.2f}"]
        for (name, lo, hi), length_km in sorted(unique_pipes.items())
    ]