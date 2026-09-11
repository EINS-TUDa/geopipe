# DataRegistry

`DataRegistry` is the pipeline's central data-lookup point. Every
component that needs **regionalised data** — heat demand, electricity
demand profiles, technology shares, street networks — asks the
registry for it with a `DataRegistryQuery` (a *key* plus optional
*params*) and the region's *topology*; the registry returns the result
of the highest-priority dataset whose `scope` covers that
region.

This indirection means the rest of the pipeline does not need to know
whether data comes from a GeoJSON, a Postgres database, a hard-coded
constant, or a custom query function — only that some dataset has
registered against the key.

> **Status today.** `EnergySystemBuilder` resolves the demand values,
> profiles and technology shares of each `DemandType` through the
> registry. Street-network data will follow.

## Minimal usage

```python
import pathlib
from geopipe import DataRegistry
from geopipe.data.data_registry import DataKeys
from geopipe.data.dataset import FileDataset

heating_shares = FileDataset(
    keys=[DataKeys.HEATING_SHARES],
    file_path=str(pathlib.Path(__file__).parent / "Census2022HeatingType.geojson"),
    query_function=census_bensheim_query,  # see below
    priority=10,
    scope=None,  # None = applies everywhere
)

data_reg = DataRegistry(crs="EPSG:25832")
data_reg.register(heating_shares)
```

## Project CRS

The registry owns the project CRS; it is the single CRS of the whole
pipeline. Every registered dataset is reprojected to it once (on
registration, or on first load for file datasets), and the topology
builder reprojects streets and polygons to it. Query functions
therefore always receive data in the project CRS. Use a projected CRS in
metres — a warning is logged otherwise, since lengths and distances are
measured in CRS units.

## API

### `DataRegistry`

| Method | Signature | Description |
|---|---|---|
| `__init__` | `DataRegistry(crs: str \| pyproj.CRS)` | Sets the project CRS (see above). |
| `crs` | property → `pyproj.CRS` | The project CRS. |
| `query` | `query(topology, query: DataRegistryQuery, unit=None) -> Any` | Answers the query for one region topology (see below). |

### `DataRegistryQuery`

`DataRegistryQuery(key, params={})` — `key` selects the datasets;
`params` are dataset-specific options passed unchanged to the query
function (e.g. `{"name_mapping": {...}}`).

### Routing

The registry works on region topologies only; it derives no geometry
itself. `query` routes the region to the highest-priority dataset whose
`scope` covers **all** of the region's topology nodes. A dataset
covering only part of the study area thus answers for the regions
inside it, and the next dataset by priority for the rest. A region no
dataset covers raises a `LookupError`.

### Units

With `unit=`, a numeric result is converted from the dataset's `unit`
(e.g. `UnitEnum.KWH` → `UnitEnum.MWH`); a dataset without a unit is then
rejected. The `EnergySystemBuilder` requests demand values in the energy
unit of the energy system.
| `register` | `register(dataset: Dataset) -> None` | Adds a dataset. Lookups walk registered datasets in **descending priority** (then registration order) and return the first match. |

## Built-in keys (`DataKeys`)

| Key                                       | Returns                                                  |
| ----------------------------------------- | -------------------------------------------------------- |
| `RESIDENTIAL_HEAT_DEMAND_PROFILE`         | `pd.Series` of 8760 hourly values (normalised)           |
| `RESIDENTIAL_ELECTRICITY_DEMAND_PROFILE`  | same                                                     |
| `RESIDENTIAL_HEAT_DEMAND`                 | `float` total per region (in the dataset's `unit`)       |
| `RESIDENTIAL_ELECTRICITY_DEMAND`          | `float` total per region (in the dataset's `unit`)       |
| `HEATING_SHARES`                          | `dict[str, float]` summing to 1, after `name_mapping`    |
| `STREET_NETWORK`                          | `GeoDataFrame` with a `geom` `LineString` column         |
| `LINEAR_HEAT_DENSITY`                     | `GeoDataFrame` with `geom` and `heat_density_mwh_per_km` |

A single `Dataset` can advertise multiple keys via the `keys=[…]`
list.

## Datasets

Two built-ins are available out of the box: `FileDataset` (any file
that pandas/geopandas can read) and `PostgresDataset` (any Postgres
connection). Both call the optional `query_function(dataset, topology,
query) -> Any` if supplied, otherwise return the loaded data as-is.

### `FileDataset`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `keys` | `list[str]` | — (required) | Data keys this dataset can answer (typically values from `DataKeys`). |
| `file_path` | `str` | — (required) | Path to the source file (`.geojson` / `.gpkg` / `.shp` → geopandas, else pandas CSV). |
| `query_function` | `Callable` or `None` | `None` | Custom resolver with signature `(dataset, topology, query) -> Any`. If omitted, `query()` returns the loaded data. |
| `unit` | `UnitEnum` or `None` | `None` | Optional unit annotation for the returned data. |
| `load_data_kwargs` | `dict` or `None` | `None` | Extra kwargs forwarded to the underlying `read_file` / `read_csv`. |
| `priority` | `int` | `10` | Higher = preferred when multiple datasets can answer the same key. |
| `scope` | `GeoDataFrame` or `None` | `None` | Polygon of the area the dataset is valid for; `None` = applies everywhere. A region is routed to the dataset only if the polygon covers all of its topology nodes. |

### `StreetValueDataset`

Values per street (e.g. annual heat demand), keyed by the street ids of
the raw input streets (the topology builder's `id_column`). A region's
value is the sum over its edges of *value × the edge's share of its
source street's length*, so it does not depend on how regions are
defined.

```python
heat_demand = StreetValueDataset.from_column(
    streets, id_column="fid", value_column="waerme_mwh",
    keys=[DataKeys.RESIDENTIAL_HEAT_DEMAND], unit=UnitEnum.MWH)
```

### `PostgresDataset`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `keys` | `list[str]` | — (required) | Data keys this dataset can answer. |
| `db_connection` | `PostgresConnection` | — (required) | Connection used by `execute_spatial_query` / the query function. |
| `query_function` | `Callable` or `None` | — (required) | Resolver with signature `(dataset, topology, query) -> Any`. |
| `sql` | `str` or `None` | `None` | SQL executed by `fetch()`, with `:wkt` / `:epsg` placeholders for the region. |
| `unit` | `UnitEnum` or `None` | `None` | Optional unit annotation. |
| `priority` | `int` | `2` | Lower default than `FileDataset` so files win on ties. |
| `scope` | `GeoDataFrame` or `None` | `None` | Polygon of the area the dataset is valid for; `None` = applies everywhere. |

## Adding a Postgres data source

Wiring up a database source has three parts: credentials in `.env`, a
`PostgresConnection`, and a `PostgresDataset` that carries the SQL.

### 1. Credentials in `.env`

Credentials never live in code. Each database is one **prefix**, and
`PostgresConnection.from_env(prefix)` reads exactly five variables:
`{PREFIX}_HOST`, `{PREFIX}_PORT`, `{PREFIX}_DATABASE`, `{PREFIX}_USER`,
`{PREFIX}_PASSWORD`. All five are required; a missing one raises
`RuntimeError: Missing env var …`.

Copy `.env.template` to `.env` in the repository root and fill it in.
`.env` is git-ignored; `.env.template` is the committed, value-free
version — add a block there whenever you add a new database, so others
know which variables to set.

```dotenv
# --- INFDBGAUSS ---
INFDBGAUSS_HOST=db.example.org
INFDBGAUSS_PORT=5432
INFDBGAUSS_DATABASE=***REMOVED***
INFDBGAUSS_USER=***REMOVED***
INFDBGAUSS_PASSWORD=secret
```

Format rules — the file is parsed by `python-dotenv`, **not** by Python:

- One `KEY=value` per line, no trailing comma, no `;`.
- Do **not** quote values unless the value itself contains spaces or
  `#`. Quotes are stripped, so `PORT="5432"` works but adds nothing.
- No spaces around `=` (`KEY = value` is tolerated, but stick to the
  plain form).
- Lines starting with `#` are comments; blank lines are ignored.
- A value already present in the real environment wins over the `.env`
  entry — useful for overriding a single variable on a server or in CI.

Malformed lines are skipped with a `python-dotenv could not parse
statement starting at line N` warning, and the variable then shows up
as missing — if you get that warning, look for a stray comma or quote
on the reported line.

### 2. Connection and dataset

`from_env` locates the nearest `.env` starting from the current working
directory, so scripts run from anywhere inside the repository pick it up.

The `sql` string is executed by `fetch()` through
`execute_spatial_query`, which binds two parameters for you: `:wkt`
(the geometry passed to `fetch`, as WKT) and `:epsg` (its EPSG code). Use them in a
`ST_Intersects` clause so the database does the spatial filtering:

```python
from geopipe import DataRegistry
from geopipe.data import PostgresConnection, PostgresDataset
from geopipe.data.data_registry import DataKeys

db_conn = PostgresConnection.from_env("INFDBGAUSS")

heating_shares = PostgresDataset(
    keys=[DataKeys.HEATING_SHARES],
    db_connection=db_conn,
    query_function=census_bensheim_query,  # see below
    sql="""
        SELECT
            SUM(c."gas")::float     AS "Gas",
            SUM(c."heizoel")::float AS "Heizoel"
            -- … remaining carriers …
        FROM opendata.zensus_2022_100m_energietraeger_heizung AS c
        WHERE ST_Intersects(
            c.geom,
            ST_Transform(ST_GeomFromText(:wkt, :epsg), ST_SRID(c.geom))
        )
        """,
)

data_reg = DataRegistry(crs="EPSG:25832")
data_reg.register(heating_shares)
```

The same `db_conn` can back any number of datasets — it is shared, and
the SQLAlchemy engine is created lazily on first query. `is_available()`
opens a connection and runs `SELECT 1`, returning `False` instead of
raising if the server is unreachable, so a database dataset that is
temporarily down simply loses to a lower-priority `FileDataset`.

The `query_function` receives the raw query result via
`dataset.fetch(geometry, query)` (e.g. `topology.convex_hull`) and post-processes it exactly as it would
for a file-backed dataset — the same function can therefore serve both a
`FileDataset` and a `PostgresDataset` variant of the same data.

## Custom query function

When the default loader doesn't fit (e.g. you need to aggregate raw
census categories into named technologies), pass a `query_function`.
It receives the dataset, the region's `Topology` and the
`DataRegistryQuery`. How the topology becomes a spatial filter is up to
the function — e.g. `topology.convex_hull` (a one-row GeoDataFrame) for
an area-based lookup:

```python
def census_bensheim_query(dataset, topology, query):
    if query.key != DataKeys.HEATING_SHARES:
        raise ValueError("only heating_shares supported")

    gdf = dataset.fetch(topology.convex_hull, query)
    # ... aggregation ...
    name_mapping = query.params.get("name_mapping", {})
    return mapped_shares
```

The function runs lazily, on every `query()` call — it can re-read the
file or hit the network if it wants, though for performance it is
typical to cache `dataset.get_data()`.
