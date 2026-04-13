from pypeline.data.dataset import SimpleDataset, CSVDataset
from pypeline.units import UnitEnum

def get_default_residential_heat_demand_profile_dataset() -> CSVDataset:
    return CSVDataset(
        keys=["residential_heat_demand_profile"],
        file_path="data/D_Heat_Household_J.txt",
        pandas_kwargs={"sep": "\s+", "decimal": ".", "header": None},
        priority=1,
    )

def get_default_residential_electricity_demand_profile_dataset() -> CSVDataset:
    return CSVDataset(
        keys=["residential_electricity_demand_profile"],
        file_path="data/corrected_eletricity_demand_2016.txt",
        pandas_kwargs={"sep": "\s+", "decimal": ".", "header": None},
        priority=1,
    )

def get_default_residential_yearly_electricity_demand_dataset() -> SimpleDataset:
    return SimpleDataset(
    keys=["residential_electricity_demand"],
    unit=UnitEnum.KWH,
    data=3500.0,
    priority=1,
    )