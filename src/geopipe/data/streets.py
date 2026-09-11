# coding: utf-8
"""Street data preparation: validation, original street ids and length shares, and optional geometry cleaning.

Streets enter the pipeline only through :meth:`DataRegistry.register_streets`, which calls :func:`prepare_streets`.
"""
import logging
from collections import defaultdict
from typing import Optional

import geopandas as gpd
import networkx as nx
import pandas as pd
from pyproj import CRS
from shapely import LineString, MultiLineString, Point
from shapely.ops import nearest_points

from ..topology_builder.topology import SOURCE_STREET_ID, SOURCE_SHARE
from ..topology_builder.topology_build_utils import gdf_to_nx

logger = logging.getLogger(__name__)


def prepare_streets(streets: gpd.GeoDataFrame, id_column: str, crs: CRS,
                    divide_at_junctions: bool = False, junction_tol: float = 1e-6,
                    gap_distance: Optional[float] = None,
                    drop_isolated_null_columns: Optional[list[str]] = None) -> gpd.GeoDataFrame:
    """Validate and reproject the streets, record each street's original id and length share, optionally clean them.

    Every street gets ``source_street_id`` (its ``id_column`` value) and ``source_share`` (1). The cleaning steps
    split both along with the streets, so street-keyed data can be mapped onto any regions.

    Cleaning steps, in this order, all off by default:

    * ``divide_at_junctions`` -- split streets at T-junctions and mid-segment crossings
      (:func:`divide_segments_at_junctions`); ``junction_tol`` is the tolerance in CRS units.
    * ``gap_distance`` -- bridge dead-ends to the nearest street within this distance (:func:`close_dead_end_gaps`).
    * ``drop_isolated_null_columns`` -- drop streets outside the largest connected component whose values in all
      of these columns are NULL (:func:`drop_null_isolated_segments`).
    """
    if streets.crs is None:
        raise ValueError("Streets data has no CRS")
    if id_column not in streets:
        raise ValueError(f"Streets id column '{id_column}' does not exist in the streets data")
    if streets[id_column].isna().any():
        raise ValueError(f"Streets id column '{id_column}' contains empty ids")
    if not streets[id_column].is_unique:
        raise ValueError(f"Street ids in column '{id_column}' are not unique")
    if not streets.geom_type.dropna().isin(["LineString", "MultiLineString"]).all():
        raise ValueError("Streets must be LineString or MultiLineString geometries")

    if streets.crs != crs:
        streets = streets.to_crs(crs)
    streets = streets.assign(**{SOURCE_STREET_ID: streets[id_column], SOURCE_SHARE: 1.0})

    if divide_at_junctions:
        streets = divide_segments_at_junctions(streets, [SOURCE_SHARE], junction_tol, id_column=id_column)
    if gap_distance is not None:
        streets = close_dead_end_gaps(streets, gap_distance, id_column=id_column)
    if drop_isolated_null_columns:
        streets = drop_null_isolated_segments(streets, drop_isolated_null_columns)
    return streets


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
                        snap_tol: float = 1e-6,
                        id_column: str | None = None) -> gpd.GeoDataFrame:
    """Bridge gaps by connecting dead-end nodes to nearby streets.

    A dead-end (degree-1 node) is connected to the closest point on the nearest street
    within ``search_radius``, *except* its own segment and the segments meeting it at the
    far (neighbour) junction. A path may already exist between the two: dead-ends near
    another street are typically digitisation gaps that should be closed regardless.

    The connection point is inserted as a vertex into the target street so it becomes a
    shared graph node on rebuild, and a short connector street is appended. All attributes
    of a connector are NaN, so it carries no data.

    Two dead-ends facing each other are bridged only once: a connector between a pair of
    segments suppresses the reverse connector, so no duplicates are produced.
    """
    streets = streets.copy().reset_index(drop=True)
    geom_col = streets.geometry.name

    graph = gdf_to_nx(streets)
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
                row[id_column] = f"{k * 100}{base[id_column]}"
            output_rows.append(row)
        split_count += 1

    streets = gpd.GeoDataFrame(output_rows, geometry=geom_col, crs=streets.crs)
    logger.info("Divided %d street segment(s) at junctions and crossings", split_count)
    return streets


def drop_null_isolated_segments(streets: gpd.GeoDataFrame, null_columns: list[str]) -> gpd.GeoDataFrame:
    """Drop isolated street segments that carry no data.

    A segment is isolated if it lies in a connected component other than the largest.
    Such a segment is dropped only if *all* of its ``null_columns`` are NULL (NaN/None).
    The largest component and any segment with data are always kept.
    """
    streets = streets.copy().reset_index(drop=True)

    graph = gdf_to_nx(streets)
    components = sorted(nx.connected_components(graph), key=len, reverse=True)
    if len(components) <= 1:
        return streets

    main_nodes = components[0]

    def is_isolated(geom) -> bool:
        node = _node_of(geom)
        return node is not None and node not in main_nodes

    isolated_mask = streets.geometry.apply(is_isolated)
    null_mask = streets[null_columns].isna().all(axis=1)
    drop_mask = isolated_mask & null_mask

    logger.info("Dropping %d isolated segments with no data", int(drop_mask.sum()))
    return streets.loc[~drop_mask].reset_index(drop=True)
