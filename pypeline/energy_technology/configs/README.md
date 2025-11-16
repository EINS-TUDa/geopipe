YAML technology catalog

This directory bundles the default technology specifications for the energy
system model. All packaged technologies live inside `technologies.yaml` while
shared field defaults (applied to every entry unless overridden) live in
`defaults.yaml`.

Default layering applied by the loader:

1. Global defaults (`defaults` block)
2. Category-specific overrides (`categories` block, e.g. `grid`)
3. Stage-specific overrides (`stages` block, optional)
4. The individual technology entry itself

`technologies.yaml` maps technology names to their field dictionaries. The YAML
key becomes the technology `name`, so you do not need to repeat it inside the
entry. Each entry supports the following fields (required unless noted
otherwise):

- `commodity_in` (string)
  - Input commodity name (e.g. "Electricity", "Heat_D0").
- `commodity_out` (string)
  - Output commodity name (e.g. "Heat", "Dummy"). Automatically inferred when
    omitted for prefixes listed below.
- `efficiency` (float)
  - Thermal/electrical conversion efficiency (unitless). Use `1.0` for pure
    pass-through technologies.
- `technical_lifetime` (integer)
  - Lifetime in years (mapped to `technical_lifetime` in CESM parameters).
- `capex_cost_power` (float)
  - Capital expenditure per MW (EUR/MW).
- `cap_min` / `cap_max` (float, optional)
  - Lower/upper capacity bounds in MW. Leave undefined for no constraint.
- `opex_cost_power` (float, optional)
  - Fixed OPEX component per MW (EUR/MW).
- `opex_cost_energy` (float, optional)
  - Variable OPEX per MWh (EUR/MWh).
- `stage` (string, optional)
  - Value from `TechnologyStage` (e.g. `stage1`, `stage2`). Defaults to
    `stage1` if omitted or inferred from the technology name prefix.
- `category` (string, optional)
  - Value from `TechnologyCategory` (e.g. `demand_link`, `supply`). Defaults to
    `demand_link` if omitted.

Optional extras:
- `availability_profile` (string)
  - Name of a TSS file (without extension) describing availability over the
    time series.

- Put shared defaults in `defaults.yaml`. The loader merges them into every
  technology specification before validation. Use the ``categories`` and
  ``stages`` sections to define presets for groups (for example, grid assets).
  Keep cost and performance numbers tech-specific unless a value legitimately
  applies to all members of a group.
- Append new entries to the `technologies` list (or top-level array) in
  `technologies.yaml`.
- The tech loader validates the schema during import. Malformed entries now cause
  a descriptive error that includes the YAML file and technology name, making
  failures visible instead of silently skipping broken specs.
- Commodity names are canonicalised by the CESM writer (e.g. "electricity" →
  "Electricity"). For convenience the loader infers `commodity_out`, `stage`,
  and some input commodities when they are omitted for common name patterns:
  - `ind_*` → `commodity_out = residential_heat`, `stage = stage1`
  - `cen_*` → `commodity_out = district_heat_in`, `stage = stage3`
  - `grid_*` or `*_grid` → `category = grid`, `stage = stage2`, `commodity_out` inferred from the
    suffix (e.g. `grid_electricity` → `electricity`, `heat_grid` → `district_heat_out`)
  - Any name containing `heat_grid` (including `district_heat_grid_*` pipe branches) is treated as
    a district heat grid: `category = grid`, `stage = stage2`, `commodity_in = district_heat_in`,
    `commodity_out = district_heat_out` unless explicitly overridden in YAML.
  - `ind_*` containing `_connection` → `category = demand_link`
  - Names containing `boiler` → `category = supply`
  - Names containing `boiler` also infer the token before `boiler` as `commodity_in` (e.g.
    `ind_gas_boiler` → `gas`, `ind_oil_boiler` → `oil`)
  - `ind_*` containing `_district_` → `commodity_in = district_heat_out`
- Additional CESM parameters (e.g. `output_profile`) are applied by the writer
  at export time and do not need to appear in the YAML file.
- To include external YAML specs at runtime, set the environment variable
  `PYPELINE_TECH_SPECS` to a path-separated list of files or pass additional
  file paths to `register_default_technologies(extra_spec_files=...)`.
