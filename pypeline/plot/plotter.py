import re
from typing import Any
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from shapely.geometry import Point
import matplotlib.cm as cm
import contextily as ctx

from pypeline.energy_system.energy_system import EnergySystem
from pypeline.optimization.solver import Results

class EnergySystemPlotter:
    _DEFAULT_FIGSIZE = (10, 10)
    _UNSUPPLIED_COLOR = "darkgray"
    _COMMODITY_COLORS = {
        "gas": "#d7301f",
        "oil": "#050300",
        "hydrogen": "#2171b5",
        "biomass": "#238b45",
        "electricity": "#e8ff51",
        "other": "#f161db",
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
        if c in {"gas", "oil", "hydrogen", "biomass", "electricity", "coal"}:
            return c
        return EnergySystemPlotter._technology_family(tech_name, commodity_in)

    def _build_label_color_map(self, labels: set[str], *, share_view: str) -> dict[str, Any]:
        if share_view == "commodity":
            out: dict[str, Any] = {}
            for label in labels:
                key = str(label).lower()
                out[label] = self._COMMODITY_COLORS.get(key, self._COMMODITY_COLORS["other"])
            return out

        families: dict[str, list[str]] = {}
        for label in sorted(labels):
            fam = self._technology_family(label)
            families.setdefault(fam, []).append(label)

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
        for fam, fam_labels in families.items():
            cmap = cm.get_cmap(family_cmaps.get(fam, "Greys"))
            n = len(fam_labels)
            vals = [0.75] if n == 1 else np.linspace(0.45, 0.9, n)
            for idx, label in enumerate(fam_labels):
                out[label] = cmap(float(vals[idx]))
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
                r_tech.technology.name
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
        return base in {"heat_exchanger", "ind_district_heating_connection"} or base.startswith("heatexchanger")

    def _is_visual_supply_tech(self, tech: Any, demand_commodity_in: str | None) -> bool:
        tech_name = getattr(tech, "name", "")
        base = self._base_tech_name(tech_name)
        if self._is_heat_exchanger_tech(base):
            return False
        if base.startswith("cen_"):
            return True
        if demand_commodity_in is None:
            return False
        return getattr(tech, "commodity_out", None) == demand_commodity_in

    def _resolve_cp_key(
        self,
        tech_year_map: dict[str, Any],
        tech_name: str,
        *,
        region_idx: int,
        single_region: bool,
    ) -> str | None:
        candidates: list[str] = [tech_name]
        if not single_region:
            candidates.append(f"{tech_name}_D{region_idx}")

        m = re.search(r"_D\d+$", tech_name, flags=re.IGNORECASE)
        if m:
            candidates.append(tech_name[:m.start()])

        lower_map = {k.lower(): k for k in tech_year_map.keys()}
        norm_map = {self._normalize_cp_name(k): k for k in tech_year_map.keys()}

        for candidate in candidates:
            if candidate in tech_year_map:
                return candidate
            candidate_lower = candidate.lower()
            if candidate_lower in lower_map:
                return lower_map[candidate_lower]
            candidate_norm = self._normalize_cp_name(candidate)
            if candidate_norm in norm_map:
                return norm_map[candidate_norm]
        return None

    def plot(
        self,
        demand_name: str | None = "residential_heat",
        kind: str = "energy",
        share_view: str = "technology",
        *,
        demand_values_by_region: dict[int, float] | None = None,
        tech_outputs_by_region: dict[int, dict[str, float]] | None = None,
        title_suffix: str | None = None,
        ax: Any | None = None,
        show: bool = True,
        block: bool = True,
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

        # Plot each region's polygon
        for region_idx, region in enumerate(self.energy_system.regions):
            region.polygon.boundary.plot(ax=ax, edgecolor="black", linewidth=2, zorder=2)
            region.polygon.plot(ax=ax, alpha=0.5, color="grey", zorder=1)

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

                    # Calculate the pie radius based on demand_value
                    pie_radius = 20 + demand_value / 100000  # Adjust formula as needed

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
                        wedgeprops={"width": pie_radius / 1.5, 'linewidth': 2, 'edgecolor': 'white'}
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
                        tech_name
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
        demand_name: str | None = "residential_heat",
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
        demand_name: str | None = "residential_heat",
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