from os import PathLike
from typing import Optional, List

from pypeline import EnergySystem, TechnologyRegistry
from pypeline.energy_system import Scenario


def write_cesm_inputs_from_energy_system(
        energy_system: EnergySystem,
        scenario: Scenario,
        *,
        techmap_dir: PathLike,
        timeseries_dir: PathLike,
        model_name: str,
        scenario_name: str,
        tss_name: str,
        technology_registry: TechnologyRegistry | None = None,
        retain_existing_output_factor: float | None = None,
        retain_existing_output_years_factor: float | None = None,
        retain_existing_output_schedule: Optional[List[float]] = None,
        **kwargs,
) -> None:
    """Generate CESM inputs from an EnergySystem."""

    resolved_schedule = _resolve_retain_schedule(
        explicit_schedule=retain_existing_output_schedule,
        scenario=scenario,
    )
    if resolved_schedule is None:
        raise ValueError("retain_existing_output_schedule must be provided via scenario or argument")

    grid_prices, supply_prices = _commodity_config_from_energy_system(energy_system)
    logger.debug(
        "commodity_config extracted: grid_prices=%s (%s), supply_prices=%s (%s)",
        grid_prices,
        type(grid_prices).__name__,
        supply_prices,
        type(supply_prices).__name__,
    )
    _write_cesm_inputs(
        energy_system,
        scenario,
        techmap_dir=techmap_dir,
        timeseries_dir=timeseries_dir,
        model_name=model_name,
        scenario_name=scenario_name,
        tss_name=tss_name,
        grid_prices=grid_prices,
        supply_prices=supply_prices,
        technology_registry=technology_registry,
        retain_existing_output_factor=retain_existing_output_factor,
        retain_existing_output_years_factor=retain_existing_output_years_factor,
        retain_existing_output_schedule=resolved_schedule,
        **kwargs,
    )