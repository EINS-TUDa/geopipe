# coding: utf-8
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import yaml
from typing import Any
import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely import LineString, MultiLineString, Point
from shapely.ops import nearest_points

import logging

logger = logging.getLogger(__name__)


def modify_streets_data(streets_data: gpd.GeoDataFrame | Path,
                        modifications_file: Path, ) -> gpd.GeoDataFrame:
    """Modify the data of a geopandas GeoDataFrame according to the specifications in a YAML file.

    The yaml file mus have the following structure. The elements in the GeoDataFrame is described with a column name
    and a row specifier which can be either the row index or the entry in another column.
    For each entry, there are two actions supported: add and replace.

    .. code-block:: yaml
        column_name:  # Name of the column where to change
          - index: 0  # Index of the row where to change
            add: 50
          - column_name: value_in_column  # Specifier of the row where to change, e.g. column_name: value_in_column
            replace: 100

    Parameters
    ----------
    streets_data : gpd.GeoDataFrame | Path
        A GeoDataFrame containing the street data or a path to a file that can be read into a GeoDataFrame.
    modifications_file : Path
        A path to a YAML file that specifies the modifications to be made to the GeoDataFrame.

    Returns
    -------
    modified_streets_data : gpd.GeoDataFrame
    """
    if isinstance(streets_data, Path):
        streets_data = gpd.read_file(streets_data)
    streets_data: gpd.GeoDataFrame

    modifications_data = load_yaml(modifications_file)

    for column_name, mod_data_per_street in modifications_data.items():
        column_name: str
        mod_data_per_street: list[dict[str, Any]]
        for mod_data in mod_data_per_street:
            mod_data: dict[str, Any]
            if len(mod_data) != 2:
                raise ValueError()

            add_value = mod_data.pop("add", None)
            replace_value = mod_data.pop("replace", None)

            if add_value is None and replace_value is None:
                raise ValueError()
            if add_value is not None and replace_value is not None:
                raise ValueError()

            [(key, identifier)] = mod_data.items()
            if key == "index":
                row_specifier = streets_data.index == identifier
            else:
                row_specifier = streets_data[key] == identifier

            value = streets_data.loc[row_specifier, column_name].iloc[0]

            if add_value is not None:
                value = value + add_value
            if replace_value is not None:
                value = replace_value

            logger.info("Modifying column '%s' for rows where '%s' is '%s': %s -> %s",
                        column_name, key, identifier, streets_data.loc[row_specifier, column_name].iloc[0], value)

            streets_data.loc[row_specifier, column_name] = value

    return streets_data


def load_yaml(config_file: Path) -> dict[str, Any]:
    if not config_file.exists():
        raise FileNotFoundError(f"Scenario file not found: {config_file}")
    with config_file.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping root in {config_file}, got {type(data).__name__}")
    return data


@dataclass
class TopologyBuildResult:
    network: nx.Graph
    region_topologies: dict[int, nx.Graph]
    streets: gpd.GeoDataFrame


class TopologyBuildError(RuntimeError):
    """Raised when topology building fails."""


def gdf_to_nx(gdf: gpd.GeoDataFrame, extensive_columns: list[str] | None = None) -> nx.Graph:
    """Convert line-segment GeoDataFrame rows into a street graph.

    Each row's geometry is split into edges between consecutive vertices. Extensive
    columns (length-additive, e.g. demand totals) are distributed across the row's
    segments proportionally to segment length, so summing across edges recovers the
    original row value. Non-extensive attributes are copied verbatim onto every segment.
    """
    graph: nx.Graph = nx.Graph()
    if hasattr(gdf, "crs") and gdf.crs is not None:
        graph.graph["crs"] = gdf.crs

    extensive_columns = set(extensive_columns or ())

    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None:
            continue

        attrs = {col: row[col] for col in gdf.columns if col != "geometry"}

        if geom.geom_type == "LineString":
            lines = [geom]
        elif geom.geom_type == "MultiLineString":
            lines = list(geom.geoms)
        else:
            continue

        segments = []
        for line in lines:
            coords = list(line.coords)
            for idx in range(len(coords) - 1):
                u = (float(coords[idx][0]), float(coords[idx][1]))
                v = (float(coords[idx + 1][0]), float(coords[idx + 1][1]))
                length = ((v[0] - u[0]) ** 2 + (v[1] - u[1]) ** 2) ** 0.5
                segments.append((u, v, line, length))

        total_length = sum(seg_length for _, _, _, seg_length in segments)
        for u, v, line, length in segments:
            seg_attrs = dict(attrs)
            if extensive_columns and total_length > 0:
                share = length / total_length
                for col in extensive_columns:
                    if col in seg_attrs:
                        seg_attrs[col] = seg_attrs[col] * share
            graph.add_edge(u, v, geometry=line, length=length, **seg_attrs)

    return graph


def _node_of(geom) -> tuple[float, float] | None:
    """Return the first vertex of ``geom`` as a graph-node coordinate tuple.

    Matches the node coordinates produced by :func:`gdf_to_nx`, so the result can be
    looked up directly in a graph built from the same GeoDataFrame.
    """
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "MultiLineString":
        coords = geom.geoms[0].coords
    elif geom.geom_type == "LineString":
        coords = geom.coords
    return (float(coords[0][0]), float(coords[0][1]))


def _all_nodes(geom):
    """Yield every vertex of ``geom`` as a graph-node coordinate tuple."""
    lines = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
    for line in lines:
        if line.geom_type != "LineString":
            continue
        for x, y in line.coords:
            yield (float(x), float(y))


def _insert_node(geom, point: Point, tol: float):
    """Return ``geom`` with ``point`` inserted as a vertex at its projected position.

    Handles LineString and MultiLineString (the point is inserted into the nearest
    component). The inserted vertex becomes a graph node once the geometry is rebuilt
    with :func:`gdf_to_nx`. If the projection coincides (within ``tol``) with an existing
    vertex, the geometry is returned unchanged so no zero-length segment is created.
    """
    if geom.geom_type == "MultiLineString":
        parts = list(geom.geoms)
        nearest_i = min(range(len(parts)), key=lambda i: parts[i].distance(point))
        parts[nearest_i] = _insert_node_into_line(parts[nearest_i], point, tol)
        return MultiLineString(parts)
    return _insert_node_into_line(geom, point, tol)


def _insert_node_into_line(line: LineString, point: Point, tol: float) -> LineString:
    """Insert ``point`` as a vertex into a single LineString at its projected position."""
    coords = list(line.coords)
    d = line.project(point)

    # already at (or beyond) an endpoint -> nothing to insert
    if d <= tol or d >= line.length - tol:
        return line

    cum = 0.0
    for i in range(len(coords) - 1):
        seg_len = Point(coords[i]).distance(Point(coords[i + 1]))
        if d < cum + seg_len - tol:
            if d <= cum + tol:
                return line  # coincides with existing vertex coords[i]
            return LineString(coords[:i + 1] + [(point.x, point.y)] + coords[i + 1:])
        cum += seg_len

    return line


def close_dead_end_gaps(streets: gpd.GeoDataFrame, search_radius: float,
                        extensive_columns: list[str] | None = None,
                        snap_tol: float = 1e-6,
                        id_column: str | None = None) -> gpd.GeoDataFrame:
    """Bridge gaps by connecting dead-end nodes to nearby streets.

    A dead-end (degree-1 node) is connected to the closest point on the nearest street
    within ``search_radius``, *except* its own segment and the segments meeting it at the
    far (neighbour) junction. A path may already exist between the two: dead-ends near
    another street are typically digitisation gaps that should be closed regardless.

    The connection point is inserted as a vertex into the target street so it becomes a
    shared graph node on rebuild, and a short connector street is appended. Connectors
    carry no demand: their ``extensive_columns`` are set to NaN.

    Two dead-ends facing each other are bridged only once: a connector between a pair of
    segments suppresses the reverse connector, so no duplicates are produced.
    """
    extensive_columns = list(extensive_columns or [])
    streets = streets.copy().reset_index(drop=True)
    geom_col = streets.geometry.name

    graph = gdf_to_nx(streets, extensive_columns=extensive_columns)
    sindex = streets.sindex

    # map every vertex to the street rows that own it, to identify a dead-end's own
    # segment and the segments connected at its neighbour junction
    node_to_positions: dict[tuple[float, float], set[int]] = {}
    for pos, geom in enumerate(streets.geometry):
        for node in _all_nodes(geom):
            node_to_positions.setdefault(node, set()).add(pos)

    dead_ends = sorted(node for node, degree in graph.degree() if degree == 1)

    bridged_pairs: set[frozenset[int]] = set()
    new_rows = []
    for node in dead_ends:
        node_pt = Point(node)

        # own segment + segments sharing the dead-end's neighbour junction
        own_positions = node_to_positions.get(node, set())
        neighbour = next(graph.neighbors(node))
        excluded = own_positions | node_to_positions.get(neighbour, set())

        best_pos = None
        best_dist = float("inf")
        for pos in sindex.query(node_pt.buffer(search_radius)):
            if pos in excluded:
                continue
            dist = node_pt.distance(streets.geometry.iloc[pos])
            if dist < best_dist:
                best_dist = dist
                best_pos = pos

        if best_pos is None or best_dist >= search_radius:
            continue

        # facing dead-ends share the same segment pair -> bridge it only once
        pair = frozenset(own_positions | {best_pos})
        if pair in bridged_pairs:
            continue
        bridged_pairs.add(pair)

        target_geom = streets.geometry.iloc[best_pos]
        snap_pt = nearest_points(node_pt, target_geom)[1]

        # make the connection point a graph node by inserting it into the target street
        streets.iat[best_pos, streets.columns.get_loc(geom_col)] = _insert_node(
            target_geom, snap_pt, snap_tol)

        # connector: all attrs None, id_column gets "000" prefix of target segment id
        row = {col: float("nan") for col in streets.columns if col != geom_col}
        row[geom_col] = LineString([node, (snap_pt.x, snap_pt.y)])
        if id_column and id_column in streets.columns:
            target_id = streets.iloc[best_pos][id_column]
            row[id_column] = f"000{target_id}"
        new_rows.append(row)

        logger.info("Closed gap at dead-end %s -> nearest street (distance %.3f m)",
                    node, best_dist)

    if new_rows:
        connectors = gpd.GeoDataFrame(new_rows, geometry=geom_col, crs=streets.crs)
        streets = gpd.GeoDataFrame(
            pd.concat([streets, connectors], ignore_index=True),
            geometry=geom_col, crs=streets.crs)

    logger.info("Closed %d dead-end gaps", len(new_rows))
    return streets


def _intersection_points(geom_a, geom_b) -> list[Point]:
    """Return the point-like intersections of two geometries.

    Crossings and touches yield a Point or MultiPoint; collinear overlaps yield a
    line and are ignored (an overlap is a data issue, not a junction to split at).
    """
    inter = geom_a.intersection(geom_b)
    if inter.is_empty:
        return []
    if inter.geom_type == "Point":
        return [inter]
    if inter.geom_type == "MultiPoint":
        return list(inter.geoms)
    if inter.geom_type == "GeometryCollection":
        return [g for g in inter.geoms if g.geom_type == "Point"]
    return []


def _line_interior_contains(line, point: Point, tol: float) -> bool:
    """True if ``point`` lies on ``line`` strictly between the endpoints of a component."""
    parts = line.geoms if line.geom_type == "MultiLineString" else [line]
    for part in parts:
        if part.geom_type != "LineString":
            continue
        if part.distance(point) <= tol:
            d = part.project(point)
            if tol < d < part.length - tol:
                return True
    return False


def _dedupe_points(points: list[Point], tol: float) -> list[Point]:
    """Drop points that coincide (within ``tol``) with an earlier one."""
    unique: list[Point] = []
    for p in points:
        if not any(p.distance(q) <= tol for q in unique):
            unique.append(p)
    return unique


def _as_single_linestring(geom) -> LineString | None:
    """Return the lone LineString of ``geom``, or ``None`` if it is genuinely multi-part.

    Street data is commonly stored with each feature as a single-component
    ``MultiLineString``; such a geometry is treated as the LineString it wraps.
    """
    if geom.geom_type == "LineString":
        return geom
    if geom.geom_type == "MultiLineString" and len(geom.geoms) == 1:
        return geom.geoms[0]
    return None


def _split_line_at_points(line: LineString, points: list[Point],
                          tol: float) -> list[LineString]:
    """Cut ``line`` into sub-LineStrings at each of ``points`` (which lie on its interior)."""
    for p in points:
        line = _insert_node_into_line(line, p, tol)
    coords = list(line.coords)
    pieces: list[LineString] = []
    start = 0
    for i in range(1, len(coords) - 1):
        vertex = Point(coords[i])
        if any(vertex.distance(p) <= tol for p in points):
            pieces.append(LineString(coords[start:i + 1]))
            start = i
    pieces.append(LineString(coords[start:]))
    return pieces


def divide_segments_at_junctions(streets: gpd.GeoDataFrame,
                                 extensive_columns: list[str] | None = None,
                                 snap_tol: float = 1e-6,
                                 id_column: str | None = None) -> gpd.GeoDataFrame:
    """Split street segments at junctions and true mid-segment crossings.

    Two streets are graph-disconnected unless they share a vertex, because
    :func:`gdf_to_nx` only places graph nodes at vertices. This finds every point where
    streets meet without a shared vertex and cuts the segments there so the meeting point
    becomes a shared graph node:

    * **T-junctions** -- one street ends on (or has a vertex within ``snap_tol`` of) the
      interior of another; the crossed street is split at that point.
    * **Mid-segment crossings** -- two streets cross in each other's interior with no
      vertex at the intersection; *both* streets are split at the crossing.

    A crossed street is cut into separate rows. Each resulting piece gets a distinct id:
    ``f"{k * 100}{old_id}"`` for the k-th piece (so a two-way split yields ``100<id>`` and
    ``200<id>``), provided ``id_column`` is given. Extensive (length-additive) columns are
    divided across the pieces in proportion to their length; other columns are copied.

    ``snap_tol`` (CRS units) is the tolerance for a point to count as lying on a segment
    and below which a point coincides with an existing vertex (so no zero-length piece is
    produced). Streets sharing an endpoint are left untouched -- they already connect.
    """
    extensive_columns = list(extensive_columns or [])
    streets = streets.copy().reset_index(drop=True)
    geom_col = streets.geometry.name
    sindex = streets.sindex

    split_points: dict[int, list[Point]] = defaultdict(list)

    # crossings and exact touches between street pairs (catches mid-segment X-crossings)
    for i, gi in enumerate(streets.geometry):
        if gi is None:
            continue
        for j in sindex.query(gi):
            if j <= i:
                continue
            gj = streets.geometry.iloc[j]
            if gj is None:
                continue
            for p in _intersection_points(gi, gj):
                if _line_interior_contains(gi, p, snap_tol):
                    split_points[i].append(p)
                if _line_interior_contains(gj, p, snap_tol):
                    split_points[j].append(p)

    # vertices lying within tolerance of another street's interior (near-touch T-junctions)
    junction_points = {
        node for geom in streets.geometry if geom is not None
        for node in _all_nodes(geom)
    }
    for pt in junction_points:
        point = Point(pt)
        for pos in sindex.query(point.buffer(snap_tol)):
            geom = streets.geometry.iloc[pos]
            if geom is None or pt in set(_all_nodes(geom)):
                continue
            if geom.distance(point) > snap_tol:
                continue
            snap_pt = nearest_points(point, geom)[1]
            if _line_interior_contains(geom, snap_pt, snap_tol):
                split_points[pos].append(snap_pt)

    if not split_points:
        return streets

    output_rows: list[dict] = []
    split_count = 0
    for pos in range(len(streets)):
        base = streets.iloc[pos]
        geom = base[geom_col]
        points = _dedupe_points(split_points.get(pos, []), snap_tol)

        if not points or geom is None:
            output_rows.append(base.to_dict())
            continue

        line = _as_single_linestring(geom)
        if line is None:
            # a genuinely multi-part geometry cannot be cut into distinctly-identified
            # rows; just insert the junction points as vertices to preserve connectivity
            row = base.to_dict()
            for p in points:
                geom = _insert_node(geom, p, snap_tol)
            row[geom_col] = geom
            output_rows.append(row)
            continue

        pieces = _split_line_at_points(line, points, snap_tol)
        if len(pieces) <= 1:
            output_rows.append(base.to_dict())
            continue

        # keep the original geometry type so the output column stays homogeneous
        as_multi = geom.geom_type == "MultiLineString"

        total_length = sum(piece.length for piece in pieces)
        for k, piece in enumerate(pieces, start=1):
            row = base.to_dict()
            row[geom_col] = MultiLineString([piece]) if as_multi else piece
            if extensive_columns and total_length > 0:
                share = piece.length / total_length
                for col in extensive_columns:
                    if col in row and pd.notna(row[col]):
                        row[col] = row[col] * share
            if id_column and id_column in row:
                row[id_column] = f"0{k * 100}{base[id_column]}"
            output_rows.append(row)
        split_count += 1

    streets = gpd.GeoDataFrame(output_rows, geometry=geom_col, crs=streets.crs)
    logger.info("Divided %d street segment(s) at junctions and crossings", split_count)
    return streets


def drop_null_isolated_segments(streets: gpd.GeoDataFrame,
                                extensive_columns: list[str] | None = None
                                ) -> gpd.GeoDataFrame:
    """Drop isolated street segments that carry no demand.

    A segment is isolated if it lies in a connected component other than the largest.
    Such a segment is dropped only if *all* of its ``extensive_columns`` are NULL
    (NaN/None), i.e. it carries no demand. The largest component and any segment with
    demand are always kept.
    """
    extensive_columns = list(extensive_columns or [])
    streets = streets.copy().reset_index(drop=True)

    graph = gdf_to_nx(streets, extensive_columns=extensive_columns)
    components = sorted(nx.connected_components(graph), key=len, reverse=True)
    if len(components) <= 1:
        return streets

    main_nodes = components[0]

    def is_isolated(geom) -> bool:
        node = _node_of(geom)
        return node is not None and node not in main_nodes

    isolated_mask = streets.geometry.apply(is_isolated)
    if extensive_columns:
        null_mask = streets[extensive_columns].isna().all(axis=1)
    else:
        null_mask = False
    drop_mask = isolated_mask & null_mask

    logger.info("Dropping %d isolated segments with no demand", int(drop_mask.sum()))
    return streets.loc[~drop_mask].reset_index(drop=True)
