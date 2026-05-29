# coding=utf-8
"""High-level wrapper around :class:`cesm.core.plotter.Plotter`.

The original CESM plotter operates on fully-qualified, region-suffixed
commodity and conversion-process names (e.g. ``residential_heat_D0``,
``cen_gas_boiler_D2``, ``heat_pipe_D0_D1``). This wrapper exposes the same
plot types but lets the caller think in terms of a *base* commodity and an
optional region scope:

* ``region=None``                              -> commodity used as-is (bare name)
* ``region=<int>``                             -> ``{commodity}_D{region}``
* ``region=CesmPlotter.ALL_REGIONS``           -> sum/concat across all regions

For Sankey diagrams, a per-region view is supported. Pipes between regions
are rendered with a virtual ``to Region X`` / ``from Region Y`` node on the
foreign endpoint so they remain visible without dragging the foreign region's
network into the diagram.
"""
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Union

import pandas as pd
import plotly.colors as plc
import plotly.graph_objects as go

from cesm.core.data_access import DAO
from cesm.core.plotter import Plotter as _InnerPlotter, PlotType, PlotterExeption

from geopipe.optimization.cesm.techmap import _color_from_name

logger = logging.getLogger(__name__)



# Suffix conventions are produced in `geopipe.optimization.cesm.techmap`:
#   <tech>_D<region>                 - decentralized/central/grid technology
#   <pipe>_D<region_in>_D<region_out>- pipe between two regions
#   <commodity>_D<region>            - region-scoped (grid) commodity
_REGION_SUFFIX_RE = re.compile(r"_D(\d+)$")
_PIPE_SUFFIX_RE = re.compile(r"_D(\d+)_D(\d+)$")


class _AllRegionsSentinel:
    """Sentinel that means 'sum / merge across every region'."""

    _instance: Optional["_AllRegionsSentinel"] = None

    def __new__(cls) -> "_AllRegionsSentinel":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "CesmPlotter.ALL_REGIONS"


RegionScope = Union[int, _AllRegionsSentinel, None]


@dataclass(frozen=True)
class _ParsedName:
    base: str
    regions: tuple[int, ...]  # () = global/bare, (r,) = single region, (r_in, r_out) = pipe

    @property
    def is_pipe(self) -> bool:
        return len(self.regions) == 2


class _HasDbPath:  # structural-typing hint only; matches Solution and similar
    db_path: Union[str, Path]


def _resolve_dao(
    source: Union[DAO, sqlite3.Connection, Path, str, _HasDbPath],
) -> tuple[DAO, Optional[sqlite3.Connection], bool]:
    """Normalize the constructor argument to ``(dao, connection, owns_conn)``.

    ``owns_conn`` is ``True`` when this plotter opened the connection and is
    therefore responsible for closing it.
    """
    if isinstance(source, DAO):
        return source, None, False
    if isinstance(source, sqlite3.Connection):
        return DAO(source), source, False
    if isinstance(source, (str, Path)):
        conn = sqlite3.connect(str(source))
        return DAO(conn), conn, True
    db_path = getattr(source, "db_path", None)
    if db_path is not None:
        conn = sqlite3.connect(str(db_path))
        return DAO(conn), conn, True
    raise TypeError(
        f"CesmPlotter: cannot build a DAO from {type(source).__name__}. "
        f"Pass a Solution, a path to a sqlite file, a sqlite3.Connection, or a DAO."
    )


def _parse_name(name: str) -> _ParsedName:
    """Split off trailing region suffixes from a commodity or CP name."""
    pipe = _PIPE_SUFFIX_RE.search(name)
    if pipe is not None:
        base = name[: pipe.start()]
        return _ParsedName(base=base, regions=(int(pipe.group(1)), int(pipe.group(2))))
    single = _REGION_SUFFIX_RE.search(name)
    if single is not None:
        base = name[: single.start()]
        return _ParsedName(base=base, regions=(int(single.group(1)),))
    return _ParsedName(base=name, regions=())


class CesmPlotter:
    """Region-aware wrapper around the CESM Plotter.

    Parameters
    ----------
    source
        Positional form. Anything pointing at a solved CESM run: a
        :class:`Solution`-like object (anything with a ``db_path`` attribute),
        a path/string to a sqlite database file, a :class:`sqlite3.Connection`,
        or a :class:`cesm.core.data_access.DAO`.
    solution
        Keyword form. A Solution-like object exposing ``db_path``. The plotter
        opens and closes the sqlite connection itself.
    db_path
        Keyword form. Path/string to a sqlite database file.
    show
        If ``True`` (default) figures are displayed via Plotly's ``show()`` on
        creation. Set to ``False`` for headless workflows; the figure is always
        returned regardless.

    Exactly one of ``source``, ``solution``, or ``db_path`` must be supplied.
    The plotter is also a context manager::

        with CesmPlotter(solution=solution) as plotter:
            plotter.plot_sankey(year=2030, region=0)
    """

    ALL_REGIONS: _AllRegionsSentinel = _AllRegionsSentinel()
    PlotType = PlotType

    def __init__(
        self,
        source: Union[DAO, sqlite3.Connection, Path, str, "_HasDbPath", None] = None,
        *,
        solution: Optional["_HasDbPath"] = None,
        db_path: Optional[Union[Path, str]] = None,
        show: bool = True,
    ) -> None:
        provided = [x for x in (source, solution, db_path) if x is not None]
        if len(provided) != 1:
            raise TypeError(
                "CesmPlotter requires exactly one of 'source', 'solution', or 'db_path'."
            )
        self.dao, self._conn, self._owns_conn = _resolve_dao(provided[0])
        self._inner = _InnerPlotter(self.dao)
        self.show = show

    def close(self) -> None:
        """Close the underlying sqlite connection if this plotter opened it."""
        if self._owns_conn and self._conn is not None:
            self._conn.close()
            self._conn = None
            self._owns_conn = False

    def __enter__(self) -> "CesmPlotter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------ public

    @property
    def regions(self) -> list[int]:
        """All region IDs discovered from commodity and CP names."""
        ids: set[int] = set()
        for name in self.dao.get_set("commodity") or []:
            ids.update(_parse_name(name).regions)
        for name in self.dao.get_set("conversion_process") or []:
            ids.update(_parse_name(name).regions)
        return sorted(ids)

    def base_commodities(self) -> list[str]:
        """All unique base (region-stripped) commodity names."""
        return sorted({_parse_name(c).base for c in (self.dao.get_set("commodity") or [])})

    # ---- plots that don't depend on a commodity: delegate as-is ----

    def plot_single_value(self, single_value_type):
        return self._inner.plot_single_value(single_value_type)

    # ---- bar / timeseries / sankey with region scoping ----

    def plot_bars(
        self,
        bar_type: PlotType.Bar,
        commodity: Optional[str] = None,
        region: RegionScope = None,
    ) -> go.Figure:
        """Bar plot for the given metric.

        ``CO2_EMISSION`` and ``PRIMARY_ENERGY`` ignore ``commodity``/``region``.
        For the other metrics, ``commodity`` is the *base* name (e.g.
        ``residential_heat``) and ``region`` selects the scope.
        """
        if bar_type in (PlotType.Bar.CO2_EMISSION, PlotType.Bar.PRIMARY_ENERGY):
            return self._inner.plot_bars(bar_type)

        bar_cfg = {
            PlotType.Bar.ENERGY_CONSUMPTION: ("Eintot", "cin", "energy", "Energy Consumption"),
            PlotType.Bar.ENERGY_PRODUCTION: ("Eouttot", "cout", "energy", "Energy Production"),
            PlotType.Bar.ACTIVE_CAPACITY: ("Cap_active", "cout", "power", "Active Capacity"),
            PlotType.Bar.NEW_CAPACITY: ("Cap_new", "cout", "power", "New Capacity"),
        }
        if bar_type not in bar_cfg:
            raise PlotterExeption(f"Unsupported bar type: {bar_type}")
        if commodity is None:
            raise PlotterExeption("'commodity' is required for this bar plot type")

        var_name, filter_col, unit_kind, title_prefix = bar_cfg[bar_type]
        df = self._fetch_filtered(var_name, commodity, region, filter_col)
        title = f"{title_prefix} for {commodity}{self._scope_suffix(region)}"
        yaxis = f"{title_prefix} [{self._inner.units[unit_kind]}]"

        self._ensure_color_order_entries(df["cp"])
        traces = self._inner._get_traces(df, type="bar", stacks="cp")
        layout = self._inner._get_default_layout(title=title, yaxistitle=yaxis)
        fig = go.Figure(data=traces, layout=layout)
        if self.show:
            fig.show()
        return fig

    def plot_timeseries(
        self,
        timeseries_type: PlotType.TimeSeries,
        year: int,
        commodity: str,
        region: RegionScope = None,
    ) -> go.Figure:
        """Stacked time-series plot for one year, scoped by ``region``."""
        self._check_year(year)

        ts_cfg = {
            PlotType.TimeSeries.ENERGY_CONSUMPTION: ("Eintime", "cin", "energy", "Energy Consumption"),
            PlotType.TimeSeries.ENERGY_PRODUCTION: ("Eouttime", "cout", "energy", "Energy Production"),
            PlotType.TimeSeries.POWER_CONSUMPTION: ("Pin", "cin", "power", "Power Consumption"),
            PlotType.TimeSeries.POWER_PRODUCTION: ("Pout", "cout", "power", "Power Production"),
        }
        if timeseries_type not in ts_cfg:
            raise PlotterExeption(f"Unsupported timeseries type: {timeseries_type}")
        var_name, filter_col, unit_kind, title_prefix = ts_cfg[timeseries_type]

        df = self._fetch_filtered(var_name, commodity, region, filter_col, year=year, time_axis=True)
        title = f"{title_prefix} for {commodity} in {year}{self._scope_suffix(region)}"
        yaxis = f"{title_prefix} [{self._inner.units[unit_kind]}]"

        self._ensure_color_order_entries(df["cp"])
        traces = self._inner._get_traces(df, type="timeseries", x="Time", stacks="cp")
        layout = self._inner._get_default_layout(title=title, yaxistitle=yaxis, xaxistitle="Time")
        fig = go.Figure(data=traces, layout=layout)
        if self.show:
            fig.show()
        return fig

    def plot_sankey(self, year: int, region: RegionScope = None) -> go.Figure:
        """Sankey diagram for ``year``. Per-region when ``region`` is an int."""
        self._check_year(year)

        if region is None or isinstance(region, _AllRegionsSentinel):
            return self._inner.plot_sankey(year)

        if not isinstance(region, int):
            raise PlotterExeption(f"Invalid region: {region!r}")
        if region not in self.regions:
            raise PlotterExeption(f"Region {region} not in model. Known: {self.regions}")

        sankey_df = self._build_region_sankey_df(year, region)
        if sankey_df.empty:
            raise PlotterExeption(f"No Sankey flows found for region {region} in year {year}")

        nodes = sorted(set(sankey_df["source"]).union(sankey_df["target"]))
        idx = {n: i for i, n in enumerate(nodes)}
        sankey_df["source_i"] = sankey_df["source"].map(idx)
        sankey_df["target_i"] = sankey_df["target"].map(idx)

        colors = self._rgba_colors(nodes, alpha=0.8)

        fig = go.Figure(
            data=[
                go.Sankey(
                    arrangement="perpendicular",
                    node=dict(thickness=10, line=dict(color="black", width=0.01),
                              label=nodes, color=colors),
                    link=dict(source=sankey_df["source_i"],
                              target=sankey_df["target_i"],
                              value=sankey_df["value"]),
                )
            ]
        )
        fig.update_layout(title_text=f"{year} Sankey - Region {region}", font_size=12)
        if self.show:
            fig.show()
        return fig

    # ----------------------------------------------------------- internal helpers

    def _scope_suffix(self, region: RegionScope) -> str:
        if region is None:
            return ""
        if isinstance(region, _AllRegionsSentinel):
            return " (summed across regions)"
        return f" (region {region})"

    def _resolve_commodity_names(self, base: str, region: RegionScope) -> list[str]:
        """Resolve a base commodity name + region scope to full DB names."""
        all_commodities = list(self.dao.get_set("commodity") or [])
        all_set = set(all_commodities)

        if region is None:
            if base in all_set:
                return [base]
            raise PlotterExeption(
                f"Commodity '{base}' not found. If it is region-scoped, pass region=<int> "
                f"or region=CesmPlotter.ALL_REGIONS."
            )

        if isinstance(region, _AllRegionsSentinel):
            matches = [c for c in all_commodities if _parse_name(c).base == base]
            if not matches:
                raise PlotterExeption(f"No commodities found with base name '{base}'")
            return matches

        if isinstance(region, int):
            candidate = f"{base}_D{region}"
            if candidate in all_set:
                return [candidate]
            if base in all_set:
                # bare/global commodity - still valid in a region scope (e.g. fuel imports)
                return [base]
            raise PlotterExeption(
                f"Commodity '{base}' not found in region {region} (looked for '{candidate}')"
            )

        raise PlotterExeption(f"Invalid region: {region!r}")

    def _fetch_filtered(
        self,
        var_name: str,
        commodity: str,
        region: RegionScope,
        filter_col: str,
        year: Optional[int] = None,
        time_axis: bool = False,
    ) -> pd.DataFrame:
        """Fetch the raw frame and apply commodity / region / year filters."""
        commodity_names = self._resolve_commodity_names(commodity, region)
        df = self.dao.get_as_dataframe(var_name)
        df = df[df[filter_col].isin(commodity_names)]
        if year is not None and "Year" in df.columns:
            df = df[df["Year"] == year]

        if isinstance(region, int):
            # Keep only CPs that belong to this region (decentralized/central/grid in
            # this region, or pipes touching it). Drops e.g. cross-region pipes that
            # would otherwise leak through bare-commodity matches.
            mask = df["cp"].apply(lambda cp: self._cp_belongs_to_region(cp, region))
            df = df[mask]

        if isinstance(region, _AllRegionsSentinel):
            # Strip region from CP names so identical techs in different regions stack
            # together rather than as separate series.
            df = df.copy()
            df["cp"] = df["cp"].map(lambda cp: _parse_name(cp).base)
            group_cols = ["cp"]
            if "Year" in df.columns:
                group_cols.append("Year")
            if time_axis and "Time" in df.columns:
                group_cols.append("Time")
            df = df.groupby(group_cols, as_index=False)["value"].sum()

        if df.empty:
            raise PlotterExeption(
                f"No data for '{commodity}'{self._scope_suffix(region)} in '{var_name}'."
            )
        return df

    @staticmethod
    def _cp_belongs_to_region(cp_name: str, region: int) -> bool:
        parsed = _parse_name(cp_name)
        if not parsed.regions:
            return True  # global CP (e.g. import) - relevant everywhere
        return region in parsed.regions

    def _node_color(self, name: str) -> str:
        """Color lookup that tolerates virtual nodes added by region scoping."""
        self._ensure_color_order_entries([name])
        return self._inner._get_color(name)

    def _ensure_color_order_entries(self, names: Iterable[str]) -> None:
        """Seed ``_colors_orders`` for names the DB never produced.

        Cross-region aggregation strips ``_D{id}`` from CP names, and per-region
        Sankey adds virtual ``to Region X`` / ``from Region Y`` nodes; neither
        exists in the DB-backed lookup the inner plotter uses for colours/order.
        We reuse ``techmap._color_from_name`` so aggregated entries get the same
        colour as their suffixed siblings (the techmap already strips ``_D.*$``
        before hashing).
        """
        settings = self._inner._colors_orders
        for name in names:
            if name not in settings:
                settings[name] = {"color": _color_from_name(name), "order": None}

    def _rgba_colors(self, names: Iterable[str], alpha: float) -> list[str]:
        out = []
        for name in names:
            r, g, b = plc.hex_to_rgb(self._node_color(name))
            out.append(f"rgba({r}, {g}, {b}, {alpha})")
        return out

    # -- sankey internals --

    def _build_region_sankey_df(self, year: int, region: int) -> pd.DataFrame:
        """Return source/target/value rows scoped to a single region.

        Pipes are kept but the foreign-region endpoint is replaced by a virtual
        node so the diagram stays inside one region without losing the flow.
        """
        consumption = self.dao.get_as_dataframe("Eintot", Year=year)
        production = self.dao.get_as_dataframe("Eouttot", Year=year)

        rows: list[tuple[str, str, float]] = []

        for _, row in consumption.iterrows():
            cin, cp, value = row["cin"], row["cp"], row["value"]
            if self._is_filtered_node(cin) or self._is_filtered_node(cp) or cin == cp:
                continue
            kept = self._scope_consumption_row(cin, cp, value, region)
            if kept is not None:
                rows.append(kept)

        for _, row in production.iterrows():
            cp, cout, value = row["cp"], row["cout"], row["value"]
            if self._is_filtered_node(cp) or self._is_filtered_node(cout) or cp == cout:
                continue
            kept = self._scope_production_row(cp, cout, value, region)
            if kept is not None:
                rows.append(kept)

        return pd.DataFrame(rows, columns=["source", "target", "value"])

    @staticmethod
    def _is_filtered_node(name: str) -> bool:
        return name == "Dummy" or name.startswith("Help_")

    def _scope_consumption_row(
        self, cin: str, cp: str, value: float, region: int
    ) -> Optional[tuple[str, str, float]]:
        parsed_cp = _parse_name(cp)
        parsed_cin = _parse_name(cin)

        if parsed_cp.is_pipe:
            r_in, r_out = parsed_cp.regions
            if region == r_in:
                return (cin, cp, value)
            if region == r_out:
                # Pipe's input is in a foreign region; represent that side with a virtual node.
                return (f"from Region {r_in}", cp, value)
            return None

        cp_region = parsed_cp.regions[0] if parsed_cp.regions else None
        cin_region = parsed_cin.regions[0] if parsed_cin.regions else None
        if cp_region is not None and cp_region != region:
            return None
        if cin_region is not None and cin_region != region:
            return None
        return (cin, cp, value)

    def _scope_production_row(
        self, cp: str, cout: str, value: float, region: int
    ) -> Optional[tuple[str, str, float]]:
        parsed_cp = _parse_name(cp)
        parsed_cout = _parse_name(cout)

        if parsed_cp.is_pipe:
            r_in, r_out = parsed_cp.regions
            if region == r_in:
                # Pipe outputs into a foreign region - export node.
                return (cp, f"to Region {r_out}", value)
            if region == r_out:
                return (cp, cout, value)
            return None

        cp_region = parsed_cp.regions[0] if parsed_cp.regions else None
        cout_region = parsed_cout.regions[0] if parsed_cout.regions else None
        if cp_region is not None and cp_region != region:
            return None
        if cout_region is not None and cout_region != region:
            return None
        return (cp, cout, value)

    # -- validators --

    def _check_year(self, year: int) -> None:
        years = [int(y) for y in (self.dao.get_set("year") or [])]
        if year not in years:
            raise PlotterExeption(f"Year {year} not in model! Known years: {years}")