import geopandas as gpd
import pandas as pd
from sqlalchemy import text

from pypeline import DataRegistry
from pypeline.data import DatabaseConnection, PostgreSQLDataset
from pypeline.data.data_registry import DataKeys
from pypeline.data.data_registry_check import check_registry
from pypeline.data.data_utils import get_gdf_from_ags
from pypeline.data.dataset import CensusTechnology
from pypeline.data.default_datasets import get_default_residential_heat_demand_profile_dataset, \
    get_default_residential_electricity_demand_profile_dataset, \
    get_default_residential_yearly_electricity_demand_dataset
from pypeline.units import UnitEnum


def ***REMOVED***_waermeatlas_heat_demand_query(dataset: PostgreSQLDataset, query: dict) -> float:
    if query["key"] != DataKeys.RESIDENTIAL_HEAT_DEMAND:
        raise ValueError(f"***REMOVED***_waermeatlas_heat_demand_query only supports '{DataKeys.RESIDENTIAL_HEAT_DEMAND}' key")


    sql_text = f"""
        WITH geom_input AS (
            SELECT ST_Transform(
                ST_GeomFromText(:wkt, :epsg),
                Find_SRID('need_intern','waermeatlas_hessen_bensheim_wah_punkte','geom')
            ) AS g
        )
        SELECT
            SUM(qnutzwaerme_2020_kwh) AS heat_demand_kwh
        FROM need_intern.waermeatlas_hessen_bensheim_wah_punkte, geom_input
        WHERE geom && geom_input.g
          AND ST_Within(geom, geom_input.g);
        """

    df_heat_demand = dataset.execute_spatial_query(query, sql_text)
    return float(df_heat_demand["heat_demand_kwh"].iloc[0])

def ***REMOVED***_gauss_census_query(dataset: PostgreSQLDataset, query: dict) -> dict[str, float]:
    if query["key"] != "heating_shares":
        raise ValueError("census_grid_query only supports 'heating_types' key")

    census_names = {"gas": CensusTechnology.Gas,
                    "heizoel": CensusTechnology.Oil,
                    "holz_holzpellets": CensusTechnology.Wood,
                    "biomasse_biogas": CensusTechnology.Biomass,
                    "solar_geothermie_waermepumpen": CensusTechnology.Renewable,
                    "strom": CensusTechnology.Electric,
                    "kohle": CensusTechnology.Coal,
                    "fernwaerme": CensusTechnology.District_Heating,
                    "kein_energietraeger": CensusTechnology.NoEnergyCarrier}

    columns_sql = ", ".join([f'COALESCE(SUM("{tech}"), 0) AS "{tech}"' for tech in list(census_names.keys())])

    sql_text = text(f"""
        WITH geom_input AS (
            SELECT ST_Transform(
                ST_GeomFromText(:wkt, :epsg),
                Find_SRID('opendata','zensus_2022_100m_energietraeger','geom')
            ) AS g
        )
        SELECT
            {columns_sql}
        FROM opendata.zensus_2022_100m_energietraeger p, geom_input
        WHERE p.geom && geom_input.g
          AND ST_Within(p.geom, geom_input.g)
    """)

    df_shares = dataset.execute_spatial_query(query, sql_text)
    df_shares = df_shares.rename(columns=census_names)
    technologies = list(census_names.values())

    total = df_shares[technologies].sum(axis=1).iloc[0]
    if total and total != 0:
        df_shares[technologies] = df_shares[technologies] / total
    else:
        df_shares[technologies] = 0
    tech_shares = df_shares.iloc[0].to_dict()

    name_mapping = query.get("name_mapping", {})
    if name_mapping:
        mapped_shares = {v: tech_shares[k] for k,v in name_mapping.items() if v is not None}
        # ensure values sum to 1
        total_mapped = sum(mapped_shares.values())
        if total_mapped > 0:
            mapped_shares = {k: v / total_mapped for k, v in mapped_shares.items()}
        else:
            mapped_shares = {k: 0.0 for k in mapped_shares.keys()}

        tech_shares = mapped_shares

    return tech_shares

def get_data_reg_***REMOVED***_gauss() -> DataRegistry:
    gdf_bensheim = get_gdf_from_ags(["06431002"])

    ***REMOVED***_conn = DatabaseConnection(
        host="***REMOVED***",
        port=54328,
        database="***REMOVED***",
        user="***REMOVED***",
        password="***REMOVED***")
    print(f"***REMOVED*** on {***REMOVED***_conn.host} available: {***REMOVED***_conn.is_available()}")
    census_heating_shares = PostgreSQLDataset(
        keys=["heating_shares"],
        db_connection=***REMOVED***_conn,
        priority=5,
        query_function=***REMOVED***_gauss_census_query,
        regional_validity=gdf_bensheim)

    waermeatlas_residential_heat_demand = PostgreSQLDataset(
        keys=[DataKeys.RESIDENTIAL_HEAT_DEMAND],
        db_connection=***REMOVED***_conn,
        query_function=***REMOVED***_waermeatlas_heat_demand_query,
        unit=UnitEnum.MWH,
        priority=5,
        regional_validity=gdf_bensheim
    )

    data_reg = DataRegistry()
    data_reg.register(get_default_residential_heat_demand_profile_dataset())
    data_reg.register(get_default_residential_electricity_demand_profile_dataset())
    data_reg.register(get_default_residential_yearly_electricity_demand_dataset())
    data_reg.register(census_heating_shares)
    data_reg.register(waermeatlas_residential_heat_demand)
    return data_reg


if __name__ == "__main__":
    data_registry = get_data_reg_***REMOVED***_gauss()


    gdf = gpd.read_file("examples/bensheim/wah_bensheim_4_districts.geojson")
    polygon_bensheim = gdf.iloc[[1]].reset_index(drop=True).copy()
    test_query_1 = {"key": DataKeys.RESIDENTIAL_HEAT_DEMAND,
                  "region": polygon_bensheim}
    heat_demand = data_registry.query(test_query_1)

    test_query_2 = {"key": "heating_shares",
                  "region": polygon_bensheim}
    heating_shares = data_registry.query(test_query_2)

    check_registry(data_registry, polygon_bensheim)


    print(heat_demand)
    print(heating_shares)
