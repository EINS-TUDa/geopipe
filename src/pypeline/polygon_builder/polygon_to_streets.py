import geopandas as gpd

def polygons_to_streets(polygons: gpd.GeoDataFrame, streets: gpd.GeoDataFrame):
    """
    Converts polygons to streets by intersecting the street network with the polygon boundaries.

    Args:
        polygons: GeoDataFrame containing the polygons with a 'geom' column of type Polygon.
        streets: GeoDataFrame containing the street segments with a 'geom' column of type LineString.

    Returns:
        GeoDataFrame containing the street segments that intersect with the polygon boundaries.
    """

    streets = streets.to_crs(polygons.crs)

    streets["within_polygon"] = None
    for polygon in polygons.itertuples():
        hits = streets.geometry.within(polygon.geometry)
        for index in hits[hits].index:
            if streets["within_polygon"].iloc[index] is not None:
                print(f"Warning: Street segment {index} is within multiple polygons (current: {streets['within_polygon'].iloc[index]}, new: {polygon.id})")
            streets.loc[index, "within_polygon"] = polygon.id

    return streets