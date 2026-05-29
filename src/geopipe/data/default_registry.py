import geopandas as gpd
import networkx as nx
import pandas as pd
from shapely.geometry import MultiPoint
from pathlib import Path
from geopipe.data import (
    DataRegistry,
    DatabaseConnection,
    PostgreSQLDataset, CSVDataset,
)
from sqlalchemy import text

from geopipe.data.dataset import SimpleDataset, FileDataset, CensusTechnology
from geopipe.units import UnitEnum


def ***REMOVED***_census_query(dataset: PostgreSQLDataset, query: dict) -> dict[str, float]:
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
    df_shares = df_shares.rename(columns=census_names)
    technologies = list(census_names.values())

    total = df_shares[technologies].sum(axis=1).iloc[0]
    if total and total != 0:
        df_shares[technologies] = df_shares[technologies] / total
    else:
        df_shares[technologies] = 0
    tech_shares = df_shares.iloc[0].to_dict()

    name_mapping = query.get("name_mapping", {})
    if query["name_mapping"]:
        mapped_shares = {v: tech_shares[k] for k,v in name_mapping.items() if v is not None}
        # ensure values sum to 1
        total_mapped = sum(mapped_shares.values())
        if total_mapped > 0:
            mapped_shares = {k: v / total_mapped for k, v in mapped_shares.items()}
        else:
            mapped_shares = {k: 0.0 for k in mapped_shares.keys()}

        tech_shares = mapped_shares

    return tech_shares


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


def _graph_region_total_heat_mwh(region: nx.Graph) -> float:
    seen: set = set()
    total_wh = 0.0
    for _, _, d in region.edges(data=True):
        sid = d.get("street_id")
        if sid is not None and sid in seen:
            continue
        if sid is not None:
            seen.add(sid)
        total_wh += float(d.get("total_heat_demand", 0.0))
    return total_wh / 1_000_000.0


class GraphHeatDemandDataset(SimpleDataset):
    """Graph based residential heat demand dataset"""

    def __init__(self):
        super().__init__(
            keys=["residential_heat_demand"],
            unit=UnitEnum.KWH,
            data=0.0,
            priority=6,
        )

    def _default_query(self, query: dict) -> float:
        if query["key"] != "residential_heat_demand":
            raise ValueError("GraphHeatDemandDataset only supports 'residential_heat_demand' key")

        region = query.get("region")
        if not isinstance(region, nx.Graph):
            raise ValueError("Requires region as nx.Graph")

        return _graph_region_total_heat_mwh(region)


def census_south_hessen_query(dataset: FileDataset, query: dict) -> dict[CensusTechnology, float]:
    if query["key"] != "heating_shares":
        raise ValueError("census_south_hessen_query only supports 'heating_shares' key")

    census_names = {   "Gas": CensusTechnology.Gas,
                       "Heizoel": CensusTechnology.Oil,
                       "Holz_Holzpellets": CensusTechnology.Wood,
                       "Biomasse_Biogas": CensusTechnology.Biomass,
                       "Solar_Geothermie_Waermepumpen": CensusTechnology.Renewable,
                       "Strom": CensusTechnology.Electric,
                       "Kohle": CensusTechnology.Coal,
                       "Fernwaerme": CensusTechnology.District_Heating,
                       "kein_Energietraeger": CensusTechnology.NoEnergyCarrier}

    region = query["region"]
    if isinstance(region, nx.Graph):
        region_crs = region.graph.get("crs")
        boundary_gdf = gpd.GeoDataFrame(
            geometry=[MultiPoint(list(region.nodes)).convex_hull], crs=region_crs
        )
    elif isinstance(region, gpd.GeoDataFrame):
        boundary_gdf = gpd.GeoDataFrame(
            geometry=[region.geometry.union_all()],
            crs=region.crs,
        )
        region_crs = boundary_gdf.crs
    else:
        raise TypeError("census_south_hessen_query expects region as nx.Graph or GeoDataFrame")

    gdf = dataset.get_data()
    if region_crs is not None and gdf.crs != region_crs:
        gdf = gdf.to_crs(region_crs)

    gdf_in_region = gpd.sjoin(gdf, boundary_gdf, predicate="intersects", how="inner")
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


def _create_default_datasets(
    *,
    mode: str,
    local_heating_shares_file: str | Path | None,
) -> list:
    ***REMOVED***_conn = DatabaseConnection(
        host="localhost",
        # host="ds1.example.com",
        port=54328,
        database="***REMOVED***",
        user="***REMOVED***",
        password="***REMOVED***"
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

    datasets = [
        residential_heat_demand_profile_dataset,
        residential_electricity_demand_profile_dataset,
        residential_yearly_electricity_demand,
    ]

    if mode == "***REMOVED***":
        ***REMOVED***_available = ***REMOVED***_conn.is_available()
        print(f"***REMOVED*** on {***REMOVED***_conn.host} available: {***REMOVED***_available}")
        if not ***REMOVED***_available:
            raise ConnectionError("infDB is not reachable. Retry or pass local files")

        datasets.insert(0, PostgreSQLDataset(
            keys=["heating_shares"],
            db_connection=***REMOVED***_conn,
            priority=5,
            query_function=***REMOVED***_census_query,
            regional_validity=None,
        ))
        datasets.insert(1, PostgreSQLDataset(
            keys=["residential_heat_demand"],
            unit=UnitEnum.KWH,
            db_connection=***REMOVED***_conn,
            priority=5,
            query_function=***REMOVED***_kwp_query,
            regional_validity=None,
        ))
        return datasets

    if local_heating_shares_file is None:
        raise ValueError(
            "Local mode requires explicit local_heating_shares_file."
        )

    shares_file = Path(local_heating_shares_file)
    if not shares_file.exists():
        raise FileNotFoundError(f"Local heating shares file not found: {shares_file}")

    datasets.insert(0, GraphHeatDemandDataset())

    datasets.insert(1, FileDataset(
        keys=["heating_shares"],
        file_path=str(shares_file),
        query_function=census_south_hessen_query,
        priority=6,
        regional_validity=None,
    ))
    return datasets


def get_default_data_registry(
    *,
    mode: str,
    local_heating_shares_file: str | Path | None = None,
) -> DataRegistry:
    """
    Builds a DataRegistry.

    Modes:
    - '***REMOVED***': infDB-backed demand/heating-shares datasets.
    - 'local': local-file demand/heating-shares datasets.

    Strict behavior: local requires explicit heating-shares file path.
    Residential heat demand is resolved from graph edge attributes in local mode.
    """
    if mode not in {"***REMOVED***", "local"}:
        raise ValueError("mode must be one of: '***REMOVED***', 'local'")

    registry = DataRegistry()
    for dataset in _create_default_datasets(
        mode=mode,
        local_heating_shares_file=local_heating_shares_file,
    ):
        registry.register(dataset)

    return registry


if __name__ == "__main__":
    # Simple test query for debugging
    test_query_1 = {"key": "heating_shares",
                "region": gpd.read_file("../../data/polygon_neuburg.geojson")}
    test_query_2 = {"key": "residential_heat_demand",
                "region": gpd.read_file("../../data/polygon_neuburg.geojson")}
    test_query_3 = {"key": "residential_heat_demand_profile"}
    test_query_4 = {"key": "heating_shares", "region": gpd.read_file("../../data/baublock_bensheim_epsg25832.geojson")}

    registry = get_default_data_registry(
        mode="local",
        local_heating_shares_file="data/Census2022HeatingType100mGrid/Census2022HeatingType100mGrid_Polygons_southhessen.geojson",
    )
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
