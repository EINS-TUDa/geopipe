import re
import colorsys
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patheffects as pe
from matplotlib import transforms
from matplotlib.patches import Patch, Circle, Arc
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties
import contextily as ctx
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from pypeline.energy_system.energy_system import EnergySystem
from pypeline.energy_system.rule_book import DEFAULT_HEAT_GRID_DEMAND_NAME, HEAT_EXCHANGER_NAMES


class PlotDefaults:
    KIND = "energy"
    NCOLS = 2
    DPI = 300
    STREET_FIGSIZE = (12, 14)
    ES_FIGSIZE = (10, 10)
    REGION_FILL_ALPHA = 0.5
    REGION_FILL_COLOR = "grey"
    REGION_BOUNDARY_LINEWIDTH = 0.8
    PIE_RADIUS_BASE = 0.005
    PIE_RADIUS_AREA_SCALE = 0.02
    PIE_LABEL_MAP_SPAN_SCALE = 0.025
    PIE_LABEL_RADIUS_SCALE = 1.7
    CENTRAL_EXPLODE_SCALE = 0.25
    MIN_SLICE_SHARE = 0.001
    ID_FONT_SIZE = 10.0
    LABEL_FONT_SIZE = 2.0
    PIE_MAX_RADIUS_SCALE = 0.05

@dataclass
class RegionPlotData:
    demand_value: float
    tech_outputs: dict[str, float]

@dataclass
class DonutLayout:
    pie_radius: float
    label_offset: float
    label_edge_offset: float

@dataclass
class YearPlotInputs:
    demand_values_by_region: dict[int, float]
    tech_outputs_by_region: dict[int, dict[str, float]]

@dataclass
class PlotContext:
    region_plot_data: dict[int, RegionPlotData]
    used_labels: set[str]
    color_map: dict[str, Any]
    map_span: float
    area_by_region: dict[int, float]
    max_region_area: float
    has_unsupplied: bool = False

class EnergySystemPlotter:
    _DEFAULT_FIGSIZE = PlotDefaults.ES_FIGSIZE
    _UNSUPPLIED_COLOR = "darkgray"
    _FAMILY_ORDER = {"gas": 0, "oil": 1, "electricity": 2, "biomass": 3, "hydrogen": 4, "coal": 5, "other": 6, "import": 7}
    _COMMODITY_COLORS = {
        "gas": "#c40007",
        "hydrogen": "#43a7ff",
        "oil": "#4f2b0a",
        "biomass": "#0f5310",
        "electricity": "#bfff00",
        "coal": "#020202",
        "other": "#FFC3FE"
    }

    @staticmethod
    def _build_region_id_color_map(region_ids: list[int]) -> dict[int, Any]:
        ids = sorted({int(v) for v in region_ids})
        if not ids:
            return {}
        palette = plt.get_cmap("tab20", len(ids))
        return {rid: palette(idx) for idx, rid in enumerate(ids)}

    @staticmethod
    def _find_pie_center_inside_polygon(
        region_geom: Any,
        *,
        preferred_x: float,
        preferred_y: float,
        pie_radius: float,
        map_span: float,
        other_region_geoms: list[Any] | None = None,
    ) -> tuple[float, float]:
        if region_geom is None or region_geom.is_empty:
            return (preferred_x, preferred_y)

        boundary = region_geom.boundary
        rep = region_geom.representative_point()
        preferred = Point(float(preferred_x), float(preferred_y))
        min_eps = max(1e-6, pie_radius * 0.02)

        other_geoms = [g for g in (other_region_geoms or []) if g is not None and (not g.is_empty)]

        def _valid(pt: Point, clearance: float) -> bool:
            if pt is None or pt.is_empty:
                return False
            if not bool(region_geom.contains(pt)):
                return False
            if not (float(pt.distance(boundary)) > float(clearance)):
                return False
            pie_circle = pt.buffer(float(pie_radius))
            if pie_circle is None or pie_circle.is_empty:
                return False
            if not bool(pie_circle.within(region_geom)):
                return False
            for og in other_geoms:
                if pie_circle.intersects(og):
                    return False
            return True

        clearances = [pie_radius + min_eps]

        bx0, by0, bx1, by1 = region_geom.bounds
        diag = math.hypot(float(bx1 - bx0), float(by1 - by0))
        max_r = max(diag, pie_radius * 8.0, map_span * 0.15)
        step_r = max(pie_radius * 0.5, map_span * 0.0025, 0.5)
        angle_step_deg = 12
        max_ring_steps = 120

        for clearance in clearances:
            if _valid(preferred, clearance):
                return (float(preferred.x), float(preferred.y))
            if _valid(rep, clearance):
                return (float(rep.x), float(rep.y))

            rr = step_r
            ring_steps = 0
            while rr <= max_r and ring_steps < max_ring_steps:
                for deg in range(0, 360, angle_step_deg):
                    theta = math.radians(float(deg))
                    cand = Point(float(preferred.x) + rr * math.cos(theta), float(preferred.y) + rr * math.sin(theta))
                    if _valid(cand, clearance):
                        return (float(cand.x), float(cand.y))
                rr += step_r
                ring_steps += 1

        return (float(preferred.x), float(preferred.y))

    @staticmethod
    def _pie_radius_fits_polygon(
        region_geom: Any,
        *,
        preferred_x: float,
        preferred_y: float,
        pie_radius: float,
        map_span: float,
        other_region_geoms: list[Any] | None = None,
    ) -> bool:
        if region_geom is None or region_geom.is_empty:
            return False
        x, y = EnergySystemPlotter._find_pie_center_inside_polygon(
            region_geom,
            preferred_x=float(preferred_x),
            preferred_y=float(preferred_y),
            pie_radius=float(pie_radius),
            map_span=float(map_span),
            other_region_geoms=other_region_geoms,
        )
        pt = Point(float(x), float(y))
        if not (pt.within(region_geom) or pt.touches(region_geom)):
            return False
        pie_circle = pt.buffer(float(pie_radius))
        if pie_circle is None or pie_circle.is_empty:
            return False
        if not bool(pie_circle.within(region_geom)):
            return False
        for og in [g for g in (other_region_geoms or []) if g is not None and (not g.is_empty)]:
            if pie_circle.intersects(og):
                return False
        boundary = region_geom.boundary
        if boundary is None or boundary.is_empty:
            return True
        eps = max(1e-6, float(pie_radius) * 0.01)
        return float(pt.distance(boundary)) + eps >= float(pie_radius)

    @staticmethod
    def _cap_pie_radius_to_polygon_fit(
        region_geom: Any,
        *,
        preferred_x: float,
        preferred_y: float,
        proposed_radius: float,
        base_radius: float,
        map_span: float,
        other_region_geoms: list[Any] | None = None,
    ) -> float:
        proposed = max(0.0, float(proposed_radius))
        base = max(0.0, float(base_radius))
        if proposed <= base + 1e-9:
            return proposed

        if EnergySystemPlotter._pie_radius_fits_polygon(
            region_geom,
            preferred_x=float(preferred_x),
            preferred_y=float(preferred_y),
            pie_radius=proposed,
            map_span=float(map_span),
            other_region_geoms=other_region_geoms,
        ):
            return proposed

        if not EnergySystemPlotter._pie_radius_fits_polygon(
            region_geom,
            preferred_x=float(preferred_x),
            preferred_y=float(preferred_y),
            pie_radius=base,
            map_span=float(map_span),
            other_region_geoms=other_region_geoms,
        ):
            return base

        lo = base
        hi = proposed
        for _ in range(10):
            mid = 0.5 * (lo + hi)
            if EnergySystemPlotter._pie_radius_fits_polygon(
                region_geom,
                preferred_x=float(preferred_x),
                preferred_y=float(preferred_y),
                pie_radius=mid,
                map_span=float(map_span),
                other_region_geoms=other_region_geoms,
            ):
                lo = mid
            else:
                hi = mid
        return float(lo)

    @staticmethod
    def plot_streets_colored_by_region(
        streets_with_region: gpd.GeoDataFrame,
        *,
        output_path: Path,
        polygons: gpd.GeoDataFrame | None = None,
        region_id_column: str = "id",
        title: str = "Districts by streets",
        caps_legend: dict[str, Any] | None = None,
    ) -> None:
        """Render a compact topology plot for polygon pipeline outputs."""
        s = streets_with_region
        region_ids = [int(v) for v in sorted(s[region_id_column].dropna().unique())]

        fig, ax = plt.subplots(figsize=PlotDefaults.STREET_FIGSIZE)
        if region_ids:
            color_map = EnergySystemPlotter._build_region_id_color_map(region_ids)
            for rid, group in s.dropna(subset=[region_id_column]).groupby(region_id_column):
                rid_int = int(rid)
                merged_lines = group.geometry.union_all()
                gs = gpd.GeoSeries([merged_lines], crs=s.crs)
                gs.plot(ax=ax, color=color_map.get(rid_int, "#666666"), linewidth=2.1, zorder=2)
                gs.plot(ax=ax, color="#202020", linewidth=0.45, zorder=3)
                rp = merged_lines.representative_point()
                txt = ax.text(rp.x, rp.y, str(rid_int), fontsize=8, fontweight="bold", color="black", zorder=4)
                txt.set_path_effects([pe.Stroke(linewidth=2.0, foreground="white"), pe.Normal()])

        unassigned = s[s[region_id_column].isna()]
        if not unassigned.empty:
            unassigned.plot(ax=ax, color="#aaaaaa", linewidth=1.0, zorder=1)

        table_rows: list[int] = []
        length_km_by_region: dict[int, float] = {}
        demand_mwh_by_region: dict[int, float] = {}
        if region_ids:
            length_km_by_region = (
                s.dropna(subset=[region_id_column])
                .assign(_rid=lambda df: df[region_id_column].astype(int), _len_km=lambda df: df.geometry.length / 1000.0)
                .groupby("_rid")["_len_km"]
                .sum()
                .to_dict()
            )
            if polygons is not None and (not polygons.empty):
                p = polygons
                if region_id_column in p.columns:
                    if "annual_demand_mwh" in p.columns:
                        demand_mwh_by_region = (
                            p.dropna(subset=[region_id_column])
                            .assign(_rid=lambda df: df[region_id_column].astype(int), _demand=lambda df: pd.to_numeric(df["annual_demand_mwh"], errors="coerce").fillna(0.0))
                            .groupby("_rid")["_demand"]
                            .sum()
                            .to_dict()
                        )
            table_rows = sorted(set((int(v) for v in region_ids)))[:50]

        has_top_panel = bool(table_rows)
        top_panel_h = 0.0
        if has_top_panel:
            # Reserve figure space above the map for district tables only.
            rows_per_table = min(25, (len(table_rows) + 1) // 2)
            top_panel_h = min(0.42, max(0.20, 0.12 + 0.012 * (rows_per_table + 1)))
            fig.subplots_adjust(top=1.0 - top_panel_h - 0.02)

        if table_rows:
            split_idx = min(25, (len(table_rows) + 1) // 2)
            left_rows = table_rows[:split_idx]
            right_rows = table_rows[split_idx:50]
            table_specs = [
                (left_rows, [0.04, 1.0 - top_panel_h + 0.01, 0.42, top_panel_h - 0.02]),
                (right_rows, [0.54, 1.0 - top_panel_h + 0.01, 0.42, top_panel_h - 0.02]),
            ]

            for rows, rect in table_specs:
                if not rows:
                    continue
                table_ax = fig.add_axes(rect)
                table_ax.axis("off")
                cell_rows = [
                    [
                        f"{int(rid)}",
                        f"{float(demand_mwh_by_region.get(int(rid), 0.0)):.1f}",
                        f"{float(length_km_by_region.get(int(rid), 0.0)):.2f}",
                    ]
                    for rid in rows
                ]
                table = table_ax.table(
                    cellText=cell_rows,
                    colLabels=["ID", "MWh", "KM"],
                    cellLoc="center",
                    colLoc="center",
                    bbox=[0.0, 0.0, 1.0, 1.0],
                )
                table.auto_set_font_size(False)
                table.set_fontsize(7.0)
                for (r, c), cell in table.get_celld().items():
                    cell.set_edgecolor("#bdbdbd")
                    cell.set_linewidth(0.8)
                    if r == 0:
                        cell.set_text_props(fontweight="bold", ha="center", va="center")
                        cell.set_facecolor("#f7f7f7")
                    else:
                        cell.set_text_props(ha="center", va="center")
                for row_idx, rid in enumerate(rows, start=1):
                    id_text = table[(row_idx, 0)].get_text()
                    id_text.set_color(color_map.get(int(rid), "#666666"))
                    id_text.set_fontweight("bold")

        if caps_legend:
            lines = [f"{k}: {v}" for k, v in caps_legend.items() if v is not None]
            if lines:
                ax.text(
                    0.02,
                    0.02,
                    "Topology caps\n" + "\n".join(lines),
                    transform=ax.transAxes,
                    ha="left",
                    va="bottom",
                    fontsize=8,
                    bbox={"facecolor": "white", "alpha": 0.9, "edgecolor": "#bdbdbd", "pad": 4.0},
                )

        ax.set_title(title)
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.set_aspect("equal", adjustable="box")
        if not has_top_panel:
            fig.tight_layout()
        fig.savefig(output_path, dpi=PlotDefaults.DPI)
        plt.close(fig)

    def __init__(self, energy_system: EnergySystem):
        self.energy_system = energy_system

    def _build_plot_context(
        self,
        *,
        demand_name: str | None,
        kind: str,
        demand_values_by_region: dict[int, float] | None,
        tech_outputs_by_region: dict[int, dict[str, float]] | None,
    ) -> PlotContext:
        demand_overrides = demand_values_by_region if demand_values_by_region else None
        output_overrides = tech_outputs_by_region if tech_outputs_by_region else None
        regions = self.energy_system.regions
        aggregate_outputs = self._aggregate_region_outputs

        used_labels: set[str] = set()
        region_plot_data: dict[int, RegionPlotData] = {}
        area_by_region: dict[int, float] = {}
        minx = math.inf
        miny = math.inf
        maxx = -math.inf
        maxy = -math.inf

        for region_idx, region in enumerate(regions):
            region_geom = region.boundary
            demand = region.get_demand(demand_name)

            if demand_overrides and region_idx in demand_overrides:
                demand_value = float(demand_overrides[region_idx])
            else:
                demand_value = float(demand.value)

            if output_overrides and region_idx in output_overrides:
                tech_outputs = {
                    name: val_f
                    for name, value in output_overrides[region_idx].items()
                    if (val_f := float(value)) > 0.0
                }
            else:
                tech_outputs = aggregate_outputs(
                    region=region,
                    demand_commodity_in=demand.demand.commodity_in,
                    kind=kind,
                )

            if tech_outputs:
                used_labels.update(tech_outputs.keys())
            region_plot_data[region_idx] = RegionPlotData(demand_value=demand_value, tech_outputs=tech_outputs)

            bounds = polygon.total_bounds
            minx = min(minx, float(bounds[0]))
            miny = min(miny, float(bounds[1]))
            maxx = max(maxx, float(bounds[2]))
            maxy = max(maxy, float(bounds[3]))
            area_by_region[region_idx] = max(float(region_geom.area), 0.0)

        map_span = max(float(maxx - minx), float(maxy - miny), 1.0)
        max_region_area = max(area_by_region.values())
        color_map = self._build_label_color_map(used_labels) if used_labels else {}
        return PlotContext(
            region_plot_data=region_plot_data,
            used_labels=used_labels,
            color_map=color_map,
            map_span=map_span,
            area_by_region=area_by_region,
            max_region_area=max_region_area,
        )

    def save_optimized_years_grid(
        self,
        optimization_results: dict[str, Any],
        *,
        years: list[int],
        plots_dir: str | Path,
        demand_name: str | None = DEFAULT_HEAT_GRID_DEMAND_NAME,
        kind: str = PlotDefaults.KIND,
        ncols: int = PlotDefaults.NCOLS,
        include_initial: bool = False,
        initial_title: str = "unoptimized",
        dpi: int = PlotDefaults.DPI,
    ) -> Path:
        self.plot_optimized_years_grid(
            optimization_results,
            years=years,
            demand_name=demand_name,
            kind=kind,
            ncols=ncols,
            include_initial=include_initial,
            initial_title=initial_title,
            show=False,
        )

        output_dir = Path(plots_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "technology_mix.png"
        plt.gcf().savefig(output_path, dpi=dpi)
        return output_path

    def save_default_mix_plots(
        self,
        optimization_results: dict[str, Any],
        *,
        years: list[int],
        plots_dir: str | Path,
        demand_name: str | None = DEFAULT_HEAT_GRID_DEMAND_NAME,
        kind: str = PlotDefaults.KIND,
        ncols: int = PlotDefaults.NCOLS,
        include_initial: bool = True,
        dpi: int = PlotDefaults.DPI,
    ) -> dict[str, Path]:
        return {
            "technology": self.save_optimized_years_grid(
                optimization_results,
                years=years,
                plots_dir=plots_dir,
                demand_name=demand_name,
                kind=kind,
                ncols=ncols,
                include_initial=include_initial,
                initial_title="before optimization (technology mix)",
                dpi=dpi,
            )
        }

    @staticmethod
    def _technology_family(label: str, commodity_in: str | None = None) -> str:
        s = str(label or "").lower()
        c = str(commodity_in or "").lower()
        if "import" in s:
            return "import"
        if "heat_pump" in s:
            return "electricity"
        if "hydrogen" in s or "hydrogen" in c:
            return "hydrogen"
        if "gas" in s or c == "gas":
            return "gas"
        if "oil" in s or c == "oil":
            return "oil"
        if any(x in s for x in ("biomass", "wood", "biogas")) or c in {"biomass", "wood", "biogas"}:
            return "biomass"
        if "coal" in s or c == "coal":
            return "coal"
        if "electric" in s or c == "electricity":
            return "electricity"
        return "other"

    def _technology_bucket(self, tech: Any) -> str:
        tech_name = str(getattr(tech, "name", "") or "")
        base = self._base_tech_name(tech_name).lower()
        commodity_in = str(getattr(tech, "commodity_in", "") or "").strip().lower()
        family = self._technology_family(tech_name, commodity_in)
        if (
            base.startswith("pipe_")
            or base.startswith("import_")
            or "import" in base
            or commodity_in.startswith("district_heat_in")
            or commodity_in.startswith("district_heat_import")
            or self._is_heat_exchanger_tech(base)
        ):
            return "import"
        if base.startswith("cen_"):
            return f"cen_{family}"
        if base.startswith("ind_"):
            return f"ind_{family}"
        return tech_name

    @staticmethod
    def _technology_origin(label: str) -> str:
        ll = str(label).lower()
        if ll.startswith("cen_"):
            return "cen"
        if ll == "import" or ll.startswith("import"):
            return "cen"
        if ll.startswith("ind_"):
            return "ind"
        return "other"

    def _order_technology_slices(self, ordered: list[tuple[str, float]]) -> list[tuple[str, float]]:
        if not ordered:
            return ordered
        central_families = {self._technology_family(label).lower() for label, _ in ordered if self._technology_origin(str(label)) == "cen"}
        if len(central_families) == 1:
            target_family = next(iter(central_families))
        elif len(central_families) == 2 and "import" in central_families:
            target_family = next((f for f in central_families if f != "import"), "import")
        else:
            return ordered

        central_block = [item for item in ordered if self._technology_origin(str(item[0])) == "cen" and self._technology_family(str(item[0])).lower() == target_family]
        if "import" in central_families:
            central_block += [
                item for item in ordered
                if self._technology_origin(str(item[0])) == "cen"
                and self._technology_family(str(item[0])).lower() == "import"
            ]
        if not central_block:
            return ordered

        central_labels = {str(label) for label, _ in central_block}
        remainder = [item for item in ordered if str(item[0]) not in central_labels]
        ind_indices = [idx for idx, (label, _) in enumerate(remainder) if self._technology_origin(str(label)) == "ind" and self._technology_family(label).lower() == target_family]
        if not ind_indices:
            return ordered
        cut = max(ind_indices) + 1
        return remainder[:cut] + central_block + remainder[cut:]

    def _build_label_color_map(self, labels: set[str]) -> dict[str, Any]:
        color_map: dict[str, Any] = {}
        fallback = self._COMMODITY_COLORS["other"]
        for label in sorted(labels):
            family = self._technology_family(label).lower()
            base_color = self._COMMODITY_COLORS.get(family, fallback)
            origin = self._technology_origin(str(label))
            color_map[label] = self._shade_origin_color(base_color, origin=origin) if origin in {"cen", "ind"} else base_color
        return color_map

    @staticmethod
    def _shade_origin_color(base_color: Any, *, origin: str) -> tuple[float, float, float]:
        r, g, b = mcolors.to_rgb(base_color)
        if origin == "ind":
            return (r, g, b)
        if origin == "cen":
            h, s, v = colorsys.rgb_to_hsv(r, g, b)
            s = min(1.0, s * 1.2)
            v = max(0.0, min(1.0, v * 0.95))
            return colorsys.hsv_to_rgb(h, s, v)
        return (r, g, b)

    def _draw_glyph_centered_text(
        self,
        ax: Any,
        *,
        x: float,
        y: float,
        text: str,
        fontsize: float,
        fontweight: str,
        ha: str = "center",
        color: str | None = None,
        zorder: float = 4.0,
        bbox: dict[str, Any] | None = None,
    ) -> None:
        path = TextPath((0.0, 0.0), str(text), size=float(fontsize), prop=FontProperties(weight=fontweight))
        extents = path.get_extents()
        shift_points = -0.5 * (float(extents.y0) + float(extents.y1))
        transform = ax.transData + transforms.ScaledTranslation(0.0, shift_points / 72.0, ax.figure.dpi_scale_trans)
        ax.text(
            x,
            y,
            text,
            ha=ha,
            va="baseline",
            transform=transform,
            fontsize=fontsize,
            fontweight=fontweight,
            color=color,
            zorder=zorder,
            bbox=bbox,
        )

    def _highlight_central_wedges(
        self,
        ax: Any,
        *,
        wedges: list[Any],
        labels: list[str],
        sizes: list[float],
        donut: DonutLayout,
        x: float,
        y: float,
    ) -> float:
        origins = [self._technology_origin(str(label)) for label in labels]
        central_indices = [idx for idx, origin in enumerate(origins) if origin == "cen"]
        if not central_indices:
            return 0.0
        central_wedges = [wedges[idx] for idx in central_indices]

        theta1 = min(float(w.theta1) for w in central_wedges)
        theta2 = max(float(w.theta2) for w in central_wedges)
        mid = math.radians((theta1 + theta2) / 2.0)
        shift = donut.pie_radius * PlotDefaults.CENTRAL_EXPLODE_SCALE
        cx = x + shift * math.cos(mid)
        cy = y + shift * math.sin(mid)
        for wedge in central_wedges:
            wedge.set_center((cx, cy))

        n_wedges = len(wedges)
        for idx in central_indices:
            wedge = wedges[idx]
            cx_w, cy_w = wedge.center
            r_out = float(wedge.r)
            width = float(wedge.width) if wedge.width is not None else 0.0
            r_in = max(0.0, r_out - width)
            theta1 = float(wedge.theta1)
            theta2 = float(wedge.theta2)
            ax.add_patch(
                Arc(
                    (cx_w, cy_w),
                    2.0 * r_out,
                    2.0 * r_out,
                    angle=0.0,
                    theta1=theta1,
                    theta2=theta2,
                    color="white",
                    linewidth=0.8,
                    zorder=3.2,
                )
            )
            if r_in > 0.0:
                ax.add_patch(
                    Arc(
                        (cx_w, cy_w),
                        2.0 * r_in,
                        2.0 * r_in,
                        angle=0.0,
                        theta1=theta1,
                        theta2=theta2,
                        color="white",
                        linewidth=0.8,
                        zorder=3.2,
                    )
                )

            prev_is_cen = origins[(idx - 1) % n_wedges] == "cen"
            next_is_cen = origins[(idx + 1) % n_wedges] == "cen"
            if not prev_is_cen:
                a1 = math.radians(theta1)
                ax.plot(
                    [cx_w + r_in * math.cos(a1), cx_w + r_out * math.cos(a1)],
                    [cy_w + r_in * math.sin(a1), cy_w + r_out * math.sin(a1)],
                    color="white",
                    linewidth=0.8,
                    zorder=3.2,
                )
            if not next_is_cen:
                a2 = math.radians(theta2)
                ax.plot(
                    [cx_w + r_in * math.cos(a2), cx_w + r_out * math.cos(a2)],
                    [cy_w + r_in * math.sin(a2), cy_w + r_out * math.sin(a2)],
                    color="white",
                    linewidth=0.8,
                    zorder=3.2,
                )

        central_share = sum(float(sizes[idx]) for idx in central_indices)
        if central_share < PlotDefaults.MIN_SLICE_SHARE:
            return 0.0
        return float(central_share)

    def _aggregate_region_outputs(
        self,
        *,
        region: Any,
        demand_commodity_in: str | None,
        kind: str,
    ) -> dict[str, float]:
        outputs: dict[str, float] = {}
        for r_tech in region.region_technologies:
            if not self._is_visual_supply_tech(r_tech.technology, demand_commodity_in):
                continue
            value = float(r_tech.initial_energy_output) if kind == PlotDefaults.KIND else float(r_tech.initial_capacity)
            if value <= 0:
                continue
            label = self._technology_bucket(r_tech.technology)
            outputs[label] = outputs.get(label, 0.0) + value
        return outputs

    @staticmethod
    def _base_tech_name(name: str) -> str:
        return re.sub(r"_d\d+$", "", str(name), flags=re.IGNORECASE)

    def _is_heat_exchanger_tech(self, tech_name: str) -> bool:
        base = self._base_tech_name(tech_name).lower()
        return base in set(HEAT_EXCHANGER_NAMES) or base.startswith("heatexchanger")

    def _is_visual_supply_tech(self, tech: Any, demand_commodity_in: str | None) -> bool:
        tech_name = str(getattr(tech, "name", "") or "")
        base = self._base_tech_name(tech_name).lower()
        commodity_out = str(getattr(tech, "commodity_out", "") or "").strip().lower()

        if demand_commodity_in is None:
            return False

        demand_out = str(demand_commodity_in).strip().lower()
        if commodity_out == demand_out:
            return True

        if base.startswith("cen_"):
            return True

        if (
            self._is_heat_exchanger_tech(base)
            or base.startswith("pipe_")
            or base.startswith("import_")
            or "import" in base
            or commodity_out.startswith("district_heat_in")
            or commodity_out.startswith("district_heat_out")
            or commodity_out.startswith("district_heat_import")
        ):
            return True

        return False

    def plot(
        self,
        demand_name: str | None = DEFAULT_HEAT_GRID_DEMAND_NAME,
        kind: str = PlotDefaults.KIND,
        *,
        demand_values_by_region: dict[int, float] | None = None,
        tech_outputs_by_region: dict[int, dict[str, float]] | None = None,
        title_suffix: str | None = None,
        ax: Any | None = None,
        show: bool = True,
        block: bool = True,
        draw_basemap: bool = True,
        region_fill_alpha: float = PlotDefaults.REGION_FILL_ALPHA,
        region_fill_color: str = PlotDefaults.REGION_FILL_COLOR,
        draw_region_ids: bool = False,
        region_boundary_linewidth: float = PlotDefaults.REGION_BOUNDARY_LINEWIDTH,
    ) -> None:
        created_new_figure = ax is None
        if ax is None:
            _, ax = plt.subplots(figsize=self._DEFAULT_FIGSIZE)

        context = self._build_plot_context(
            demand_name=demand_name,
            kind=kind,
            demand_values_by_region=demand_values_by_region,
            tech_outputs_by_region=tech_outputs_by_region,
        )
        inv_max_region_area = 1.0 / context.max_region_area
        region_ids = [int(getattr(region, "id_", idx)) for idx, region in enumerate(self.energy_system.regions)]
        region_color_map = self._build_region_id_color_map(region_ids)
        region_geoms = [region.boundary for region in self.energy_system.regions]

        for region_idx, region in enumerate(self.energy_system.regions):
            region_geom = region_geoms[region_idx]
            other_region_geoms = [g for j, g in enumerate(region_geoms) if j != region_idx]
            region_draw = gpd.GeoSeries([region_geom], crs=region.crs)
            region_id = getattr(region, "id_", region_idx)
            region_color = region_color_map.get(int(region_id), "#666666")
            region_draw.boundary.plot(ax=ax, edgecolor="black", linewidth=float(region_boundary_linewidth), zorder=2)
            if float(region_fill_alpha) > 0:
                region_draw.plot(ax=ax, alpha=float(region_fill_alpha), color=region_color, zorder=1)

            if draw_region_ids:
                rp = region_geom.representative_point()
                ax.text(
                    rp.x,
                    rp.y,
                    str(region_id),
                    ha="center",
                    va="center",
                    fontsize=8,
                    zorder=5,
                    bbox={"facecolor": "white", "alpha": 0.7, "edgecolor": "none", "pad": 1.0},
                )

            data = context.region_plot_data[region_idx]
            demand_value = data.demand_value
            tech_outputs = data.tech_outputs
            centroid = region_geom.centroid
            area_norm = context.area_by_region[region_idx] * inv_max_region_area
            base_radius = context.map_span * PlotDefaults.PIE_RADIUS_BASE
            proposed_radius = context.map_span * (PlotDefaults.PIE_RADIUS_BASE + PlotDefaults.PIE_RADIUS_AREA_SCALE * (area_norm ** 0.5))
            pie_radius = self._cap_pie_radius_to_polygon_fit(
                region_geom,
                preferred_x=float(centroid.x),
                preferred_y=float(centroid.y),
                proposed_radius=float(proposed_radius),
                base_radius=float(base_radius),
                map_span=float(context.map_span),
                other_region_geoms=other_region_geoms,
            )
            x, y = self._find_pie_center_inside_polygon(
                region_geom,
                preferred_x=float(centroid.x),
                preferred_y=float(centroid.y),
                pie_radius=float(pie_radius),
                map_span=float(context.map_span),
                other_region_geoms=other_region_geoms,
            )
            demand_offset = max(
                context.map_span * PlotDefaults.PIE_LABEL_MAP_SPAN_SCALE,
                pie_radius * PlotDefaults.PIE_LABEL_RADIUS_SCALE,
            )
            label_offset = demand_offset * PlotDefaults.CENTRAL_EXPLODE_SCALE
            donut = DonutLayout(
                pie_radius=pie_radius,
                label_offset=label_offset,
                label_edge_offset=pie_radius + label_offset,
            )

            total_output = sum(tech_outputs.values())
            ordered: list[tuple[str, float]] = []
            if total_output > 0:
                normalized = {k: v / total_output for k, v in tech_outputs.items() if (v / total_output) >= PlotDefaults.MIN_SLICE_SHARE}
                if normalized:
                    inv_filtered_total = 1.0 / sum(normalized.values())
                    origin_order = {"cen": 0, "ind": 1, "other": 2}
                    ordered = self._order_technology_slices(
                        sorted(
                            ((k, v * inv_filtered_total) for k, v in normalized.items()),
                            key=lambda kv: (
                                origin_order.get(self._technology_origin(str(kv[0])), 99),
                                self._FAMILY_ORDER.get(self._technology_family(str(kv[0])).lower(), 99),
                                str(kv[0]).lower(),
                            ),
                        )
                    )
            if ordered:
                labels = [label for label, _ in ordered]
                sizes = [value for _, value in ordered]
                colors = [context.color_map.get(label, self._UNSUPPLIED_COLOR) for label in labels]
            else:
                labels = []
                sizes = [1.0]
                colors = [self._UNSUPPLIED_COLOR]
                context.has_unsupplied = True

            wedges, _ = ax.pie(
                sizes,
                colors=colors,
                explode=[0.0] * len(sizes),
                radius=donut.pie_radius,
                center=(x, y),
                frame=True,
                textprops={"fontsize": 6},
                wedgeprops={"width": donut.pie_radius / 1.5, "linewidth": 0.0, "edgecolor": "none"},
            )
            for wedge in wedges:
                wedge.set_edgecolor("none")
                wedge.set_linewidth(0.0)
                wedge.set_zorder(3)

            dhn_share = 0.0
            if labels:
                dhn_share = self._highlight_central_wedges(
                    ax,
                    wedges=wedges,
                    labels=labels,
                    sizes=sizes,
                    donut=donut,
                    x=x,
                    y=y,
                )

            ax.add_patch(Circle((x, y), radius=donut.pie_radius / 2.0, facecolor="white", edgecolor="none", zorder=3.5))
            pie_scale = donut.pie_radius / (context.map_span * PlotDefaults.PIE_MAX_RADIUS_SCALE)
            self._draw_glyph_centered_text(ax, x=x, y=y, text=str(region_id), fontsize=PlotDefaults.ID_FONT_SIZE * pie_scale, fontweight="bold", zorder=4.2)

            demand_value_text = f"{demand_value / 1000:.1f} MWh" if kind == PlotDefaults.KIND else f"{demand_value:.1f} kW"
            dhn_percent = max(0.0, min(100.0, float(dhn_share) * 100.0))
            demand_label = rf"$\mathbf{{{demand_value_text}}}\ \mathit{{({dhn_percent:.1f}\%\ DHN)}}$"
            ax.text(
                x,
                y - donut.label_edge_offset,
                demand_label,
                ha="center",
                va="center",
                fontsize=PlotDefaults.LABEL_FONT_SIZE,
                zorder=4,
                bbox={"facecolor": "white", "edgecolor": "black", "linewidth": 1.0, "pad": 1.5},
            )

        if draw_basemap:
            ctx.add_basemap(ax, crs=self.energy_system.regions[0].crs, source=ctx.providers.OpenStreetMap.Mapnik, alpha=0.7, zorder=0)

        merged_legend: dict[str, Any] = {}
        for tech in context.used_labels:
            label = "import" if str(tech).lower() == "import" else self._technology_family(tech).lower()
            if label not in merged_legend:
                merged_legend[label] = self._COMMODITY_COLORS["other"] if label == "import" else self._COMMODITY_COLORS.get(label, self._COMMODITY_COLORS["other"])
        legend_techs = sorted(merged_legend.keys(), key=lambda k: self._FAMILY_ORDER.get(k, 99))
        legend_elements = [Patch(facecolor=merged_legend[label], label=label) for label in legend_techs]
        if demand_name and context.has_unsupplied:
            legend_elements.append(Patch(facecolor=self._UNSUPPLIED_COLOR, label="Unsupplied Demand"))
        if legend_elements:
            ax.legend(handles=legend_elements, loc="upper right", title="Technologies")

        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        title = (
            f"Energy System: {self.energy_system.name} - Demand: {demand_name}"
            if demand_name
            else f"Energy System: {self.energy_system.name}"
        )
        if title_suffix:
            title = f"{title} ({title_suffix})"
        ax.set_title(title)

        if show and created_new_figure:
            plt.show(block=block)

    def _build_optimized_plot_data(
        self,
        optimization_results: dict[str, Any],
        *,
        year: int,
        demand_name: str | None,
        kind: str,
    ) -> YearPlotInputs:
        kpis = optimization_results.get("kpis", {}) if isinstance(optimization_results, dict) else {}
        metric_key = "energy_by_tech_year" if kind == PlotDefaults.KIND else "cap_active_by_tech_year"
        tech_year_map = kpis.get(metric_key, {}) if isinstance(kpis, dict) else {}

        is_visual_supply_tech = self._is_visual_supply_tech
        technology_bucket = self._technology_bucket
        district_suffix = re.compile(r"_D\d+$", re.IGNORECASE)

        def normalize_cp_name(name: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", str(name).lower())

        map_keys = tuple(tech_year_map.keys())
        lower_map = {str(k).lower(): k for k in map_keys}
        norm_map = {normalize_cp_name(str(k)): k for k in map_keys}
        has_district_keys_cache: dict[str, bool] = {}

        def resolve_cp_key(tech_name: str, *, region_idx: int, single_region: bool) -> str | None:
            m = district_suffix.search(tech_name)
            base_name = tech_name[:m.start()] if m else tech_name
            has_district_keys = has_district_keys_cache.get(base_name)
            if has_district_keys is None:
                district_pattern = re.compile(rf"^{re.escape(base_name)}_d\d+$", re.IGNORECASE)
                has_district_keys = any(district_pattern.match(str(k)) for k in map_keys)
                has_district_keys_cache[base_name] = has_district_keys

            candidates = ([f"{base_name}_D{region_idx}"] if not single_region else []) + [tech_name] + ([base_name] if m else [])

            def is_valid(name: str) -> bool:
                return single_region or not has_district_keys or bool(district_suffix.search(name))

            for candidate in dict.fromkeys(candidates):
                if not is_valid(candidate):
                    continue
                for matched in (candidate, lower_map.get(candidate.lower()), norm_map.get(normalize_cp_name(candidate))):
                    if matched and is_valid(str(matched)):
                        return str(matched)
            return None

        year_value_cache: dict[str, float] = {}

        def cached_year_value(cp_key: str) -> float:
            if cp_key in year_value_cache:
                return year_value_cache[cp_key]
            series = tech_year_map.get(cp_key, {})
            value = float(series.get(str(year), series.get(year, 0.0)) or 0.0)
            year_value_cache[cp_key] = value
            return value

        demand_values_by_region: dict[int, float] = {}
        tech_outputs_by_region: dict[int, dict[str, float]] = {}
        regions = self.energy_system.regions
        single_region = len(regions) == 1

        for idx, region in enumerate(regions):
            demand_cp = "HeatDemand" if single_region else f"HeatDemand_D{idx}"
            demand_values_by_region[idx] = cached_year_value(demand_cp)
            demand = region.get_demand(demand_name) if demand_name else None
            demand_commodity_in = demand.demand.commodity_in if demand else None

            per_region: dict[str, float] = {}
            for r_tech in region.region_technologies:
                tech_name = r_tech.technology.name
                if not is_visual_supply_tech(r_tech.technology, demand_commodity_in):
                    continue

                cp_key = resolve_cp_key(tech_name, region_idx=idx, single_region=single_region)
                if cp_key is None:
                    continue

                value = cached_year_value(cp_key)
                if value > 0:
                    label = technology_bucket(r_tech.technology)
                    per_region[label] = per_region.get(label, 0.0) + value
            tech_outputs_by_region[idx] = per_region

        return YearPlotInputs(
            demand_values_by_region=demand_values_by_region,
            tech_outputs_by_region=tech_outputs_by_region,
        )

    def plot_optimized_years_grid(
        self,
        optimization_results: dict[str, Any],
        *,
        years: list[int],
        demand_name: str | None = DEFAULT_HEAT_GRID_DEMAND_NAME,
        kind: str = PlotDefaults.KIND,
        ncols: int = PlotDefaults.NCOLS,
        include_initial: bool = False,
        initial_title: str = "unoptimized",
        show: bool = True,
        block: bool = True,
    ) -> None:
        total_panels = len(years) + (1 if include_initial else 0)
        nrows = (total_panels + ncols - 1) // ncols
        fig, axes = plt.subplots(
            nrows,
            ncols,
            figsize=(self._DEFAULT_FIGSIZE[0] * ncols, self._DEFAULT_FIGSIZE[1] * nrows),
            squeeze=False,
        )
        axes_flat = axes.ravel()

        offset = 0
        if include_initial:
            self.plot(
                demand_name=demand_name,
                kind=kind,
                title_suffix=initial_title,
                ax=axes_flat[0],
                show=False,
            )
            offset = 1

        for idx, year in enumerate(years):
            plot_inputs = self._build_optimized_plot_data(
                optimization_results,
                year=year,
                demand_name=demand_name,
                kind=kind,
            )
            self.plot(
                demand_name=demand_name,
                kind=kind,
                demand_values_by_region=plot_inputs.demand_values_by_region,
                tech_outputs_by_region=plot_inputs.tech_outputs_by_region,
                title_suffix=f"optimized {year}",
                ax=axes_flat[idx + offset],
                show=False,
            )

        for idx in range(total_panels, len(axes_flat)):
            axes_flat[idx].axis("off")

        fig.tight_layout()
        if show:
            plt.show(block=block)
