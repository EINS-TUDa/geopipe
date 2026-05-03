import pandas as pd

from ...energy_system.units import Unit, UnitGW, UnitKW, UnitMW

_COLUMNS = ["quantity", "input", "scale_factor", "output"]


def df_units(unit: Unit) -> pd.DataFrame:
    """Return the CESM Units sheet for ``unit``.

    ``input`` is the unit the user provides via :class:`EnergySystem`,
    ``output`` is what CESM stores internally, and
    ``cesm_value = input_value * scale_factor``.
    """
    if isinstance(unit, UnitKW):
        rows = [
            {"quantity": "power", "input": "kW", "scale_factor": 1, "output": "kW"},
            {"quantity": "energy", "input": "MWh", "scale_factor": 1000, "output": "kWh"},
            {"quantity": "co2_emissions", "input": "t", "scale_factor": 1, "output": "t"},
            {"quantity": "cost_energy", "input": "EUR/MWh", "scale_factor": 0.001, "output": "EUR/kWh"},
            {"quantity": "cost_power", "input": "EUR/kW", "scale_factor": 1, "output": "EUR/kW"},
            {"quantity": "co2_spec", "input": "kg/kWh", "scale_factor": 0.001, "output": "t/kWh"},
            {"quantity": "money", "input": "EUR", "scale_factor": 1, "output": "EUR"},
        ]
    elif isinstance(unit, UnitGW):
        rows = [
            {"quantity": "power", "input": "GW", "scale_factor": 1, "output": "GW"},
            {"quantity": "energy", "input": "TWh", "scale_factor": 1000, "output": "GWh"},
            {"quantity": "co2_emissions", "input": "Mio t", "scale_factor": 1, "output": "Mio t"},
            {"quantity": "cost_energy", "input": "EUR/MWh", "scale_factor": 0.001, "output": "Mio EUR/GWh"},
            {"quantity": "cost_power", "input": "EUR/kW", "scale_factor": 1, "output": "Mio EUR/GW"},
            {"quantity": "co2_spec", "input": "kg/kWh", "scale_factor": 0.001, "output": "Mio t/GWh"},
            {"quantity": "money", "input": "Mio EUR", "scale_factor": 1, "output": "Mio EUR"},
        ]
    elif isinstance(unit, UnitMW):
        rows = [
            {"quantity": "power", "input": "MW", "scale_factor": 1, "output": "MW"},
            {"quantity": "energy", "input": "GWh", "scale_factor": 1000, "output": "MWh"},
            {"quantity": "co2_emissions", "input": "kilo t", "scale_factor": 1, "output": "kilo t"},
            {"quantity": "cost_energy", "input": "EUR/MWh", "scale_factor": 0.001, "output": "k EUR/MWh"},
            {"quantity": "cost_power", "input": "EUR/kW", "scale_factor": 1, "output": "k EUR/MW"},
            {"quantity": "co2_spec", "input": "kg/kWh", "scale_factor": 0.001, "output": "kilo t/MWh"},
            {"quantity": "money", "input": "k EUR", "scale_factor": 1, "output": "k EUR"},
        ]
    else:
        raise TypeError(f"Unsupported unit type: {type(unit).__name__}")
    return pd.DataFrame(rows, columns=_COLUMNS)


def scale_factors(unit: Unit) -> dict[str, float]:
    """Return ``{quantity: scale_factor}`` so ``cesm_value / factor`` yields the EnergySystem-unit value."""
    df = df_units(unit)
    return dict(zip(df["quantity"], df["scale_factor"].astype(float)))