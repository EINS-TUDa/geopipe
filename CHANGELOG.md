Changelog
=========

All notable changes to this project will be documented in this file.

The format is inspired by Keep a Changelog and adheres (lightly) to Semantic Versioning.

Unreleased
----------
Added
- Unified `tools.cesm_plugin` module consolidating previous `cesm_backend`, `cesm_writer`, and `cesm_runner` logic into a single, documented API.
- This changelog.
 - JSON-only technology catalog with `TechnologySpec` dataclass and loader utilities.

Changed
- `examples/bensheim_om.py` now imports only from `tools.cesm_plugin`.
 - Default technologies now registered via `register_default_technologies()` instead of class decorators.

Removed
- Deprecated shim files `tools/cesm_backend.py`, `tools/cesm_writer.py`, `tools/cesm_runner.py` (previously emitted `DeprecationWarning`). All functionality is available via `tools.cesm_plugin`.

Breaking
- Any direct import of the removed shim modules must be updated to use `tools.cesm_plugin`.

 Deprecated
 - Module `pypeline.energy_system.technologies` (class-based default tech definitions). Use JSON specs + `catalog.register_default_technologies()`.

Migration Notes
---------------
1. Replace backend import:
   - Old: `from tools.cesm_backend import CESMBackend`
   - New: `from tools.cesm_plugin import CESMBackend`
2. Replace writer import:
   - Old: `from tools.cesm_writer import write_cesm_inputs_from_data`
   - New: `from tools.cesm_plugin import write_cesm_inputs_from_data`
3. Runner usage (CLI):
   - Old: `python tools/cesm_runner.py ...` (file removed)
   - New: `python tools/cesm_plugin.py ...` or `python -m tools.cesm_plugin ...`.

Future Improvements
-------------------
- Simplify path injection logic in `cesm_plugin.main` once CESM core is packaged properly (e.g. turning `CESM/core` into an installable package).
