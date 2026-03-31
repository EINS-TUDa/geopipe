import osm2geojson
import geopandas as gpd
import json

def get_gdf_from_ags(ags_list: [str]):
    """
    Given a list of AGS (amtlicher Gemeindeschlüssel) codes, return a GeoDataFrame with the corresponding geometries. Uses Overpass API.
    """
    # remove whitespaces from ags_list
    ags_list = [ags.strip().replace(" ", "") for ags in ags_list]
    ags_query = "|".join(ags_list)

    query = f"""
    [out:json];
    relation
      ["de:amtlicher_gemeindeschluessel"~"^({ags_query})"];
    out body geom;
    """

    overpass_json = osm2geojson.overpass_call(query)
    overpass_json = json.loads(overpass_json)
    geojson = osm2geojson.json2geojson(overpass_json)
    gdf = gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")
    gdf = gdf[["geometry"]]
    gdf = gdf.dissolve() # dissolve to a single geometry
    return gdf


if __name__ == "__main__":
    # 06 = Hessen, 09 = Bayern, 06 4 31 002 = Bensheim
    ags_codes = ["06 4 31 002"]  # Example AGS codes
    gdf = get_gdf_from_ags(ags_codes)
    print(gdf.head())
    # gdf.to_file("output.geojson", driver="GeoJSON")
