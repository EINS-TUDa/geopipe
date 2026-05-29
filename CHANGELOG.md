# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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