import geopandas as gpd
import pandas as pd
from pypeline.data import (
    DataRegistry,
    DatabaseConnection,
    PostgreSQLDataset,
)
from sqlalchemy import text

***REMOVED***_conn = DatabaseConnection(
    # host="localhost",
    host="ds1.example.com",
    port=54328,
    database="***REMOVED***",
    user="***REMOVED***",
    password="***REMOVED***"
)
print(***REMOVED***_conn.is_available())


def census_grid_query(dataset: PostgreSQLDataset, query: dict) -> "pd.DataFrame":
    if query["key"] != "heating_shares":
        raise ValueError("census_grid_query only supports 'heating_types' key")

    region: gpd.GeoDataFrame = query["region"]

    columns = ["heizoel", "biomasse_biogas", "strom", "fernwaerme", "gas",
               "holz_holzpellets", "solar_geothermie_waermepumpen", "kohle", "kein_energietraeger"]

    region_epsg = region.crs.to_epsg() if region.crs else 4326
    region_geom_wkt = region.union_all().wkt

    # Create SQL with SUM aggregation for all columns
    columns_sql = ", ".join([f'SUM("{col}") as "{col}"' for col in columns])

    sql = f"""
    SELECT {columns_sql}
    FROM need.zensus_2022_100m_energietraeger
    WHERE ST_Within(
        ST_Transform(geometry, {region_epsg}),
        ST_GeomFromText('{region_geom_wkt}', {region_epsg})
    )
    """

    engine = dataset.db_connection.get_engine()
    df = pd.read_sql(text(sql), engine)

    return df


census_heating_dataset = PostgreSQLDataset(
        keys=["heating_shares"],
        db_connection=***REMOVED***_conn,
        priority=5,
        query_function=census_grid_query
    )


DEFAULT_REGISTRY = DataRegistry()
DEFAULT_REGISTRY.register(census_heating_dataset)

# Simple test query for debugging
# Note: this will attempt a DB connection; keep commented out in non-db environments
test_query = {"key": "heating_shares",
         "region": gpd.read_file("../../data/polygon_neuburg.geojson")
}
data = DEFAULT_REGISTRY.query(test_query)
print(data)
