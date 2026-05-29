# coding=utf-8
from pathlib import Path

import pytest


@pytest.fixture
def streets_data_path():
    path = Path(__file__).parent / 'test_data' / 'bensheim_streets_heat_demand.geojson'
    assert path.exists()
    return path


@pytest.fixture
def polygons_data_path():
    path = Path(__file__).parent / 'test_data' / '4_polygone_bensheim.geojson'
    assert path.exists()
    return path
