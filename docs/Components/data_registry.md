# DataRegistry

`DataRegistry` is the pipeline's central data-lookup point. Every
component that needs **regionalised data** — heat demand, electricity
demand profiles, technology shares, street networks — asks the
registry for it by *key* and *region*; the registry returns the result
of the highest-priority dataset whose `regional_validity` covers that
region.

This indirection means the rest of the pipeline does not need to know
whether data comes from a GeoJSON, a Postgres database, a hard-coded
constant, or a custom query function — only that some dataset has
registered against the key.

> **Status today.** `EnergySystemBuilder` currently only queries the
> registry for **`heating_shares`** (via
> `DemandType.technology_shares_query_params`). The other keys listed
> below are reserved for upcoming changes that will move demand
> profiles, demand totals, and street-network data through the same
> mechanism — register them now if it fits your data layout, but until
> those wirings land they have no effect on the built `EnergySystem`.

## Minimal usage

```python
import pathlib
from pypeline import DataRegistry
from pypeline.data.data_registry import DataKeys
from pypeline.data.dataset import FileDataset

heating_shares = FileDataset(
    keys=[DataKeys.HEATING_SHARES],
    file_path=str(pathlib.Path(__file__).parent / "Census2022HeatingType.geojson"),
    query_function=census_bensheim_query,   # see below
    priority=10,
    regional_validity=None,                 # None = applies everywhere
)

data_reg = DataRegistry()
data_reg.register(heating_shares)
```

## API

### `DataRegistry`

| Method | Signature | Description |
|---|---|---|
| `__init__` | `DataRegistry()` | No arguments. |
| `register` | `register(dataset: Dataset) -> None` | Adds a dataset. Lookups walk registered datasets in **descending priority** (then registration order) and return the first match. |

## Built-in keys (`DataKeys`)

| Key                                       | Returns                                                  |
| ----------------------------------------- | -------------------------------------------------------- |
| `RESIDENTIAL_HEAT_DEMAND_PROFILE`         | `pd.Series` of 8760 hourly values (normalised)           |
| `RESIDENTIAL_ELECTRICITY_DEMAND_PROFILE`  | same                                                     |
| `RESIDENTIAL_HEAT_DEMAND`                 | `float` total per region (kWh)                           |
| `RESIDENTIAL_ELECTRICITY_DEMAND`          | `float` total per region (kWh)                           |
| `HEATING_SHARES`                          | `dict[str, float]` summing to 1, after `name_mapping`    |
| `STREET_NETWORK`                          | `GeoDataFrame` with a `geom` `LineString` column         |
| `LINEAR_HEAT_DENSITY`                     | `GeoDataFrame` with `geom` and `heat_density_mwh_per_km` |

A single `Dataset` can advertise multiple keys via the `keys=[…]`
list.

## Datasets

Two built-ins are available out of the box: `FileDataset` (any file
that pandas/geopandas can read) and `PostgreSQLDataset` (any Postgres
connection). Both call the optional `query_function(dataset, region,
query) -> Any` if supplied, otherwise return the loaded data as-is.

### `FileDataset`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `keys` | `list[str]` | — (required) | Data keys this dataset can answer (typically values from `DataKeys`). |
| `file_path` | `str` | — (required) | Path to the source file (`.geojson` / `.gpkg` / `.shp` → geopandas, else pandas CSV). |
| `query_function` | `Callable` or `None` | `None` | Custom resolver with signature `(dataset, region, query) -> Any`. If omitted, `query()` returns the loaded data. |
| `unit` | `UnitEnum` or `None` | `None` | Optional unit annotation for the returned data. |
| `load_data_kwargs` | `dict` or `None` | `None` | Extra kwargs forwarded to the underlying `read_file` / `read_csv`. |
| `priority` | `int` | `10` | Higher = preferred when multiple datasets can answer the same key. |
| `regional_validity` | `GeoDataFrame` or `None` | `None` | Validity polygon; `None` = applies everywhere. Lookups skip datasets whose polygon does not cover the queried region. |

### `PostgreSQLDataset`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `keys` | `list[str]` | — (required) | Data keys this dataset can answer. |
| `db_connection` | `DatabaseConnection` | — (required) | Connection used by `execute_spatial_query` / the query function. |
| `query_function` | `Callable` or `None` | — (required) | Resolver with signature `(dataset, region, query) -> Any`. |
| `unit` | `UnitEnum` or `None` | `None` | Optional unit annotation. |
| `priority` | `int` | `2` | Lower default than `FileDataset` so files win on ties. |
| `regional_validity` | `GeoDataFrame` or `None` | `None` | Validity polygon; `None` = applies everywhere. |

## Custom query function

When the default loader doesn't fit (e.g. you need to aggregate raw
census categories into named technologies), pass a `query_function`.
It receives the dataset, the requested region, and the query dict:

```python
def census_bensheim_query(dataset, region, query):
    if query["key"] != "heating_shares":
        raise ValueError("only heating_shares supported")

    gdf = dataset.get_data()
    # ... spatial join, aggregation, etc. ...
    name_mapping = query.get("name_mapping", {})
    return mapped_shares
```

The function runs lazily, on every `query()` call — it can re-read the
file or hit the network if it wants, though for performance it is
typical to cache `dataset.get_data()`.
