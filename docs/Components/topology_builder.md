# TopologyBuilder

A `TopologyBuilder` turns a street-network GeoDataFrame into a **region
graph** that all later pipeline stages consume. Two implementations
share the same fluent setter interface and differ only in *how regions
are defined*. `.build()` returns a `TopologyBuildResult` namedtuple
with `network` (a `networkx.Graph`), `region_topologies`, and the
(possibly splitted) `streets` GeoDataFrame.

`set_extensive_columns(...)` lists demand columns whose values are
length-additive and must be **rescaled** when a street is split into
multiple segments (e.g. the heat demand of a 200 m segment cut into
80 m + 120 m). Intensive columns (densities, temperatures) are left
untouched.

### `streets.geojson` (shared input)

User-supplied, required by both builders. Must contain a geometry
column of `LineString` features. All other columns are
application-specific — at minimum you'll want at least one demand
column (e.g. `waerme_mwh` in kWh) and a stable street ID column to
reference from the region-definition input.

## SimpleTopologyBuilder

Regions are listed explicitly in a YAML file: each region ID maps to a
column name and the list of values to match. A street belongs to
region *k* iff its row matches one of the values listed under *k*.
Use this when you already know the region membership of every street
and want full control without relying on geometry.

### API

| Setter | Type | Default | Description |
|---|---|---|---|
| `set_streets_data` | `GeoDataFrame` or `Path` | — (required) | Street network. A `Path` is loaded via `geopandas.read_file`. |
| `set_grouping` | `dict` or `Path` | — (required) | Region grouping (see `region_grouping.yaml` below). A `Path` is loaded as YAML. |
| `set_extensive_columns` | `list[str]` | `[]` | Length-additive columns to rescale when streets are split. |
| `set_region_id_column` | `str` | `"region"` | Name of the column written into `streets` carrying the region ID. |
| `set_default_region` | `Any` | `None` | Value assigned to streets that match no group. |
| `set_default_regions_in_topology` | `bool` | `False` | If `True`, edges in the default region are included in `region_topologies`. |

```python
from pathlib import Path
from geopipe.topology_builder.topology_builder import SimpleTopologyBuilder

tb = SimpleTopologyBuilder()
tb.set_streets_data(Path("input/streets.geojson"))
tb.set_extensive_columns(["waerme_mwh"])
tb.set_grouping(Path("input/region_grouping.yaml"))
result = tb.build()
```

### `region_grouping.yaml`

Maps **region ID → column name → list of values to match**. Each region
is the union of street rows whose `street_id` is in the list:

```yaml
0:
  street_id:
    - DEHE04620001hKyX
    - DEHE04620001hKzm
1:
  street_id:
    - DEHE04620001hKzc
```

Use the special key `index` instead of a column name to match raw row
indices.

## PolygonTopologyBuilder

Regions are derived by spatial join from a polygon GeoJSON: every
street that falls inside polygon *k* is assigned to region *k*, and
streets crossing polygon boundaries are split. The region ID is read
from the column passed to `set_region_id_column`. Use this when
regions are defined by area geometries rather than explicit street
lists.

### API

| Setter | Type | Default | Description |
|---|---|---|---|
| `set_streets_data` | `GeoDataFrame` or `Path` | — (required) | Street network. A `Path` is loaded via `geopandas.read_file`. |
| `set_polygons_data` | `GeoDataFrame` or `Path` | — (required) | Polygon layer defining the regions (see `polygons.geojson` below). |
| `set_region_id_column` | `str` | `"region"` | Column on `polygons_data` carrying the region ID; also the column written into `streets`. |
| `set_extensive_columns` | `list[str]` | `[]` | Length-additive columns to rescale when streets are split at polygon boundaries. |
| `set_streets_geometry_column_name` | `str` | auto-detects `"geometry"` / `"geom"` | Geometry column name on `streets_data`. |
| `set_polygons_geometry_column_name` | `str` | auto-detects `"geometry"` / `"geom"` | Geometry column name on `polygons_data`. |
| `set_default_region` | `Any` | `None` | Value assigned to streets that fall in no polygon. |
| `set_default_regions_in_topology` | `bool` | `False` | If `True`, edges in the default region are included in `region_topologies`. |

```python
from pathlib import Path
from geopipe.topology_builder.topology_builder import PolygonTopologyBuilder

tb = PolygonTopologyBuilder()
tb.set_streets_data(Path("input/streets.geojson"))
tb.set_region_id_column("id")
tb.set_extensive_columns(["waerme_mwh"])
tb.set_polygons_data(Path("input/polygons.geojson"))
result = tb.build()
```

### `polygons.geojson`

Standard GeoJSON with polygon geometries. The region ID is read from
whatever column you pass to `set_region_id_column` (default `"id"`).
Streets are assigned to region *k* iff their geometry intersects
polygon *k*; streets crossing polygon boundaries are split.
