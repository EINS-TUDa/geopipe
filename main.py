# -*- coding: utf-8 -*-
from pathlib import Path
import matplotlib.pyplot as plt
import geopandas as gpd

from core.data.data import DataRegistry
from core.energy_system.technology_registry import TechnologyRegistry
from tests.test_district import test_district_from_bounding_box, test_residential_yearly_heat_demand

import core.energy_system.technologies

if __name__ == "__main__":
    technology_registry = TechnologyRegistry()
    technology_registry.load_from_default()

    data_registry = DataRegistry()
    data_registry.load_from_default()





