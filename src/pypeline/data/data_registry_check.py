"""
This module provides functions to check the integrity and consistency of data in the DataRegistry.
"""
import pandas as pd
from geopandas import GeoDataFrame
from shapely.geometry import LineString
from .dataset import CensusTechnology
from .data_registry import DataRegistry, DataKeys


def check_registry(registry: DataRegistry, sample_region) -> bool:
    success = True

    data_key_to_check_function = {
        DataKeys.RESIDENTIAL_HEAT_DEMAND_PROFILE: _check_residential_heat_demand_profile,
        DataKeys.RESIDENTIAL_ELECTRICITY_DEMAND_PROFILE: _check_residential_electric_demand_profile,
        DataKeys.RESIDENTIAL_ELECTRICITY_DEMAND: _check_residential_electricity_demand,
        DataKeys.RESIDENTIAL_HEAT_DEMAND: _check_residential_heat_demand,
        DataKeys.HEATING_SHARES: _check_heating_shares,
        DataKeys.STREET_NETWORK: _check_street_network,
        DataKeys.LINEAR_HEAT_DENSITY: _check_linear_heat_density
    }

    for data_key, check_function in data_key_to_check_function.items():
        local_success = check_function(registry, sample_region)
        if local_success:
            print(f"Check passed for key '{data_key}'")
        success &= local_success

    return success


def _check_linear_heat_density(registry: DataRegistry, sample_region) -> bool:
    key = DataKeys.LINEAR_HEAT_DENSITY
    try:
        result = registry.query({"key": key, "region": sample_region})
    except Exception as e:
        print(f"Error querying registry for key '{key}': {e}")
        return False

    if not isinstance(result, GeoDataFrame):
        print(f"Expected a GeoDataFrame for key '{key}', but got {type(result)}")
        return False

    try:
        if result["geom"].dtype is not LineString:
            print(f"Expected 'geom' column of type LineString for key '{key}', but got {result['geom'].dtype}")
            return False
    except KeyError:
        print(f"Expected 'geom' column in GeoDataFrame for key '{key}', but it was not found")
        return False

    try:
        if result["heat_density_mwh_per_km"].dtype not in [float, int]:
            print(
                f"Expected 'heat_density_mwh_per_km' column of type float or int for key '{key}', but got {result['heat_density_mwh_per_km'].dtype}")
            return False
    except KeyError:
        print(f"Expected 'heat_density_mwh_per_km' column in GeoDataFrame for key '{key}', but it was not found")
        return False

    return True


def _check_street_network(registry: DataRegistry, sample_region) -> bool:
    key = DataKeys.STREET_NETWORK
    try:
        result = registry.query({"key": key, "region": sample_region})
    except Exception as e:
        print(f"Error querying registry for key '{key}': {e}")
        return False

    if not isinstance(result, GeoDataFrame):
        print(f"Expected a GeoDataFrame for key '{key}', but got {type(result)}")
        return False

    try:
        if result["geom"].dtype is not LineString:
            print(f"Expected 'geom' column of type LineString for key '{key}', but got {result['geom'].dtype}")
            return False
    except KeyError:
        print(f"Expected 'geom' column in GeoDataFrame for key '{key}', but it was not found")
        return False

    return True


def _check_heating_shares(registry: DataRegistry, sample_region) -> bool:
    success = True
    key = DataKeys.HEATING_SHARES
    name_mapping = {tech: tech.value for tech in CensusTechnology}

    try:
        result_1 = registry.query({"key": key, "region": sample_region})
        result_2 = registry.query({"key": key, "region": sample_region, "name_mapping": name_mapping})
    except Exception as e:
        print(f"Error querying registry for key '{key}': {e}")
        success &= False
        return success

    for result, key_type in zip([result_1, result_2],[CensusTechnology, str]):
        if not isinstance(result, dict):
            print(f"Expected a dict for key '{key}', but got {type(result)}")
            success &= False
            return success

        for k, v in result.items():
            if not isinstance(k, key_type):
                print(f"Expected keys of type {key_type} for key '{key}', but got key '{k}' of type {type(k)}")
                success &= False
            if not isinstance(v, (int, float)):
                print(f"Expected values of type int or float for key '{key}', but got value '{v}' of type {type(v)}")
                success &= False

    return success


def _check_residential_heat_demand(registry: DataRegistry, sample_region) -> bool:
    key = DataKeys.RESIDENTIAL_HEAT_DEMAND
    try:
        result = registry.query({"key": key, "region": sample_region})
    except Exception as e:
        print(f"Error querying registry for key '{key}': {e}")
        return False

    if not isinstance(result, (int, float)):
        print(f"Expected a numeric value for key '{key}', but got {type(result)}")
        return False

    return True


def _check_residential_electricity_demand(registry: DataRegistry, sample_region) -> bool:
    key = DataKeys.RESIDENTIAL_ELECTRICITY_DEMAND
    try:
        result = registry.query({"key": key, "region": sample_region})
    except Exception as e:
        print(f"Error querying registry for key '{key}': {e}")
        return False

    if not isinstance(result, (int, float)):
        print(f"Expected a numeric value for key '{key}', but got {type(result)}")
        return False

    return True


def _check_residential_electric_demand_profile(registry: DataRegistry, sample_region) -> bool:
    key = DataKeys.RESIDENTIAL_ELECTRICITY_DEMAND_PROFILE
    try:
        result = registry.query({"key": key, "region": sample_region})
    except Exception as e:
        print(f"Error querying registry for key '{key}': {e}")
        return False

    if not isinstance(result, pd.Series):
        print(f"Expected a pd.Series for key '{key}', but got {type(result)}")
        return False

    return True


def _check_residential_heat_demand_profile(registry: DataRegistry, sample_region) -> bool:
    key = DataKeys.RESIDENTIAL_HEAT_DEMAND_PROFILE
    try:
        result = registry.query({"key": key, "region": sample_region})
    except Exception as e:
        print(f"Error querying registry for key '{key}': {e}")
        return False

    if not isinstance(result, pd.Series):
        print(f"Expected a pd.Series for key '{key}', but got {type(result)}")
        return False

    return True
