import pandas as pd
import pytest

pytest.importorskip("cesm")
pytest.importorskip("gurobipy")

from geopipe.data import DataRegistryQuery
from geopipe.energy_system.demand import Demand, DemandType
from geopipe.optimization.cesm.profiles import profile_names, write_profiles


def _demand(name: str, profile: list[float]) -> Demand:
    demand_type = DemandType(name=name, commodity_in=name, cooperation_of_technologies=False,
                             decrease_percent_per_year=0, value=DataRegistryQuery(key="v"),
                             profile=DataRegistryQuery(key="p"))
    return Demand(demand_type=demand_type, value=1.0, profile=pd.Series(profile, dtype=float))


def test_profile_identical_in_all_regions_is_written_once(tmp_path):
    (tmp_path / "stale.txt").write_text("old")
    # same shape after normalisation
    demands = {1: (_demand("heat", [1, 2, 3]),), 2: (_demand("heat", [2, 4, 6]),)}
    names = profile_names(demands)
    assert set(names.values()) == {"heat"}
    write_profiles(demands, names, tmp_path)
    assert [path.name for path in tmp_path.iterdir()] == ["heat.txt"]


def test_differing_profiles_are_named_per_region():
    demands = {1: (_demand("heat", [1, 2, 3]),), 2: (_demand("heat", [3, 2, 1]),)}
    assert set(profile_names(demands).values()) == {"heat_1", "heat_2"}


def test_profile_shared_by_demands_keeps_the_first_name():
    demands = {1: (_demand("heat", [1, 2, 3]), _demand("pool", [1, 2, 3]))}
    assert set(profile_names(demands).values()) == {"heat"}
