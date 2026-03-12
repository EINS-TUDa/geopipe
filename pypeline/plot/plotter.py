import re
import hashlib
from pathlib import Path
from typing import Any
import matplotlib.cm as cm
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.patheffects as pe
from matplotlib.patches import Patch, FancyBboxPatch
from matplotlib.lines import Line2D
from shapely.geometry import Point
import contextily as ctx
import geopandas as gpd
import pandas as pd

from pypeline.energy_system.energy_system import EnergySystem
from pypeline.energy_system.rule_book import DEFAULT_HEAT_GRID_DEMAND_NAME, HEAT_EXCHANGER_NAMES


def plot_streets_colored_by_region(
    streets_with_region: gpd.GeoDataFrame,
    polygons: gpd.GeoDataFrame,
    *,
    output_path: Path,
    region_id_column: str = "id",
    title: str = "Districts by streets",
    caps_legend: dict[str, Any] | None = None,
) -> None:
    """Render a quick topology plot for polygon pipeline outputs.

    Colors street segments by assigned region, computes adjacency-aware colors to
    reduce clashes between touching regions, and labels regions. Writes a PNG.
    """
    s = streets_with_region.copy()

    region_ids = [int(rid) for rid in sorted(s[region_id_column].dropna().unique())]

    rg = polygons[[region_id_column, "geometry"]].copy()
    rg = rg.dropna(subset=[region_id_column]).copy()
    rg[region_id_column] = rg[region_id_column].astype(int)
    rg = rg[rg[region_id_column].isin(region_ids)].copy()
    if rg.crs != s.crs:
        rg = rg.to_crs(s.crs)
    adjacency: dict[int, set[int]] = {int(rid): set() for rid in region_ids}
    if not rg.empty:
        j = gpd.sjoin(
            rg[[region_id_column, "geometry"]],
            rg[[region_id_column, "geometry"]],
            how="inner",
            predicate="intersects",
        )
        for _, row in j.iterrows():
            a = int(row[f"{region_id_column}_left"])
            b = int(row[f"{region_id_column}_right"])
            if a == b:
                continue
            adjacency.setdefault(a, set()).add(b)
            adjacency.setdefault(b, set()).add(a)

    palette: list[tuple[float, float, float, float]] = []
    for h_idx in range(36):
        h = float(h_idx) / 36.0
        for s_val, v_val in ((0.95, 0.95), (0.80, 0.95), (0.95, 0.78)):
            r, g, b = mcolors.hsv_to_rgb((h, s_val, v_val))
            palette.append((float(r), float(g), float(b), 1.0))
    n_colors = len(palette)

    def _rgb_distance(c1: tuple[float, float, float, float], c2: tuple[float, float, float, float]) -> float:
        return (
            ((c1[0] - c2[0]) ** 2)
            + ((c1[1] - c2[1]) ** 2)
            + ((c1[2] - c2[2]) ** 2)
        ) ** 0.5

    color_idx_by_region: dict[int, int] = {}
    used_global: set[int] = set()
    uncolored = set(region_ids)

    def _neighbor_colors(rid: int) -> set[int]:
        return {
            color_idx_by_region[n]
            for n in adjacency.get(rid, set())
            if n in color_idx_by_region
        }

    def _pick_next_region() -> int:
        return max(
            uncolored,
            key=lambda rid: (len(_neighbor_colors(rid)), len(adjacency.get(rid, set())), -rid),
        )

    while uncolored:
        rid = _pick_next_region()
        neighbor_color_idxs = list(_neighbor_colors(rid))
        assigned_color_idxs = list(color_idx_by_region.values())

        best_idx = 0
        best_key: tuple[float, float, int, int] | None = None

        candidate_idxs = [ci for ci in range(n_colors) if ci not in set(neighbor_color_idxs)]
        if not candidate_idxs:
            candidate_idxs = list(range(n_colors))

        preferred = [ci for ci in candidate_idxs if ci not in used_global]
        if preferred:
            candidate_idxs = preferred

        for ci in candidate_idxs:
            if neighbor_color_idxs:
                min_neighbor_dist = min(_rgb_distance(palette[ci], palette[nci]) for nci in neighbor_color_idxs)
            else:
                min_neighbor_dist = 1.0

            if assigned_color_idxs:
                min_global_dist = min(_rgb_distance(palette[ci], palette[aci]) for aci in assigned_color_idxs)
            else:
                min_global_dist = 1.0

            key = (min_neighbor_dist, min_global_dist, -(ci in used_global), -ci)
            if best_key is None or key > best_key:
                best_key = key
                best_idx = ci

        color_idx_by_region[rid] = best_idx
        used_global.add(best_idx)
        uncolored.remove(rid)

    color_map: dict[int, tuple[float, float, float, float]] = {
        int(rid): palette[color_idx_by_region[rid]] for rid in region_ids
    }

    demand_col = "annual_demand_mwh"
    street_len_col = "street_length_m"

    fig, ax = plt.subplots(figsize=(12, 14))
    for rid, group in s.dropna(subset=[region_id_column]).groupby(region_id_column):
        color = color_map.get(int(rid), "#666666")

        merged_lines = group.geometry.union_all()
        gpd.GeoSeries([merged_lines], crs=s.crs).plot(ax=ax, color=color, linewidth=2.2, zorder=2)
        gpd.GeoSeries([merged_lines], crs=s.crs).plot(ax=ax, color="#202020", linewidth=0.5, zorder=3)

        rp = merged_lines.representative_point()
        txt = ax.text(
            rp.x,
            rp.y,
            str(int(rid)),
            fontsize=8,
            fontweight="bold",
            color="black",
            zorder=4,
        )
        txt.set_path_effects([
            pe.Stroke(linewidth=2.2, foreground="white"),
            pe.Normal(),
        ])

    unassigned = s[s[region_id_column].isna()]
    if not unassigned.empty:
        unassigned.plot(ax=ax, color="#aaaaaa", linewidth=1.0, zorder=1)

    if (
        region_id_column in polygons.columns
        and demand_col in polygons.columns
        and street_len_col in polygons.columns
    ):
        top_n = 30
        stats = polygons[[region_id_column, demand_col, street_len_col]].copy()
        stats[demand_col] = pd.to_numeric(stats[demand_col], errors="coerce").fillna(0.0)
        stats[street_len_col] = pd.to_numeric(stats[street_len_col], errors="coerce").fillna(0.0)
        stats = stats.sort_values(region_id_column, ascending=True).head(top_n).reset_index(drop=True)

        entries: list[tuple[int, float, float]] = []
        for _, row in stats.iterrows():
            rid = int(row[region_id_column])
            demand_mwh = float(row[demand_col])
            street_km = float(row[street_len_col]) / 1000.0
            entries.append((rid, demand_mwh, street_km))

        if entries:
            chunk = 9
            cols = [entries[i:i + chunk] for i in range(0, len(entries), chunk)]
            x_pos = [0.02, 0.355, 0.69]
            y_top = 0.975
            dy = 0.0155
            panel_w = 0.285
            for ci, col_entries in enumerate(cols[:3]):
                n_rows = len(col_entries)
                panel_h = dy * (n_rows + 1.7)
                left = x_pos[ci] - 0.006
                top = y_top + 0.002
                bottom = top - panel_h
                panel = FancyBboxPatch(
                    (left, bottom),
                    panel_w,
                    panel_h,
                    boxstyle="round,pad=0.006",
                    transform=fig.transFigure,
                    facecolor="white",
                    edgecolor="#bdbdbd",
                    linewidth=0.9,
                    alpha=0.94,
                    zorder=1,
                )
                fig.add_artist(panel)

                x0 = left + 0.008
                x3 = left + panel_w - 0.008
                id_w = 0.055
                demand_w = 0.155
                x1 = x0 + id_w
                x2 = x1 + demand_w
                header_y = top - dy

                for xv in (x1, x2):
                    fig.add_artist(Line2D([xv, xv], [bottom + 0.004, top - 0.004], transform=fig.transFigure, color="#d0d0d0", linewidth=0.8, zorder=2))
                fig.add_artist(Line2D([x0, x3], [header_y, header_y], transform=fig.transFigure, color="#c6c6c6", linewidth=0.9, zorder=2))
                for ri in range(1, n_rows + 1):
                    y_line = header_y - (ri * dy)
                    fig.add_artist(Line2D([x0, x3], [y_line, y_line], transform=fig.transFigure, color="#e5e5e5", linewidth=0.6, zorder=2))

                fig.text((x0 + x1) / 2, top - (dy * 0.45), "id", ha="center", va="center", fontsize=8.4, family="monospace", fontweight="bold", color="#111111", zorder=3)
                fig.text((x1 + x2) / 2, top - (dy * 0.45), "demand(MWh)", ha="center", va="center", fontsize=8.4, family="monospace", fontweight="bold", color="#111111", zorder=3)
                fig.text((x2 + x3) / 2, top - (dy * 0.45), "km", ha="center", va="center", fontsize=8.4, family="monospace", fontweight="bold", color="#111111", zorder=3)
                for li, (rid, demand_mwh, street_km) in enumerate(col_entries):
                    y = header_y - ((li + 0.5) * dy)
                    fig.text((x0 + x1) / 2, y, f"{rid}", ha="center", va="center", fontsize=8.6, family="monospace", fontweight="bold", color=color_map.get(int(rid), "#333333"), zorder=3)
                    fig.text((x1 + x2) / 2, y, f"{demand_mwh:.1f}", ha="center", va="center", fontsize=8.6, family="monospace", color="#1a1a1a", zorder=3)
                    fig.text((x2 + x3) / 2, y, f"{street_km:.2f}", ha="center", va="center", fontsize=8.6, family="monospace", color="#1a1a1a", zorder=3)

    ax.set_title(title)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal", adjustable="box")

    if caps_legend:
        legend_lines = []
        for key, value in caps_legend.items():
            if value is None:
                continue
            legend_lines.append(f"{key}: {value}")
        if legend_lines:
            legend_handles = [
                Line2D([], [], linestyle="none", marker=None, color="none", label=line)
                for line in legend_lines
            ]
            caps_box = ax.legend(
                handles=legend_handles,
                title="Topology caps",
                loc="lower left",
                frameon=True,
                facecolor="white",
                framealpha=0.9,
                edgecolor="#bdbdbd",
                fontsize=8,
                title_fontsize=9,
                handlelength=0,
                handletextpad=0,
                borderpad=0.6,
            )
            ax.add_artist(caps_box)

    fig.subplots_adjust(top=0.88)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.88))
    fig.savefig(output_path, dpi=220)
    plt.close(fig)

class EnergySystemPlotter:
    _DEFAULT_FIGSIZE = (10, 10)
    _UNSUPPLIED_COLOR = "darkgray"
    _COMMODITY_COLORS = {
        "gas": "#cb181d",
        "heat_pump": "#fec44f",
        "hydrogen": "#2171b5",
        "oil": "#3b2107",
        "biomass": "#31a354",
        "electricity": "#756bb1",
        "coal": "#636363",
        "other": "#969696",
    }

    def __init__(self, energy_system: EnergySystem):
        self.energy_system = energy_system

    @staticmethod
    def _technology_family(label: str, commodity_in: str | None = None) -> str:
        s = str(label or "").lower()
        c = str(commodity_in or "").lower()
        if "heat_pump" in s:
            return "heat_pump"
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

    @staticmethod
    def _commodity_bucket(commodity_in: str | None, tech_name: str) -> str:
        c = str(commodity_in or "").strip().lower()
        if c.startswith("district_heat_"):
            return "import"
        if c in {"gas", "oil", "hydrogen", "biomass", "electricity", "coal"}:
            return c
        fam = EnergySystemPlotter._technology_family(tech_name, commodity_in)
        if fam == "other" and c:
            return c
        return fam

    def _technology_bucket(self, tech: Any) -> str:
        tech_name = str(getattr(tech, "name", "") or "")
        base = self._base_tech_name(tech_name).lower()
        commodity_in = str(getattr(tech, "commodity_in", "") or "").strip().lower()
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
            return f"cen_{self._commodity_bucket(commodity_in, tech_name)}"
        if base.startswith("ind_"):
            return f"ind_{self._commodity_bucket(commodity_in, tech_name)}"
        return tech_name

    def _build_label_color_map(self, labels: set[str], *, share_view: str) -> dict[str, Any]:
        if share_view == "commodity":
            out: dict[str, Any] = {}
            for label in labels:
                key = str(label).lower()
                out[label] = self._COMMODITY_COLORS.get(key, self._COMMODITY_COLORS["other"])
            return out

        family_cmaps = {
            "gas": "Reds",
            "heat_pump": "Wistia",
            "hydrogen": "Blues",
            "oil": "YlOrBr",
            "biomass": "Greens",
            "electricity": "Purples",
            "coal": "Greys",
            "other": "Greys",
        }
        out: dict[str, Any] = {}
        for label in sorted(labels):
            fam = self._technology_family(label)
            cmap = cm.get_cmap(family_cmaps.get(fam, "Greys"))
            token = hashlib.md5(str(label).encode("utf-8")).hexdigest()
            u = (int(token[:8], 16) % 1000) / 999.0
            val = 0.42 + 0.50 * float(u)
            out[label] = cmap(float(val))
        return out

    @staticmethod
    def _positive_map(values: dict[str, Any] | None) -> dict[str, float]:
        if not values:
            return {}
        out: dict[str, float] = {}
        for name, value in values.items():
            try:
                val_f = float(value)
            except (TypeError, ValueError):
                continue
            if val_f > 0.0:
                out[name] = val_f
        return out

    def _aggregate_region_outputs(
        self,
        *,
        region: Any,
        demand_commodity_in: str | None,
        kind: str,
        share_view: str,
    ) -> dict[str, float]:
        outputs: dict[str, float] = {}
        for r_tech in region.region_technologies:
            if not self._is_visual_supply_tech(r_tech.technology, demand_commodity_in):
                continue
            value = float(r_tech.initial_energy_output) if kind == "energy" else float(r_tech.initial_capacity)
            if value <= 0:
                continue
            label = (
                self._technology_bucket(r_tech.technology)
                if share_view == "technology"
                else self._commodity_bucket(getattr(r_tech.technology, "commodity_in", None), r_tech.technology.name)
            )
            outputs[label] = outputs.get(label, 0.0) + value
        return outputs

    @staticmethod
    def _lookup_year_value(series: Any, year: int) -> float:
        if not isinstance(series, dict):
            return 0.0
        val = series.get(str(year), series.get(year, 0.0))
        try:
            return float(val or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _normalize_cp_name(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", name.lower())

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

    def _resolve_cp_key(
        self,
        tech_year_map: dict[str, Any],
        tech_name: str,
        *,
        region_idx: int,
        single_region: bool,
    ) -> str | None:
        m = re.search(r"_D\d+$", tech_name, flags=re.IGNORECASE)
        base_name = tech_name[:m.start()] if m else tech_name

        raw_candidates: list[str] = []
        if not single_region:
            raw_candidates.append(f"{base_name}_D{region_idx}")
        raw_candidates.append(tech_name)
        if m:
            raw_candidates.append(base_name)

        candidates: list[str] = []
        seen_candidates: set[str] = set()
        for candidate in raw_candidates:
            if candidate in seen_candidates:
                continue
            seen_candidates.add(candidate)
            candidates.append(candidate)

        lower_map = {k.lower(): k for k in tech_year_map.keys()}
        norm_map = {self._normalize_cp_name(k): k for k in tech_year_map.keys()}
        district_key_pattern = re.compile(rf"^{re.escape(base_name)}_d\d+$", re.IGNORECASE)
        has_district_keys = any(district_key_pattern.match(k) for k in tech_year_map.keys())

        for candidate in candidates:
            if not single_region and has_district_keys and not re.search(r"_D\d+$", candidate, flags=re.IGNORECASE):
                continue
            if candidate in tech_year_map:
                return candidate
            candidate_lower = candidate.lower()
            if candidate_lower in lower_map:
                matched = lower_map[candidate_lower]
                if not single_region and has_district_keys and not re.search(r"_D\d+$", matched, flags=re.IGNORECASE):
                    continue
                return matched
            candidate_norm = self._normalize_cp_name(candidate)
            if candidate_norm in norm_map:
                matched = norm_map[candidate_norm]
                if not single_region and has_district_keys and not re.search(r"_D\d+$", matched, flags=re.IGNORECASE):
                    continue
                return matched
        return None

    def plot(
        self,
        demand_name: str | None = DEFAULT_HEAT_GRID_DEMAND_NAME,
        kind: str = "energy",
        share_view: str = "technology",
        *,
        demand_values_by_region: dict[int, float] | None = None,
        tech_outputs_by_region: dict[int, dict[str, float]] | None = None,
        title_suffix: str | None = None,
        ax: Any | None = None,
        show: bool = True,
        block: bool = True,
        draw_basemap: bool = True,
        region_fill_alpha: float = 0.5,
        region_fill_color: str = "grey",
        draw_region_ids: bool = False,
        region_boundary_linewidth: float = 2.0,
    ) -> None:
        if kind not in {"energy", "power"}:
            raise ValueError("kind must be either 'energy' or 'power'")
        if share_view not in {"technology", "commodity"}:
            raise ValueError("share_view must be 'technology' or 'commodity'")
        if not self.energy_system.regions:
            raise ValueError("EnergySystem has no regions to plot")

        # Create a figure and axis
        created_new_figure = ax is None
        if ax is None:
            _, ax = plt.subplots(figsize=self._DEFAULT_FIGSIZE)

        has_unsupplied = False
        used_labels: set[str] = set()

        region_plot_data: dict[int, dict[str, Any]] = {}
        if demand_name:
            for region_idx, region in enumerate(self.energy_system.regions):
                demand = region.get_demand(demand_name)
                if not demand:
                    continue

                demand_value = (
                    float(demand_values_by_region[region_idx])
                    if demand_values_by_region and region_idx in demand_values_by_region
                    else float(demand.value)
                )

                tech_outputs = (
                    self._positive_map(tech_outputs_by_region[region_idx])
                    if tech_outputs_by_region and region_idx in tech_outputs_by_region
                    else self._aggregate_region_outputs(
                        region=region,
                        demand_commodity_in=demand.demand.commodity_in,
                        kind=kind,
                        share_view=share_view,
                    )
                )

                if tech_outputs:
                    used_labels.update(tech_outputs.keys())
                region_plot_data[region_idx] = {
                    "demand_value": demand_value,
                    "tech_outputs": tech_outputs,
                }

        color_map = self._build_label_color_map(used_labels, share_view=share_view) if used_labels else {}

        all_bounds = [region.polygon.total_bounds for region in self.energy_system.regions if region.polygon is not None]
        if all_bounds:
            minx = min((b[0] for b in all_bounds))
            miny = min((b[1] for b in all_bounds))
            maxx = max((b[2] for b in all_bounds))
            maxy = max((b[3] for b in all_bounds))
            map_span = max(float(maxx - minx), float(maxy - miny), 1.0)
        else:
            map_span = 1.0

        area_by_region: dict[int, float] = {}
        for region_idx, region in enumerate(self.energy_system.regions):
            poly = region.polygon
            if poly is None or poly.empty:
                area_by_region[region_idx] = 0.0
                continue
            area_val = float(poly.geometry.iloc[0].area)
            area_by_region[region_idx] = area_val if area_val > 0.0 else 0.0
        max_region_area = max(area_by_region.values()) if area_by_region else 0.0

        # Plot each region's polygon
        for region_idx, region in enumerate(self.energy_system.regions):
            region.polygon.boundary.plot(
                ax=ax,
                edgecolor="black",
                linewidth=float(region_boundary_linewidth),
                zorder=2,
            )
            if float(region_fill_alpha) > 0:
                region.polygon.plot(
                    ax=ax,
                    alpha=float(region_fill_alpha),
                    color=region_fill_color,
                    zorder=1,
                )

            if draw_region_ids:
                centroid = region.polygon.geometry.iloc[0].representative_point()
                if isinstance(centroid, Point):
                    ax.text(
                        centroid.x,
                        centroid.y,
                        str(getattr(region, "id_", region_idx)),
                        ha="center",
                        va="center",
                        fontsize=8,
                        zorder=5,
                        bbox={"facecolor": "white", "alpha": 0.7, "edgecolor": "none", "pad": 1.0},
                    )

            # If demand_name is provided, process the demand data
            if demand_name:
                region_data = region_plot_data.get(region_idx)
                if not region_data:
                    continue

                demand_value = float(region_data["demand_value"])
                tech_outputs = region_data["tech_outputs"]

                # Get the centroid of the polygon for pie chart placement
                centroid = region.polygon.geometry.iloc[0].centroid
                if isinstance(centroid, Point):
                    x, y = centroid.x, centroid.y

                    area_norm = (area_by_region.get(region_idx, 0.0) / max_region_area) if max_region_area > 0 else 0.0
                    pie_radius = map_span * (0.02 + 0.02 * (area_norm ** 0.5))

                    # Total output from all technologies
                    total_output = sum(tech_outputs.values())

                    # If there are technologies supplying this demand with non-zero output
                    if total_output > 0:
                        # Normalize the outputs for the pie chart
                        tech_outputs_normalized = {k: v / total_output for k, v in tech_outputs.items()}
                        ordered = sorted(tech_outputs_normalized.items(), key=lambda kv: kv[0])
                        sizes = [v for _, v in ordered]
                        colors = [color_map.get(label, self._UNSUPPLIED_COLOR) for label, _ in ordered]
                    else:
                        # If no technology supplies this demand, show a single-colored pie
                        sizes = [1]
                        colors = [self._UNSUPPLIED_COLOR]
                        has_unsupplied = True

                    # Draw the pie chart
                    wedge, _ = ax.pie(
                        sizes,
                        colors=colors,
                        radius=pie_radius,
                        center=(x, y),
                        frame=True,
                        textprops={"fontsize": 6},
                        wedgeprops={"width": pie_radius / 1.5, 'linewidth': 1.0, 'edgecolor': 'white'}
                    )

                    [w.set_zorder(3) for w in wedge]

                    # Add the demand value as text below the pie chart
                    ax.text(
                        x,
                        y - pie_radius - 10,
                        f"Demand: {demand_value / 1000:.0f} MWh" if kind == "energy" else f"Demand: {demand_value:.2f} kW",
                        ha="center",
                        fontsize=10,
                        zorder=4,
                    )

        # Add OpenStreetMap basemap
        if draw_basemap:
            try:
                ctx.add_basemap(
                    ax,
                    crs=self.energy_system.regions[0].polygon.crs,
                    source=ctx.providers.OpenStreetMap.Mapnik,
                    alpha=0.7,
                    zorder=0,
                )
            except Exception as e:
                print(f"Warning: Could not add basemap: {e}")

        # Add a legend for the technologies
        legend_techs = sorted(used_labels)
        legend_elements = [Patch(facecolor=color_map.get(tech, self._UNSUPPLIED_COLOR), label=tech) for tech in legend_techs]
        if demand_name and has_unsupplied:
            # Add a legend entry for demands without technology supply
            legend_elements.append(Patch(facecolor=self._UNSUPPLIED_COLOR, label="Unsupplied Demand"))
        if legend_elements:
            legend_title = "Technologies" if share_view == "technology" else "Commodities"
            ax.legend(handles=legend_elements, loc="upper right", title=legend_title)

        # Set axis labels and title
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

        # Show the plot
        if show and created_new_figure:
            plt.show(block=block)

    def _build_optimized_plot_data(
        self,
        optimization_results: dict[str, Any],
        *,
        year: int,
        demand_name: str | None,
        kind: str,
        share_view: str,
    ) -> tuple[dict[int, float], dict[int, dict[str, float]]]:
        kpis = optimization_results.get("kpis", {}) if isinstance(optimization_results, dict) else {}
        metric_key = "energy_by_tech_year" if kind == "energy" else "cap_active_by_tech_year"
        tech_year_map = kpis.get(metric_key, {}) if isinstance(kpis, dict) else {}
        demand_values_by_region: dict[int, float] = {}
        tech_outputs_by_region: dict[int, dict[str, float]] = {}
        single_region = len(self.energy_system.regions) == 1

        for idx, region in enumerate(self.energy_system.regions):
            demand_cp = "HeatDemand" if single_region else f"HeatDemand_D{idx}"
            demand_values_by_region[idx] = self._lookup_year_value(tech_year_map.get(demand_cp, {}), year)
            demand = region.get_demand(demand_name) if demand_name else None
            demand_commodity_in = demand.demand.commodity_in if demand else None

            per_region: dict[str, float] = {}
            for r_tech in region.region_technologies:
                tech_name = r_tech.technology.name
                if not self._is_visual_supply_tech(r_tech.technology, demand_commodity_in):
                    continue

                cp_key = self._resolve_cp_key(
                    tech_year_map,
                    tech_name,
                    region_idx=idx,
                    single_region=single_region,
                )
                if cp_key is None:
                    continue

                value = self._lookup_year_value(tech_year_map.get(cp_key, {}), year)
                if value > 0:
                    label = (
                        self._technology_bucket(r_tech.technology)
                        if share_view == "technology"
                        else self._commodity_bucket(getattr(r_tech.technology, "commodity_in", None), tech_name)
                    )
                    per_region[label] = per_region.get(label, 0.0) + value
            tech_outputs_by_region[idx] = per_region

        return demand_values_by_region, tech_outputs_by_region

    def plot_optimized_year(
        self,
        optimization_results: dict[str, Any],
        *,
        year: int,
        demand_name: str | None = DEFAULT_HEAT_GRID_DEMAND_NAME,
        kind: str = "energy",
        share_view: str = "technology",
        show: bool = True,
        block: bool = True,
    ) -> None:
        demand_values_by_region, tech_outputs_by_region = self._build_optimized_plot_data(
            optimization_results,
            year=year,
            demand_name=demand_name,
            kind=kind,
            share_view=share_view,
        )

        self.plot(
            demand_name=demand_name,
            kind=kind,
            share_view=share_view,
            demand_values_by_region=demand_values_by_region,
            tech_outputs_by_region=tech_outputs_by_region,
            title_suffix=f"optimized {year}",
            show=show,
            block=block,
        )

    def plot_optimized_years_grid(
        self,
        optimization_results: dict[str, Any],
        *,
        years: list[int],
        demand_name: str | None = DEFAULT_HEAT_GRID_DEMAND_NAME,
        kind: str = "energy",
        share_view: str = "technology",
        ncols: int = 2,
        include_initial: bool = False,
        initial_title: str = "unoptimized",
        show: bool = True,
        block: bool = True,
    ) -> None:
        if not years:
            raise ValueError("years must not be empty")
        if ncols <= 0:
            raise ValueError("ncols must be > 0")

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
                share_view=share_view,
                title_suffix=initial_title,
                ax=axes_flat[0],
                show=False,
            )
            offset = 1

        for idx, year in enumerate(years):
            demand_values_by_region, tech_outputs_by_region = self._build_optimized_plot_data(
                optimization_results,
                year=year,
                demand_name=demand_name,
                kind=kind,
                share_view=share_view,
            )
            self.plot(
                demand_name=demand_name,
                kind=kind,
                share_view=share_view,
                demand_values_by_region=demand_values_by_region,
                tech_outputs_by_region=tech_outputs_by_region,
                title_suffix=f"optimized {year}",
                ax=axes_flat[idx + offset],
                show=False,
            )

        for idx in range(total_panels, len(axes_flat)):
            axes_flat[idx].axis("off")

        fig.tight_layout()
        if show:
            plt.show(block=block)

    def plot_to_file(
        self,
        output_path: str | Path,
        *,
        demand_name: str | None = DEFAULT_HEAT_GRID_DEMAND_NAME,
        kind: str = "energy",
        share_view: str = "technology",
        demand_values_by_region: dict[int, float] | None = None,
        tech_outputs_by_region: dict[int, dict[str, float]] | None = None,
        title_suffix: str | None = None,
        dpi: int = 200,
        draw_basemap: bool = True,
        region_fill_alpha: float = 0.5,
        region_fill_color: str = "grey",
        draw_region_ids: bool = False,
        region_boundary_linewidth: float = 2.0,
    ) -> None:
        """Renders a single energy-system map plot"""
        self.plot(
            demand_name=demand_name,
            kind=kind,
            share_view=share_view,
            demand_values_by_region=demand_values_by_region,
            tech_outputs_by_region=tech_outputs_by_region,
            title_suffix=title_suffix,
            show=False,
            draw_basemap=draw_basemap,
            region_fill_alpha=region_fill_alpha,
            region_fill_color=region_fill_color,
            draw_region_ids=draw_region_ids,
            region_boundary_linewidth=region_boundary_linewidth,
        )
        plt.gcf().savefig(Path(output_path), dpi=dpi)
        plt.close(plt.gcf())