import geopandas as gpd
from shapely.geometry import LineString
from pypeline.energy_system.region_topology import _build_street_adjacency


def _streets_from_lines(lines):
    return gpd.GeoDataFrame(
        {"_street_key": list(range(len(lines))), "geometry": lines},
        geometry="geometry",
        crs="EPSG:25832",
    )



def junction_rule_t():
    """Checks overloaded junction neighbor rule activates only beyond three-way intersections."""
    streets = _streets_from_lines(
        [
            LineString([(-10.0, 0.0), (10.0, 0.0)]),
            LineString([(0.0, -10.0), (0.0, 10.0)]),
            LineString([(-10.0, -10.0), (10.0, 10.0)]),
            LineString([(-10.0, 10.0), (10.0, -10.0)]),
        ]
    )

    adj = _build_street_adjacency(
        streets,
        street_key_col="_street_key",
        tolerance_m=0.0,
    )

    # Without the neighbor-only rule this would be a full K4 graph.
    assert len(adj[0]) == 2
    assert len(adj[1]) == 2
    assert len(adj[2]) == 2
    assert len(adj[3]) == 2


def three_way_ok_t():
    """Checks three-way junction connectivity remains unrestricted by overload filtering."""
    streets = _streets_from_lines(
        [
            LineString([(-10.0, 0.0), (10.0, 0.0)]),
            LineString([(0.0, -10.0), (0.0, 10.0)]),
            LineString([(-10.0, -10.0), (10.0, 10.0)]),
        ]
    )

    adj = _build_street_adjacency(
        streets,
        street_key_col="_street_key",
        tolerance_m=0.0,
    )

    # N == 3 should stay unconstrained by the overloaded-junction rule.
    assert adj[0] == {1, 2}
    assert adj[1] == {0, 2}
    assert adj[2] == {0, 1}

