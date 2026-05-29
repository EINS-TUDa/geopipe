# coding=utf-8
"""Tests for CentralTechnology.max_capacity_per_year."""

import pytest

from geopipe.energy_system.technology import CentralTechnology, CentralTechType

START_YEAR = 2025


@pytest.fixture
def make_central_tech():
    """Register a CentralTechType and return a built CentralTechnology instance."""
    registered = []

    def _make(max_capacity, existing_capacity=0.0, retirement_years=None):
        name = f"test_tech_{len(registered)}"
        CentralTechnology.register(name, CentralTechType(
            commodity_in="electricity",
            commodity_out="heat",
            efficiency=1.0,
            technical_lifetime=20,
            opex_cost_energy=0.0,
            opex_cost_power=0.0,
            capex_cost_power=0.0,
            capex_cost_base=0.0,
            max_capacity=max_capacity,
            existing_capacity_retirement_years=retirement_years,
        ))
        registered.append(name)
        return CentralTechnology(name, existing_capacity=existing_capacity)

    yield _make

    CentralTechnology.clear_registered_types()


def test_max_capacity_none_returns_existing_then_unbounded(make_central_tech):
    tech = make_central_tech(max_capacity=None)
    assert tech.max_capacity_per_year(START_YEAR) == {
        START_YEAR: 0.0,
        START_YEAR + 1: None,
    }


def test_max_capacity_scalar(make_central_tech):
    tech = make_central_tech(max_capacity=100.0)
    assert tech.max_capacity_per_year(START_YEAR) == {
        START_YEAR: 0.0,
        START_YEAR + 1: 100.0,
    }


def test_max_capacity_scalar_with_existing_capacity(make_central_tech):
    tech = make_central_tech(max_capacity=100.0, existing_capacity=30.0, retirement_years=10)
    assert tech.max_capacity_per_year(START_YEAR) == {
        START_YEAR: 30.0,
        START_YEAR + 1: 100.0,
    }


def test_max_capacity_dict_with_year_zero_shifts_to_first_buildable_year(make_central_tech):
    tech = make_central_tech(max_capacity={0: 100.0, 5: 200.0})
    assert tech.max_capacity_per_year(START_YEAR) == {
        START_YEAR: 0.0,
        START_YEAR + 1: 100.0,
        START_YEAR + 5: 200.0,
    }


def test_max_capacity_dict_without_year_zero_anchors_start_year(make_central_tech):
    tech = make_central_tech(max_capacity={5: 200.0})
    assert tech.max_capacity_per_year(START_YEAR) == {
        START_YEAR: 0.0,
        START_YEAR + 5: 200.0,
    }


def test_max_capacity_dict_year_one_takes_precedence_over_shifted_year_zero(make_central_tech):
    # Explicit year-1 entry owns start_year+1; the year-0 max is dropped
    # because start_year is reserved for existing_capacity.
    tech = make_central_tech(max_capacity={0: 100.0, 1: 150.0})
    assert tech.max_capacity_per_year(START_YEAR) == {
        START_YEAR: 0.0,
        START_YEAR + 1: 150.0,
    }
