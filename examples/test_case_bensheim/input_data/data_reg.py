import pathlib

import networkx as nx
import pandas as pd
import geopandas as gpd
from geopipe.data import PostgresConnection, PostgresDataset
from shapely.geometry.multipoint import MultiPoint

from geopipe import DataRegistry
from geopipe.data.data_registry import DataKeys, DataRegistryQuery
from geopipe.data.data_utils import get_gdf_from_ags
from geopipe.data.dataset import Dataset, FileDataset, CensusTechnology, StreetValueDataset
from geopipe.energy_system.units import UnitEnum
from geopipe.topology_builder.topology import Topology


def census_bensheim_query(dataset: Dataset, topology: Topology, query: DataRegistryQuery) -> dict[CensusTechnology, float]:
    if query.key != DataKeys.HEATING_SHARES:
        raise ValueError("census_bensheim_query only supports 'heating_shares' key")

    census_names = {   "Gas": CensusTechnology.Gas,
                       "Heizoel": CensusTechnology.Oil,
                       "Holz_Holzpellets": CensusTechnology.Wood,
                       "Biomasse_Biogas": CensusTechnology.Biomass,
                       "Solar_Geothermie_Waermepumpen": CensusTechnology.Renewable,
                       "Strom": CensusTechnology.Electric,
                       "Kohle": CensusTechnology.Coal,
                       "Fernwaerme": CensusTechnology.District_Heating,
                       "kein_Energietraeger": CensusTechnology.NoEnergyCarrier}


    gdf_in_region = dataset.fetch(topology.convex_hull, query)
    gdf_in_region = gdf_in_region.rename(columns=census_names)

    technologies = list(census_names.values())

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

    tech_shares = {k: float(v) for k, v in tech_shares.items()}

    name_mapping = query.params.get("name_mapping", {})
    if name_mapping:
        # accumulate so multiple census technologies mapped to the same
        # target name are summed instead of overwriting each other
        mapped_shares: dict[str, float] = {}
        for k, v in name_mapping.items():
            if v is None:
                continue
            mapped_shares[v] = mapped_shares.get(v, 0.0) + tech_shares[k]
        # ensure values sum to 1
        total_mapped = sum(mapped_shares.values())
        if total_mapped > 0:
            mapped_shares = {k: v / total_mapped for k, v in mapped_shares.items()}
        else:
            mapped_shares = {k: 0.0 for k in mapped_shares.keys()}

        tech_shares = mapped_shares

    return tech_shares

def heat_grid_bensheim_query(dataset: FileDataset, topology: Topology, query: DataRegistryQuery) -> pd.DataFrame:
    street_segments: gpd.GeoDataFrame = query.params["segments"]
    heat_grid_data: gpd.GeoDataFrame = dataset.get_data()  # already in the project CRS
    # 1. Buffer around the heat grid geometries
    buffer_distance = 50  # meters
    heat_grid_buffer = heat_grid_data.buffer(buffer_distance).union_all()
    # 2. Street segments which are at least 60 percent covered by the buffer are considered to have an existing grid
    existing_grid_ratio = 0.6 # if 60% of the street segment is covered by the buffer, it is considered to have an existing grid
    covered = street_segments.geometry.intersection(heat_grid_buffer).length
    seg_len = street_segments.geometry.length
    result = street_segments[[]].copy()  # keep index, drop columns
    result["existing_grid"] = (
        (covered / seg_len.replace(0, pd.NA)) >= existing_grid_ratio
    ).fillna(False).astype(bool)
    return result


def case1_data_registry(streets: gpd.GeoDataFrame) -> DataRegistry:
    # db_conn = PostgresConnection.from_env("INFDBGAUSS")
    # heating_shares = PostgresDataset(
    #     keys=[DataKeys.HEATING_SHARES],
    #     db_connection=db_conn,
    #     query_function=census_bensheim_query,
    #     sql =   """
    #             SELECT
    #                 SUM(c."gas")::float                          AS "Gas",
    #                 SUM(c."heizoel")::float                      AS "Heizoel",
    #                 SUM(c."holz_holzpellets")::float             AS "Holz_Holzpellets",
    #                 SUM(c."biomasse_biogas")::float              AS "Biomasse_Biogas",
    #                 SUM(c."solar_geothermie_waermepumpen")::float AS "Solar_Geothermie_Waermepumpen",
    #                 SUM(c."strom")::float                        AS "Strom",
    #                 SUM(c."kohle")::float                        AS "Kohle",
    #                 SUM(c."fernwaerme")::float                   AS "Fernwaerme",
    #                 SUM(c."kein_energietraeger")::float          AS "kein_Energietraeger"
    #             FROM opendata.zensus_2022_100m_energietraeger_heizung AS c
    #             WHERE ST_Intersects(
    #                 c.geom,
    #                 ST_Transform(ST_GeomFromText(:wkt, :epsg), ST_SRID(c.geom))
    #             )
    #             """
    # )

    heating_shares = FileDataset(
        keys=[DataKeys.HEATING_SHARES],
        file_path= str(pathlib.Path(__file__).parent / "Census2022HeatingType100mGrid.geojson"),
        query_function=census_bensheim_query,
        )

    heat_grid_bensheim = FileDataset(
        keys=[DataKeys.EXISTING_HEAT_GRID],
        file_path=str(pathlib.Path(__file__).parent / "heat_grid_bensheim.geojson"),
        query_function=heat_grid_bensheim_query,
        priority=10,
        scope=None
    )

    residential_heat_demand = StreetValueDataset.from_column(
        streets, id_column="fid", value_column="waerme_mwh",
        keys=[DataKeys.RESIDENTIAL_HEAT_DEMAND], unit=UnitEnum.MWH)

    pool_heat_demand = StreetValueDataset(values={"1173": 800.0}, keys=["pool_heat_demand"], unit=UnitEnum.MWH)


    data_reg = DataRegistry(crs="EPSG:25832")
    data_reg.register(heating_shares)
    data_reg.register(heat_grid_bensheim)
    data_reg.register(residential_heat_demand)
    data_reg.register(pool_heat_demand)
    return data_reg

if __name__ == "__main__":
    data_reg = case1_data_registry(
        gpd.read_file(pathlib.Path(__file__).parents[1] / "private_data" / "bensheim_streets_heat_demand.geojson"))