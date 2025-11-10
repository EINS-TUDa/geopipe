import geopandas as gpd
import pandas as pd
from pypeline.data import (
    DataRegistry,
    DatabaseConnection,
    PostgreSQLDataset, CSVDataset,
)
from sqlalchemy import text

from pypeline.data.data_utils import get_gdf_from_ags
from pypeline.data.dataset import SimpleDataset, FileDataset
from pypeline.energy_system.unit import UnitEnum

# Module-level variable for singleton instance
_DEFAULT_REGISTRY: DataRegistry | None = None


def ***REMOVED***_census_query(dataset: PostgreSQLDataset, query: dict) -> dict[str, float]:
    if query["key"] != "heating_shares":
        raise ValueError("census_grid_query only supports 'heating_types' key")

    name_mapping = {   "gas": "ind_gas_boiler",
                       "heizoel": "ind_oil_boiler",
                       "holz_holzpellets": "wood",
                       "biomasse_biogas": None,
                       "solar_geothermie_waermepumpen": "ind_heat_pump",
                       "strom": None,
                       "kohle": None,
                       "fernwaerme": "ind_district_heating_connection",
                       "kein_energietraeger": None}

    columns = ["heizoel", "biomasse_biogas", "strom", "fernwaerme", "gas",
               "holz_holzpellets", "solar_geothermie_waermepumpen", "kohle", "kein_energietraeger"]

    columns_sql = ", ".join([f'COALESCE(SUM("{col}"), 0) AS "{col}"' for col in columns])

    sql_text = text(f"""
        SELECT
            {columns_sql}
        FROM clean_census_energietraeger.shares_2_buildings
        WHERE ST_Within(
            centroid,
            ST_Transform(
                ST_GeomFromText(:wkt, :epsg),
                ST_SRID(centroid))
        )
    """)

    df_shares = dataset.execute_spatial_query(query, sql_text)

    total = df_shares[columns].sum(axis=1).iloc[0]
    if total and total != 0:
        df_shares[columns] = df_shares[columns] / total
    else:
        df_shares[columns] = 0
    heating_shares = df_shares.iloc[0].to_dict()
    heating_shares = {name_mapping[k]: v for k, v in heating_shares.items() if name_mapping[k] is not None}

    return heating_shares


def ***REMOVED***_kwp_query(dataset: PostgreSQLDataset, query: dict) -> float:
    if query["key"] != "residential_heat_demand":
        raise ValueError("***REMOVED***_kwp_query only supports 'residential_heat_demand' key")

    sql_text = text("""
        SELECT
            COALESCE(SUM(demand_heating), 0) AS total_demand
        FROM kwp.buildings_heat_demand
        WHERE ST_Within(
            centroid,
            ST_Transform(
                ST_GeomFromText(:wkt, :epsg),
                ST_SRID(centroid)
            )
        )
        """)

    df = dataset.execute_spatial_query(query, sql_text)

    row = df.iloc[0]
    total_demand = float(row["total_demand"])

    return total_demand

def waermeatlas_hessen_query(dataset: FileDataset, query: dict) -> float:
    if query["key"] != "residential_heat_demand":
        raise ValueError("waermeatlas_hessen_query only supports 'residential_heat_demand' key")

    region: gpd.GeoDataFrame = query["region"]
    region_epsg = region.crs.to_epsg()

    gdf = dataset.get_data()
    gdf = gdf.to_crs(epsg=region_epsg)
    gdf_in_region = gpd.sjoin(gdf, region, predicate="within", how="inner")
    return gdf_in_region["qnutzwaerme_2020_kwh"].sum()

def census_south_hessen_query(dataset: FileDataset, query: dict) -> dict[str, float]:
    if query["key"] != "heating_shares":
        raise ValueError("census_south_hessen_query only supports 'heating_shares' key")

    name_mapping = {   "Gas": "ind_gas_boiler",
                       "Heizoel": "ind_oil_boiler",
                       "Holz_Holzpellets": "wood",
                       "Biomasse_Biogas": None,
                       "Solar_Geothermie_Waermepumpen": "ind_heat_pump",
                       "Strom": None,
                       "Kohle": None,
                       "Fernwaerme": "ind_district_heating_connection",
                       "kein_Energietraeger": None}

    region: gpd.GeoDataFrame = query["region"]
    region_epsg = region.crs.to_epsg()

    gdf = dataset.get_data()
    if gdf.crs.to_epsg() != region_epsg:
        gdf = gdf.to_crs(epsg=region_epsg)

    technologies = ["Heizoel", "Biomasse_Biogas", "Strom", "Fernwaerme", "Gas",
                    "Holz_Holzpellets", "Solar_Geothermie_Waermepumpen", "Kohle", "kein_Energietraeger"]

    gdf_in_region = gpd.sjoin(gdf, region, predicate="intersects", how="inner")

    for tech in technologies:
        gdf_in_region[tech] = pd.to_numeric(gdf_in_region[tech], errors='coerce').fillna(0)

    tech_amounts = {}
    for tech in technologies:
        tech_amounts[tech] = gdf_in_region[tech].sum()
    total = sum(tech_amounts.values())
    if total == 0:
        tech_shares = {tech: 0.0 for tech in technologies}
    else:
        tech_shares = {tech: amount / total for tech, amount in tech_amounts.items()}

    heating_shares = {name_mapping[k]: float(v) for k, v in tech_shares.items() if name_mapping[k] is not None}
    return heating_shares


def _create_default_datasets() -> list:
    """Create and return all default datasets (lazy initialization)."""
    # Create database connection
    ***REMOVED***_conn = DatabaseConnection(
        host="localhost",
        # host="ds1.example.com",
        port=54328,
        database="***REMOVED***",
        user="***REMOVED***",
        password="***REMOVED***"
    )
    print(f"***REMOVED*** on {***REMOVED***_conn.host} available: {***REMOVED***_conn.is_available()}")

    census_heating_dataset = PostgreSQLDataset(
        keys=["heating_shares"],
        db_connection=***REMOVED***_conn,
        priority=5,
        query_function=***REMOVED***_census_query,
        regional_validity=get_gdf_from_ags(["09 1 85 149"])
    )

    ***REMOVED***_kwp_dataset = PostgreSQLDataset(
        keys=["residential_heat_demand"],
        unit=UnitEnum.KWH,
        db_connection=***REMOVED***_conn,
        priority=5,
        query_function=***REMOVED***_kwp_query,
        regional_validity=get_gdf_from_ags(["09 1 85 149"])
    )

    residential_heat_demand_profile_dataset = CSVDataset(
        keys=["residential_heat_demand_profile"],
        file_path="data/D_Heat_Household_J.txt",
        pandas_kwargs={"sep": "\s+", "decimal": ".", "header": None},
        priority=1,
    )

    residential_electricity_demand_profile_dataset = CSVDataset(
        keys=["residential_electricity_demand_profile"],
        file_path="data/corrected_eletricity_demand_2016.txt",
        pandas_kwargs={"sep": "\s+", "decimal": ".", "header": None},
        priority=1,
    )

    residential_yearly_electricity_demand = SimpleDataset(
        keys=["residential_electricity_demand"],
        unit=UnitEnum.KWH,
        data=3500.0,
        priority=1,
    )

    waermeatlas_hessen_dataset = FileDataset(
        keys=["residential_heat_demand"],
        file_path="data/WaermeatlasHessen.gpkg",
        query_function=waermeatlas_hessen_query,
        unit=UnitEnum.KWH,
        priority=5,
        regional_validity=get_gdf_from_ags(["06"]),
        load_data_kwargs={"layer":"WAH_Punkte"}
    )

    census_south_hessen_dataset = FileDataset(
        keys=["heating_shares"],
        file_path="data/Census2022HeatingType100mGrid/Census2022HeatingType100mGrid_Polygons_southhessen.geojson",
        query_function=census_south_hessen_query,
        priority=3,
        regional_validity=get_gdf_from_ags(["06"]),
    )

    return [
        census_heating_dataset,
        ***REMOVED***_kwp_dataset,
        residential_heat_demand_profile_dataset,
        residential_electricity_demand_profile_dataset,
        residential_yearly_electricity_demand,
        waermeatlas_hessen_dataset,
        census_south_hessen_dataset,
    ]


def get_default_data_registry() -> DataRegistry:
    """
    Returns the default DataRegistry instance.
    Uses lazy initialization - the registry is created only on first call.
    """
    global _DEFAULT_REGISTRY

    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = DataRegistry()
        datasets = _create_default_datasets()
        for dataset in datasets:
            _DEFAULT_REGISTRY.register(dataset)

    return _DEFAULT_REGISTRY


if __name__ == "__main__":
    # Simple test query for debugging
    test_query_1 = {"key": "heating_shares",
                "region": gpd.read_file("../../data/polygon_neuburg.geojson")}
    test_query_2 = {"key": "residential_heat_demand",
                "region": gpd.read_file("../../data/polygon_neuburg.geojson")}
    test_query_3 = {"key": "residential_heat_demand_profile"}
    test_query_4 = {"key": "heating_shares", "region": gpd.read_file("../../data/baublock_bensheim_epsg25832.geojson")}

    registry = get_default_data_registry()
    for ds in registry.get_datasets():
        if isinstance(ds, FileDataset):
            ds.file_path = "../../" + ds.file_path

    data_1 = registry.query(test_query_1)
    data_2 = registry.query(test_query_2)
    data_3 = registry.query(test_query_3)
    data_4 = registry.query(test_query_4)
    print(data_1)
    print(data_2)
    print(data_3)
    print(data_4)
    print("todo: introduce test for keys and required data format")
