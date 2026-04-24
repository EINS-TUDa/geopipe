from dataclasses import dataclass
from typing import Optional


@dataclass
class ConversionSubProcess:
    conversion_process_name: str
    commodity_in: str
    commodity_out: str
    scenario: str
    spec_co2: Optional[float] = None
    efficiency: Optional[float] = None

    technical_lifetime: Optional[float] = None
    technical_availability: Optional[float] = None
    c_rate: Optional[float] = None
    efficiency_charge: Optional[float] = None
    is_storage: Optional[bool] = None

    opex_cost_energy: Optional[float | str] = None
    opex_cost_power: Optional[float | str] = None
    capex_cost_power: Optional[float | str] = None
    capex_cost_base: Optional[float | str] = None

    cap_active: Optional[float | str] = None
    max_units: Optional[int] = None

    max_eout: Optional[float | str] = None
    min_eout: Optional[float | str] = None

    cap_min: Optional[float | str] = None
    cap_max: Optional[float | str] = None
    cap_res_min: Optional[float | str] = None
    cap_res_max: Optional[float | str] = None

    out_frac_min: Optional[float] = None
    out_frac_max: Optional[float] = None
    in_frac_min: Optional[float] = None
    in_frac_max: Optional[float] = None

    availability_profile: Optional[str] = None
    output_profile: Optional[str] = None
