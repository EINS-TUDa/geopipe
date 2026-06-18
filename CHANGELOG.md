# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.2] - 2026-06-18

### Added
- Street-network cleaning step in the topology builders, run before region assignment:
  - **Junction division** (`set_divide_at_junctions(enabled, tol)`, off by default): where a street meets another at a T-junction or crosses it mid-segment — geometrically touching but graph-disconnected — the crossed street is split at the meeting point so the two share a graph node. Each piece becomes its own row and its extensive columns are split by length share.
  - **Dead-end gap closing** (`set_gap_distance`, opt-in): bridge a dead-end to the nearest unconnected street within a search radius (CRS units).
  - **Isolated-segment dropping** (`set_drop_isolated_null_segments`, opt-in): drop still-isolated segments whose extensive columns are all NULL (no demand).
- `set_topology_connections_check(enabled)` to disable the per-region connectivity check.
- `id_column` parameter on `set_streets_data`, naming a stable per-street identifier surfaced in diagnostics.

### Changed
- **Breaking:** `set_streets_data` now requires an `id_column` argument.
- **Breaking:** building now raises (instead of warning) when no extensive columns are configured.
- The region-connectivity check now reports the number of connected segment groups and the street IDs not connected to the main group, and aggregates failures across all regions instead of raising on the first.
- The polygon builder now collects and reports all streets that lie within multiple polygons (by street ID) instead of raising on the first one.
- CESM solving: the techmap now writes a `UnitMW` units sheet, scaling power, energy, money and CO₂ down by a factor of 1000 to improve numerical conditioning and reduce solving times. Results are unscaled on parse, so reported values and units are unchanged.

[1.1.2]: https://git.rwth-aachen.de/carolin.ayasse/data_pipeline_esm/-/compare/1.1.1...1.1.2

## [1.1.1] - 2026-06-09

### Added
- `DemandValueSource` abstraction for resolving a demand's annual total per region, with two implementations: `ColumnDemandValue` (sums a `streets_data` column) and `ExplicitDemandValue` (explicit `{region_id: value}` mapping).
- Localised "special" demands: a demand is now built only in regions where its value source yields a value, so `ExplicitDemandValue` scopes a demand to specific regions.
- Interface to add specific demands to individual regions.
- `default_decentral_supply_technology` now accepts a `[(tech_name, share), ...]` mix (shares validated to sum to 1) in addition to a single technology name.
- `DemandType` validation (Pydantic and builder `validate()`): commodity must be suppliable by a decentral technology or import, default-supply requirements, and explicit-value region ids must exist in the topology.
- `mip_gap` parameter on `CESMOptimizationBackend.solve()` to set the relative MIP optimality gap (`None` uses the solver default).
- Energy system build-logic diagram in the docs.

### Changed
- **Breaking:** `DemandType` is now a Pydantic model. `demand_column_name="..."` is replaced by `value_source=ColumnDemandValue(column_name="...")`; `technology_shares_query_params` is now optional (`None` for demands without census data).
- Decentral technologies are matched to a demand by commodity (`commodity_out == commodity_in`) instead of being applied to every demand.
- Removed the machine-specific editable install (`-e file:///...`) from `requirements.txt`.

### Fixed
- Decentral technologies were added for every defined demand regardless of whether `commodity_out`/`commodity_in` matched.
- `cooperation_of_technologies` indicator was ignored when instantiating decentral technologies.
- Demand profile bug: the techmap now derives the output profile directly from `profile_path`.
- Regions with a zero `ColumnDemandValue` total no longer produce empty demands.

[1.1.1]: https://git.rwth-aachen.de/carolin.ayasse/data_pipeline_esm/-/compare/1.1.0...1.1.1

## [1.1.0] - 2026-05-29

### Added
- Energy export functionality.
- `forced_decentral_technology_share_per_region` parameter to `EnergySystemBuilderConfig`.
- Validation for `EnergySystemBuilderConfig` / energy system model, with an efficiency guard.
- Constraint fixing central technology capacity in year 0 to existing capacity (with tests).
- Run date now recorded in results.
- Logging to file; suppressed the CESM setuptools warning.

### Changed
- **Breaking:** renamed package `pypeline` → `geopipe` (import paths changed); docs updated accordingly.

### Fixed
- Topology build test.

[1.1.0]: https://git.rwth-aachen.de/carolin.ayasse/data_pipeline_esm/-/compare/1.0.0...1.1.0
[1.0.0]: https://git.rwth-aachen.de/carolin.ayasse/data_pipeline_esm/-/tags/1.0.0