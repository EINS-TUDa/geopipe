YAML technology catalog

This directory bundles the default technology specifications for the energy
system model. All packaged technologies live inside `technologies.yaml`, which
replaces the former per-technology JSON files.

Each entry in `technologies.yaml` is a mapping with the following fields
(required unless noted):

- `name` (string)
  - Unique technology identifier. Used as the conversion process name in CESM.
- `commodity_in` (string)
  - Input commodity name (e.g. "Electricity", "Heat_D0").
- `commodity_out` (string)
  - Output commodity name (e.g. "Heat", "Dummy").
- `efficiency` (number)
  - Thermal/electrical conversion efficiency (unitless). Use `1.0` for pure
    pass-through technologies.
- `technical_lifetime` (integer)
  - Lifetime in years (mapped to `technical_lifetime` in CESM parameters).
- `capex_cost_power` (number)
  - Capital expenditure per MW (EUR/MW).
- `opex_cost_power` (number, optional)
  - Fixed OPEX component per MW (EUR/MW).
- `opex_cost_energy` (number, optional)
  - Variable OPEX per MWh (EUR/MWh).
- `stage` (string, optional)
  - Value from `TechnologyStage` (e.g. `stage1`, `stage2`). Defaults to
    `stage1` if omitted.
- `category` (string, optional)
  - Value from `TechnologyCategory` (e.g. `demand_link`, `supply`). Defaults to
    `demand_link` if omitted.

Optional extras:
- `availability_profile` (string)
  - Name of a TSS file (without extension) describing availability over the
    time series.

Guidance:
- Append new entries to the `technologies` list (or top-level array) in
  `technologies.yaml`.
- The tech loader validates the schema during import; malformed entries are
  skipped.
- Commodity names are canonicalised by the CESM writer (e.g. "electricity" →
  "Electricity").

  
- Additional CESM parameters (e.g. `cap_max`, `output_profile`) are applied by
  the writer at export time and do not need to appear in the YAML file.
