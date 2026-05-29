import osm2geojson
import geopandas as gpd
import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {"User-Agent": "geopipe/1.0 (your@email.com)"}

def get_gdf_from_ags(ags_list: [str]):
    ags_list = [ags.strip().replace(" ", "") for ags in ags_list]
    ags_query = "|".join(ags_list)

    query = f"""
    [out:json];
    relation
      ["de:amtlicher_gemeindeschluessel"~"^({ags_query})"];
    out body geom;
    """

    response = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS)
    response.raise_for_status()

    overpass_json = response.json()
    geojson = osm2geojson.json2geojson(overpass_json)
    gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")
    gdf = gdf[["geometry"]]
    gdf = gdf.dissolve()
    return gdf


if __name__ == "__main__":
    # 06 = Hessen, 09 = Bayern, 06 4 31 002 = Bensheim
    ags_codes = ["06 4 31 002"]  # Example AGS codes
    gdf = get_gdf_from_ags(ags_codes)
    print(gdf.head())
    # gdf.to_file("output.geojson", driver="GeoJSON")
