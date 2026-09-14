# TopologyBuilder

A `TopologyBuilder` turns the streets of the `DataRegistry` into a
**street network** split into **region topologies**, which all later
pipeline stages consume. Two implementations differ only in *how regions
are defined*:

- [`PolygonTopologyBuilder`](#polygontopologybuilder) — regions are
  derived from a polygon layer.
- [`SimpleTopologyBuilder`](#simpletopologybuilder) — regions are listed
  explicitly (street ids or row indices per region).

Streets are registered — and optionally cleaned — with
[`DataRegistry.register_streets`](data_registry.md#streets).

```python
from geopipe.topology_builder.topology_builder import (
    SimpleTopologyBuilder, PolygonTopologyBuilder,
)
```

## What `build()` does

1. **Query the streets** for the builder's area from the registry
   (`DataRegistry.streets(area)`): the polygons, or the area set with
   `set_area`.
2. **Assign regions** — every street gets a region id (column and edge
   attribute `region_id`); streets outside all regions get none.
3. **Build the street network** — the streets are converted into a
   `networkx.Graph` (see [Network structure](#network-structure)).
4. **Split into region topologies** — one sub-graph per region. Streets
   without a region stay in `network` only; connections between regions
   may run over them.
5. **Check connectivity** — every region topology must be a single
   connected graph (`set_topology_connections_check`, on by default).

## Common API

| Setter | Arguments | Default | Description |
|---|---|---|---|
| `set_data_registry` | `data_registry: DataRegistry` | — (required) | Source of the streets; its CRS is the project CRS. |
| `set_topology_connections_check` | `enabled: bool` | `True` | Raise a `ValueError` if a region topology is not connected, listing the ids of the streets not connected to its largest group. |
| `build` | — | — | Run the steps above and return a `TopologyBuildResult`. |

### `TopologyBuildResult`

`build()` returns a dataclass (`geopipe.topology_builder.TopologyBuildResult`):

| Field | Type | Description |
|---|---|---|
| `network` | `networkx.Graph` | The full street network, including streets without a region. Pass this to `EnergySystemBuilder.set_system_topology`. |
| `region_topologies` | `dict[Any, networkx.Graph]` | One sub-graph per region id. |
| `streets` | `GeoDataFrame` | The streets with the `region_id` column. |

### Network structure

- **Nodes** are `(x, y)` coordinate tuples in the project CRS.
- **Edges** connect each pair of consecutive vertices of a street
  geometry, i.e. a street with *n* vertices becomes *n − 1* edges.
  Streets are only connected where they share a vertex (see
  [divide at junctions](data_registry.md#cleaning-steps)).
- **Edge attributes**:
    - `length` — Euclidean length of the edge in CRS units,
    - `geometry` — the geometry of the street the edge belongs to,
    - `region_id` — the edge's region (`None` outside all regions),
    - `source_street_id`, `source_share` — the input street the edge
      stems from and the edge's share of that street's length.
      Street-keyed data (`StreetValueDataset`) is mapped onto regions
      with them,
    - every other column of the streets, copied unchanged. Values such
      as demands are *not* split onto the edges — use a
      `StreetValueDataset` for them.
- The graph's CRS is stored in `network.graph["crs"]`.

## PolygonTopologyBuilder

Every street that lies completely **within** polygon *k* belongs to
region *k*. The polygons are also the area whose streets are queried.

- Streets are **not** split at polygon boundaries: a street crossing a
  boundary belongs to no region.
- A street lying within more than one (overlapping) polygon raises a
  `ValueError` listing the affected streets and polygons.

| Setter | Arguments | Default | Description |
|---|---|---|---|
| `set_polygons_data` | `polygons_data: GeoDataFrame \| Path`, `id_column: str` | — (required) | Polygon layer (`Polygon` / `MultiPolygon`); `id_column` holds the region id. A `Path` is loaded via `geopandas.read_file`; the polygons are reprojected to the registry CRS. |

```python
from pathlib import Path
from geopipe.topology_builder.topology_builder import PolygonTopologyBuilder

tb = PolygonTopologyBuilder()
tb.set_data_registry(data_reg)
tb.set_polygons_data(Path("input/polygons.geojson"), id_column="id")
result = tb.build()
```

## SimpleTopologyBuilder

Regions are listed explicitly in a YAML file or dict: each region ID
maps to a column name and the list of values to match. A street belongs
to region *k* iff its value in that column is one of the values listed
under *k*. Use this when you already know the region membership of
every street.

| Setter | Arguments | Default | Description |
|---|---|---|---|
| `set_area` | `area: GeoDataFrame \| Path` | — (required) | Study area whose streets are queried from the registry. |
| `set_grouping` | `grouping: dict \| Path` | — (required) | Region grouping (see [`region_grouping.yaml`](#region_groupingyaml)). A `Path` is loaded as YAML. |

```python
from pathlib import Path
from geopipe.topology_builder.topology_builder import SimpleTopologyBuilder

tb = SimpleTopologyBuilder()
tb.set_data_registry(data_reg)
tb.set_area(Path("input/study_area.geojson"))
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

The grouping is applied to the *registered* streets: if they were
[cleaned](data_registry.md#cleaning-steps), split pieces and connectors
carry new IDs and the row index is reset, so they no longer match the
original IDs/indices.
