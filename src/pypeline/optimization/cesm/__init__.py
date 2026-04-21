"""CESM optimization backend package.

Files in this package and their roles:

backend.py
    :class:`CESMOptimizationBackend` — the public entry point.  Orchestrates
    input writing, subprocess invocation, and result parsing.

input_writer.py
    Converts an :class:`~pypeline.energy_system.core.EnergySystem` and
    :class:`~pypeline.energy_system.core.Scenario` into the CESM techmap XLSX
    and timeseries TXT files.

conversion_rows.py
    :class:`_ConversionRowsBuilder` and :func:`tech_to_cesms_row` — build the
    ConversionSubProcess rows for each technology, handling multi-district
    replication, retention overrides, lockout periods, and capacity targets.

result_parser.py
    :func:`parse_cesm_outputs` reads the CESM result SQLite DB and returns a
    :class:`CESMResults` (subclass of the standardized ``Results``).

cli.py
    Thin CLI wrapper around the CESM solver; invoked as a subprocess by
    :class:`CESMOptimizationBackend`.
"""
from pypeline.optimization.cesm.backend import CESMOptimizationBackend
from pypeline.optimization.cesm.input_writer import write_cesm_inputs_from_energy_system
from pypeline.optimization.cesm.result_parser import CESMResults

__all__ = [
    "CESMOptimizationBackend",
    "write_cesm_inputs_from_energy_system",
    "CESMResults",
]
