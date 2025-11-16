"""CESM template utilities."""
from pathlib import Path
import json
from typing import Any, Dict, Optional, Union

BUILTIN_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "cesm_run_config_template.json": {
        "cesm_params": {
            "start_year": 2020,
            "end_year": 2030,
            "year_gap": 5,
            "discount_rate": 0.05,
            "dt_hours": 1,
        },
        "commodity_config": {
            "heat_commodity_base": "Heat",
            "electricity": {
                "price_eur_per_mwh": 100.0,
                "export_price_eur_per_mwh": -10.0,
            },
        },
        "data_files": {
            "heat_file": "D_Heat_Household_J.txt",
            "heat_unit": "MWH",
            "elec_profile_file": "corrected_eletricity_demand_2016.txt",
        },
        "pipe_config": {
            "loss_fraction": 0.01,
            "cap_max_mw": 10.0,
            "lifetime_years": 30,
            "opex_eur_per_mwh": 1.0,
            "capex_eur_per_mw": 1000.0,
        },
        "network": {
            "edge_strategy": "mst",
            "min_shared_border_m": 0.0,
            "max_pipes_per_district": None,
            "neighbor_distance_m": None,
        },
    },
    "bensheim_config.json": {
        "cesm_params": {
            "start_year": 2020,
            "end_year": 2030,
            "year_gap": 5,
            "discount_rate": 0.05,
            "dt_hours": 1,
        },
        "commodity_config": {
            "heat_commodity_base": "Heat",
            "electricity": {
                "price_eur_per_mwh": 100.0,
                "export_price_eur_per_mwh": -10.0,
            },
        },
        "data_files": {
            "heat_file": "D_Heat_Household_J.txt",
            "heat_unit": "MWH",
            "elec_profile_file": "corrected_eletricity_demand_2016.txt",
        },
        "pipe_config": {
            "loss_fraction": 0.01,
            "cap_max_mw": 10.0,
            "lifetime_years": 30,
            "opex_eur_per_mwh": 0.0,
            "capex_eur_per_mw": 50000.0,
        },
        "boiler_config": {
            "small_scale": {
                "eta": 0.95,
                "capex_eur_per_mw": 11000.0,
                "opex_eur_per_mw": 0.0,
                "opex_eur_per_mwh": 0.0,
                "cap_max_mw": 0.1,
                "lifetime_years": 30,
            },
            "large_scale": {
                "eta": 0.96,
                "capex_eur_per_mw": 10000.0,
                "opex_eur_per_mw": 0.0,
                "opex_eur_per_mwh": 0.0,
                "out_frac_min": 0.5,
                "cap_min_mw": 0.04,
                "cap_max_mw": 0.85,
                "lifetime_years": 30,
            },
        },
        "network": {
            "edge_strategy": "mst",
            "min_shared_border_m": 0.0,
            "max_pipes_per_district": None,
            "neighbor_distance_m": None,
        },
    },
    "eb_template.json": {
        "conversion_process_name": "ElectricBoiler",
        "commodity_in": "Electricity",
        "commodity_out": "Heat",
        "efficiency": 0.95,
        "technical_availability": 1.0,
        "technical_lifetime": 30,
        "cap_min": 0.0,
        "cap_max": 100.0,
        "max_eout": 1_000_000.0,
        "capex_cost_power": 1_200_000.0,
        "opex_cost_power": 15_000.0,
        "opex_cost_energy": 0.5,
    },
    "pipe_template.json": {
        "conversion_process_name": "Pipe",
        "commodity_in": "Heat",
        "commodity_out": "Heat",
        "efficiency": 0.99,
        "technical_availability": 1.0,
        "technical_lifetime": 30,
        "cap_max": 1_000_000_000.0,
        "max_eout": 1_000_000_000_000.0,
        "opex_cost_energy": 1.0,
        "capex_cost_power": 1000.0,
    },
}


def load_cesm_template(name: str) -> Optional[Dict[str, Any]]:
    """Return a template by name, preferring the file system but falling back to built-ins."""
    template_path = Path(__file__).parent / name
    if template_path.exists():
        return json.loads(template_path.read_text(encoding="utf-8"))
    return BUILTIN_TEMPLATES.get(name)


def merge_with_template(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge a template with override values.
    
    Args:
        base: Base template dictionary
        overrides: Values to override
        
    Returns:
        Merged dictionary
    """
    result = base.copy()
    for k, v in overrides.items():
        if isinstance(v, dict) and k in result and isinstance(result[k], dict):
            result[k] = merge_with_template(result[k], v)
        else:
            result[k] = v    
    return result

def load_cesm_config(config_path: Optional[Union[str, Path]] = None, **overrides) -> Dict[str, Any]:
    """Load CESM run configuration.
    
    Args:
        config_path: Optional custom config path
        **overrides: Config override values
        
    Returns:
        Complete config dictionary
    """
    if config_path:
        base_config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    else:
        base_config = load_cesm_template('cesm_run_config_template.json') or {}
    
    return merge_with_template(base_config, overrides)