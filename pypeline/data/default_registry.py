import geopandas as gpd
import pandas as pd
from pypeline.data import (
    DataRegistry,
    DatabaseConnection,
    PostgreSQLDataset, CSVDataset,
)
from sqlalchemy import text

***REMOVED***_conn = DatabaseConnection(
    host="localhost",
    # host="ds1.example.com",
    port=54328,
    database="***REMOVED***",
    user="***REMOVED***",
    password="***REMOVED***"
)
print(***REMOVED***_conn.is_available())


def census_grid_query(dataset: PostgreSQLDataset, query: dict) -> dict[str, float]:
    if query["key"] != "heating_shares":
        raise ValueError("census_grid_query only supports 'heating_types' key")

    region: gpd.GeoDataFrame = query["region"]

    columns = ["heizoel", "biomasse_biogas", "strom", "fernwaerme", "gas",
               "holz_holzpellets", "solar_geothermie_waermepumpen", "kohle", "kein_energietraeger"]

    region_epsg = region.crs.to_epsg()
    region_geom_wkt = region.union_all().wkt

    columns_sql = ", ".join([f'COALESCE(SUM("{col}"), 0) AS "{col}"' for col in columns])

    sql_shares = text(f"""
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

    engine = dataset.db_connection.get_engine()
    df_shares = pd.read_sql(sql_shares, engine, params={"wkt": region_geom_wkt, "epsg": int(region_epsg)})
    total = df_shares[columns].sum(axis=1).iloc[0]
    if total and total != 0:
        df_shares[columns] = df_shares[columns] / total
    else:
        df_shares[columns] = 0
    heating_shares = df_shares.iloc[0].to_dict()

    return heating_shares

census_heating_dataset = PostgreSQLDataset(
        keys=["heating_shares"],
        db_connection=***REMOVED***_conn,
        priority=5,
        query_function=census_grid_query
    )

def ***REMOVED***_kwp_query(dataset: PostgreSQLDataset, query: dict) -> float:
    if query["key"] != "residential_heat_demand":
        raise ValueError("***REMOVED***_kwp_query only supports 'residential_heat_demand' key")

    region: gpd.GeoDataFrame = query["region"]

    region_epsg = region.crs.to_epsg()
    region_geom_wkt = region.union_all().wkt

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

    engine = dataset.db_connection.get_engine()
    df = pd.read_sql(sql_text, engine, params={"wkt": region_geom_wkt, "epsg": int(region_epsg)})

    row = df.iloc[0]
    total_demand = float(row["total_demand"])

    return total_demand

***REMOVED***_kwp_dataset = PostgreSQLDataset(
        keys=["residential_heat_demand"],
        db_connection=***REMOVED***_conn,
        priority=1,
        query_function=***REMOVED***_kwp_query
    )

residential_heat_demand_profile_dataset = CSVDataset(
        keys=["residential_heat_demand_profile"],
        file_path="data/D_Heat_Household_J.txt",
        pandas_kwargs={"sep": "\s+", "decimal": ".", "header": None},
        priority=1,
    )



DEFAULT_REGISTRY = DataRegistry()
DEFAULT_REGISTRY.register(census_heating_dataset)
DEFAULT_REGISTRY.register(***REMOVED***_kwp_dataset)
DEFAULT_REGISTRY.register(residential_heat_demand_profile_dataset)




if __name__ == "__main__":
    # Simple test query for debugging
    test_query_1 = {"key": "heating_shares",
                "region": gpd.read_file("../../data/polygon_neuburg.geojson")}
    test_query_2 = {"key": "residential_heat_demand",
                "region": gpd.read_file("../../data/polygon_neuburg.geojson")}
    test_query_3 = {"key": "residential_heat_demand_profile"}
    residential_heat_demand_profile_dataset.file_path = "../../data/D_Heat_Household_J.txt"
    data_1 = DEFAULT_REGISTRY.query(test_query_1)
    data_2 = DEFAULT_REGISTRY.query(test_query_2)
    data_3 = DEFAULT_REGISTRY.query(test_query_3)
    print(data_1)
    print(data_2)
    print(data_3)
    print("todo: introduce test for keys and required data format")


