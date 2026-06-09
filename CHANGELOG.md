# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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