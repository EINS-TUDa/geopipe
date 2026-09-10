# TopologyBuilder

A `TopologyBuilder` turns a street-network GeoDataFrame into a **region
graph** that all later pipeline stages consume. Two implementations
share the same fluent setter interface and differ only in *how regions
are defined*:

- [`SimpleTopologyBuilder`](#simpletopologybuilder) — regions are listed
  explicitly (street IDs or row indices per region).
- [`PolygonTopologyBuilder`](#polygontopologybuilder) — regions are
  derived from a polygon layer.

Both live in `geopipe.topology_builder.topology_builder`:

```python
from geopipe.topology_builder.topology_builder import (
    SimpleTopologyBuilder, PolygonTopologyBuilder,
)
```

Configure a builder with its `set_*` methods, then call `.build()`, which
returns a [`TopologyBuildResult`](#topologybuildresult).

## What `build()` does

`build()` runs the following steps in order:

1. **Validate input** — streets data, `id_column` and at least one
   extensive column must be set, plus the builder-specific input
   (grouping or polygons). Raises `TypeError` / `ValueError` otherwise.
2. **Clean the street geometry** (all steps optional, see
   [Geometry cleaning](#geometry-cleaning)):
    1. divide segments at junctions (`set_divide_at_junctions`),
    2. close dead-end gaps (`set_gap_distance`),
    3. drop isolated segments without demand (`set_drop_isolated_null_segments`).
3. **Assign regions** — every street is first given the default region
   (`set_default_region`), then the builder-specific rule assigns the
   actual region ID into the column named by `set_region_id_column`.
4. **Build the street network** — the streets are converted into a
   `networkx.Graph` (see [Network structure](#network-structure)).
5. **Split into region topologies** — one sub-graph per region ID.
   Edges in the default region are skipped unless
   `set_default_regions_in_topology(True)`.
6. **Check connectivity** — every region topology must be a single
   connected graph (`set_topology_connections_check`, on by default).

Because regions are assigned *after* cleaning, they are derived from the
cleaned geometry.

## Common API

These setters are available on both builders. Each returns the builder,
so calls can be chained.

| Setter | Arguments | Default | Description |
|---|---|---|---|
| `set_streets_data` | `streets_data: GeoDataFrame \| Path`, `id_column: str` | — (required) | Street network (see [`streets.geojson`](#streetsgeojson-shared-input)). A `Path` is loaded via `geopandas.read_file`. `id_column` names a column with a stable per-street identifier; it is used to name split/connector segments and to report disconnected streets. |
| `set_extensive_columns` | `extensive_columns: list[str]` | — (required, at least one) | Length-additive columns (e.g. heat demand totals) that are split by length share whenever a street is divided into several segments or edges. All other columns are copied unchanged. `build()` raises `ValueError` if none are given. |
| `set_region_id_column` | `region_id_column: str` | `"region"` | Name of the column written into `streets` (and onto every graph edge) carrying the region ID. For `PolygonTopologyBuilder` it is also the column read from the polygons. An existing column of that name is overwritten (with a warning). |
| `set_default_region` | `default_region: Any` | `None` | Region ID assigned to streets that match no region. |
| `set_default_regions_in_topology` | `default_regions_in_topology: bool` | `False` | If `True`, edges in the default region form their own entry in `region_topologies`; otherwise they are only part of `network`. |
| `set_divide_at_junctions` | `enabled: bool`, `tol: float = 1e-6` | `False`, `1e-6` | Split streets at T-junctions and mid-segment crossings so the streets share a graph node. `tol` (CRS units) is the max distance for a point to count as lying on a street. See [Divide at junctions](#divide-at-junctions). |
| `set_gap_distance` | `gap_distance: float \| None` | `None` (off) | Max distance (CRS units) for bridging a dead-end to the nearest street it is not yet connected to. `None` disables gap closing. See [Close dead-end gaps](#close-dead-end-gaps). |
| `set_drop_isolated_null_segments` | `drop_isolated_null: bool` | `False` | Drop street segments outside the largest connected component whose extensive columns are all NULL. See [Drop isolated segments](#drop-isolated-segments-without-demand). |
| `set_topology_connections_check` | `enabled: bool` | `True` | Check that each region topology is connected and raise `ValueError` if not. See [Connectivity check](#connectivity-check). |
| `build` | — | — | Run the pipeline above and return a `TopologyBuildResult`. |

### `streets.geojson` (shared input)

User-supplied, required by both builders. Must contain a geometry
column of `LineString` (or `MultiLineString`) features. All other
columns are application-specific — at minimum you need

- one or more demand columns (e.g. `waerme_mwh`), passed to
  `set_extensive_columns`, and
- a stable street ID column (e.g. `fid`), passed as `id_column` to
  `set_streets_data`.

Use a projected CRS in metres: edge lengths and all tolerances/distances
are measured in CRS units.

### `TopologyBuildResult`

`build()` returns a dataclass (`geopipe.topology_builder.TopologyBuildResult`)
with three fields:

| Field | Type | Description |
|---|---|---|
| `network` | `networkx.Graph` | The full street network, including edges in the default region. Pass this to `EnergySystemBuilder.set_system_topology`. |
| `region_topologies` | `dict[Any, networkx.Graph]` | One sub-graph per region ID (the default region only if `set_default_regions_in_topology(True)`). |
| `streets` | `GeoDataFrame` | The cleaned streets, with the region column added. May contain more rows than the input (junction splits, gap connectors) or fewer (dropped isolated segments). |

```python
result = tb.build()
result.network                 # nx.Graph of the whole study area
result.region_topologies[0]    # nx.Graph of region 0
result.streets.plot(column="region")
```

### Network structure

- **Nodes** are `(x, y)` coordinate tuples in the CRS of the streets.
- **Edges** connect each pair of consecutive vertices of a street
  geometry, i.e. a street with *n* vertices becomes *n − 1* edges.
  Streets are only connected where they share a vertex (see
  [Divide at junctions](#divide-at-junctions)).
- **Edge attributes**:
    - `length` — Euclidean length of the edge in CRS units,
    - `geometry` — the geometry of the street the edge belongs to,
    - every column of `streets` (including the region column and
      `id_column`). Extensive columns are scaled by the edge's share of
      the street length, so summing them over a street's edges recovers
      the street's value.
- The graph's CRS is stored in `network.graph["crs"]`.

## Geometry cleaning

All cleaning steps are off by default. They run in the order listed,
before regions are assigned.

### Divide at junctions

Enable with `set_divide_at_junctions(True, tol=1e-6)`.

Two streets are only connected in the graph if they share a vertex. This
step finds places where streets meet *without* a shared vertex and cuts
them there:

- **T-junctions** — one street ends on (or has a vertex within `tol` of)
  the interior of another; the crossed street is split at that point.
- **Mid-segment crossings** — two streets cross in each other's interior;
  *both* streets are split at the crossing.

Each piece becomes its own row. Its ID is `f"{k * 100}{old_id}"` for the
*k*-th piece (a two-way split of street `42` yields `10042` and
`20042`, as strings), and its extensive columns are divided by length
share. Streets that already share an endpoint are left untouched;
collinear overlaps are ignored.

### Close dead-end gaps

Enable with `set_gap_distance(distance)`.

Every dead-end (a node with degree 1) is connected to the closest point
on the nearest street within `distance`, excluding its own street and
the streets at its neighbouring junction. The connection point is
inserted as a vertex into the target street, and a short connector
street is appended to `streets`:

- its ID is `f"000{target_id}"` (the ID of the street it connects to),
- all other attributes, including the extensive columns, are `NaN`
  (connectors carry no demand).

Two dead-ends facing each other are bridged only once.

### Drop isolated segments without demand

Enable with `set_drop_isolated_null_segments(True)`.

After gap closing, streets that are not part of the largest connected
component of the whole network are dropped **if all of their extensive
columns are NULL**. Streets with demand are always kept.

## Connectivity check

Enabled by default (`set_topology_connections_check(True)`). After
building, each region topology must be a single connected graph. If not,
`build()` logs and raises a `ValueError` that lists, per region, the
number of connected groups and the IDs (`id_column`) of all streets not
connected to the largest group. Fix the data, enable the cleaning steps
above, or disable the check with `set_topology_connections_check(False)`.

## SimpleTopologyBuilder

Regions are listed explicitly in a YAML file or dict: each region ID
maps to a column name and the list of values to match. A street belongs
to region *k* iff its value in that column is one of the values listed
under *k*. Use this when you already know the region membership of
every street and want full control without relying on geometry.

### Additional API

| Setter | Arguments | Default | Description |
|---|---|---|---|
| `set_grouping` | `grouping: dict \| Path` | — (required) | Region grouping (see [`region_grouping.yaml`](#region_groupingyaml)). A `Path` is loaded as YAML. |

```python
from pathlib import Path
from geopipe.topology_builder.topology_builder import SimpleTopologyBuilder

tb = SimpleTopologyBuilder()
tb.set_streets_data(Path("input/streets.geojson"), id_column="street_id")
tb.set_extensive_columns(["waerme_mwh"])
tb.set_grouping(Path("input/region_grouping.yaml"))
result = tb.build()
```

### `region_grouping.yaml`

Maps **region ID → column name → list of values to match**. Region IDs
must be integers, column names strings and the values lists (otherwise
`build()` raises `TypeError`). Each region is the union of street rows
whose `street_id` is in the list:

```yaml
0:
  street_id:
    - DEHE04620001hKyX
    - DEHE04620001hKzm
1:
  street_id:
    - DEHE04620001hKzc
```

Use the special key `index` instead of a column name to match row
indices. If a street matches several regions, the last one wins.

Note that the grouping is applied to the *cleaned* streets: if
[Divide at junctions](#divide-at-junctions) or
[gap closing](#close-dead-end-gaps) is enabled, split pieces and
connectors carry new IDs and the row index is reset, so they no longer
match the original IDs/indices and end up in the default region.

## PolygonTopologyBuilder

Regions are derived from a polygon layer: every street that lies
completely **within** polygon *k* is assigned to region *k*. The region
ID is read from the polygon column named by `set_region_id_column`.
Use this when regions are defined by area geometries rather than
explicit street lists.

- Streets are **not** split at polygon boundaries: a street crossing a
  boundary lies in no polygon and gets the default region.
- A street lying within more than one (overlapping) polygon raises a
  `ValueError` listing the affected streets and polygons.
- If the CRS of the polygons differs from the streets, the polygons are
  reprojected to the streets' CRS.

### Additional API

| Setter | Arguments | Default | Description |
|---|---|---|---|
| `set_polygons_data` | `polygons_data: GeoDataFrame \| Path` | — (required) | Polygon layer defining the regions (see [`polygons.geojson`](#polygonsgeojson)). A `Path` is loaded via `geopandas.read_file`. |
| `set_streets_geometry_column_name` | `street_geometry_column_name: str` | auto-detects `"geometry"` / `"geom"` | Geometry column name on the streets data. |
| `set_polygons_geometry_column_name` | `polygon_geometry_column_name: str` | auto-detects `"geometry"` / `"geom"` | Geometry column name on the polygons data. |

```python
from pathlib import Path
from geopipe.topology_builder.topology_builder import PolygonTopologyBuilder

tb = PolygonTopologyBuilder()
tb.set_streets_data(Path("input/streets.geojson"), id_column="street_id")
tb.set_region_id_column("id")
tb.set_extensive_columns(["waerme_mwh"])
tb.set_polygons_data(Path("input/polygons.geojson"))
result = tb.build()
```

### `polygons.geojson`

Standard GeoJSON with `Polygon` / `MultiPolygon` geometries and one
column holding the region ID. Pass that column's name to
`set_region_id_column` (the default is `"region"`; the example above
uses `"id"`).
