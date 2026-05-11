# modify_streets_data

`modify_streets_data` is a standalone input-data utility. It applies
small, declarative patches to a streets GeoDataFrame so you can tweak
individual values without editing the source GeoJSON. It is
**independent of the TopologyBuilder** — the returned GeoDataFrame can
be fed into any downstream component.

## API

`modify_streets_data(streets_data, modifications_file) -> GeoDataFrame`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `streets_data` | `GeoDataFrame` or `Path` | — (required) | Street network. A `Path` is loaded via `geopandas.read_file`. |
| `modifications_file` | `Path` | — (required) | Path to a YAML file describing the patches (see `modifications.yaml` below). |

**Returns** — the modified `GeoDataFrame`. When a `Path` is passed for
`streets_data`, the file is freshly read and the result is independent
of any earlier copy; when a `GeoDataFrame` is passed, it is modified
in place and also returned.

## Usage

```python
from pathlib import Path
from pypeline.topology_builder.topology_build_utils import modify_streets_data

streets = modify_streets_data(
    streets_data=Path("input/streets.geojson"),
    modifications_file=Path("input/modifications.yaml"),
)
```

## `modifications.yaml`

Top-level key is the column to modify. Each list entry selects one row
and specifies an action:

```yaml
waerme_mwh:
  - street_id: DEHE04620001hKzm
    replace: 50
  - index: 0
    add: 10
```

**Row selector** — exactly one of:

- `<column_name>: <value>` — match the row where the given column
  equals the value (e.g. `street_id: DEHE04620001hKzm`).
- `index: <i>` — match by raw row index.

**Action** — exactly one of:

- `replace: <value>` — overwrite the cell with `<value>`.
- `add: <value>` — add `<value>` to the current cell.
