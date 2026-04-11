from types import SimpleNamespace

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from pypeline.injection import _apply_injections, apply_injected_techs


def _streets() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "street_segment_id": ["seg-1"],
            "region_id": [1],
            "total_heat_demand": [0.0],
        },
        geometry=[LineString([(0.0, 0.0), (1.0, 0.0)])],
        crs="EPSG:4326",
    )


def _toy_energy_system():
    tech = SimpleNamespace(name="cen_heat_pump_D1")
    region_tech = SimpleNamespace(technology=tech, initial_capacity=0.0, initial_energy_output=0.0)
    region = SimpleNamespace(id=1, region_technologies=[region_tech])
    return SimpleNamespace(regions=[region]), region_tech


def supply_injection_defaults_output_to_capacity_t() -> None:
    _, _, injected_techs = _apply_injections(
        _streets(),
        injections=[
            {
                "type": "supply",
                "region_id": 1,
                "technology": "cen_heat_pump",
                "initial_capacity_mw": 5.0,
            }
        ],
        street_id_column="street_segment_id",
        demand_column="total_heat_demand",
        region_id_column="region_id",
    )

    assert injected_techs[0]["initial_output_mw"] == pytest.approx(5.0)


def supply_injection_accepts_legacy_mwh_alias_as_mw_t() -> None:
    _, _, injected_techs = _apply_injections(
        _streets(),
        injections=[
            {
                "type": "supply",
                "region_id": 1,
                "technology": "cen_heat_pump",
                "initial_capacity_mw": 5.0,
                "initial_output_mwh": 4.0,
            }
        ],
        street_id_column="street_segment_id",
        demand_column="total_heat_demand",
        region_id_column="region_id",
    )

    assert injected_techs[0]["initial_output_mw"] == pytest.approx(4.0)


def supply_injection_prefers_canonical_output_key_t() -> None:
    _, _, injected_techs = _apply_injections(
        _streets(),
        injections=[
            {
                "type": "supply",
                "region_id": 1,
                "technology": "cen_heat_pump",
                "initial_capacity_mw": 5.0,
                "initial_output_mw": 3.0,
                "initial_output_mwh": 4.0,
            }
        ],
        street_id_column="street_segment_id",
        demand_column="total_heat_demand",
        region_id_column="region_id",
    )

    assert injected_techs[0]["initial_output_mw"] == pytest.approx(3.0)


def supply_injection_rejects_output_above_capacity_t() -> None:
    with pytest.raises(ValueError, match="must be <= initial_capacity_mw"):
        _apply_injections(
            _streets(),
            injections=[
                {
                    "type": "supply",
                    "region_id": 1,
                    "technology": "cen_heat_pump",
                    "initial_capacity_mw": 5.0,
                    "initial_output_mw": 6.0,
                }
            ],
            street_id_column="street_segment_id",
            demand_column="total_heat_demand",
            region_id_column="region_id",
        )


def apply_injected_techs_defaults_and_aliases_t() -> None:
    es, region_tech = _toy_energy_system()

    apply_injected_techs(
        es,
        [
            {
                "region_id": 1,
                "technology": "cen_heat_pump",
                "initial_capacity_mw": 7.0,
                "initial_output_mwh": 6.0,
            }
        ],
    )

    assert region_tech.initial_capacity == pytest.approx(7.0)
    assert region_tech.initial_energy_output == pytest.approx(6.0)


def apply_injected_techs_rejects_output_above_capacity_t() -> None:
    es, _ = _toy_energy_system()

    with pytest.raises(ValueError, match="must be <= initial_capacity_mw"):
        apply_injected_techs(
            es,
            [
                {
                    "region_id": 1,
                    "technology": "cen_heat_pump",
                    "initial_capacity_mw": 4.0,
                    "initial_output_mw": 5.0,
                }
            ],
        )