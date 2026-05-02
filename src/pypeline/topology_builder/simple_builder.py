# coding=utf-8
"""Simple scenario-driven topology builder.

Owns YAML-based region assignment and optional injection handling.
Does not own generic graph conversion primitives.
"""

from collections import defaultdict
from pathlib import Path
from typing import Any

import geopandas as gpd
import networkx as nx
import yaml
import logging

from pypeline.topology_builder.core import TopologyBuildResult, gdf_to_nx

logger = logging.getLogger(__name__)


def load_yaml(config_file: Path) -> dict[str, Any]:
    if not config_file.exists():
        raise FileNotFoundError(f"Scenario file not found: {config_file}")
    with config_file.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping root in {config_file}, got {type(data).__name__}")
    return data


def modify_streets_data(streets_data: gpd.GeoDataFrame | Path,
                        modifications_file: Path, ) -> gpd.GeoDataFrame:
    """Modify the data of a geopandas GeoDataFrame according to the specifications in a YAML file.

    The yaml file mus have the following structure. The elements in the GeoDataFrame is described with a column name
    and a row specifier which can be either the row index or the entry in another column.
    For each entry, there are two actions supported: add and replace.

    .. code-block:: yaml
        column_name:  # Name of the column where to change
          - index: 0  # Index of the row where to change
            add: 50
          - column_name: value_in_column  # Specifier of the row where to change, e.g. column_name: value_in_column
            replace: 100

    Parameters
    ----------
    streets_data : gpd.GeoDataFrame | Path
        A GeoDataFrame containing the street data or a path to a file that can be read into a GeoDataFrame.
    modifications_file : Path
        A path to a YAML file that specifies the modifications to be made to the GeoDataFrame.

    Returns
    -------
    modified_streets_data : gpd.GeoDataFrame
    """
    if isinstance(streets_data, Path):
        streets_data = gpd.read_file(streets_data)
    streets_data: gpd.GeoDataFrame

    modifications_data = load_yaml(modifications_file)

    for column_name, mod_data_per_street in modifications_data.items():
        column_name: str
        mod_data_per_street: list[dict[str, Any]]
        for mod_data in mod_data_per_street:
            mod_data: dict[str, Any]
            if len(mod_data) != 2:
                raise ValueError()

            add_value = mod_data.pop("add", None)
            replace_value = mod_data.pop("replace", None)

            if add_value is None and replace_value is None:
                raise ValueError()
            if add_value is not None and replace_value is not None:
                raise ValueError()

            [(key, value)] = mod_data.items()
            if key == "index":
                row_specifier = streets_data.index == value
            else:
                row_specifier = streets_data[key] == value

            value = streets_data.loc[row_specifier, column_name].iloc[0]

            if add_value is not None:
                value = value + add_value
            if replace_value is not None:
                value = replace_value

            streets_data.loc[row_specifier, column_name] = value

    return streets_data


def build_simple_topology(streets_data: gpd.GeoDataFrame | Path,
                          grouping: dict[str, dict[str, list[Any]]] | Path,
                          region_id_column: str,
                          default_region: Any = None) -> TopologyBuildResult:
    """Build a topology according to the specifies grouping.

    The grouping is either a dict mapping a region name to a row selector or a yaml file that evaluates to such a dict.
    For example:

    .. code-block:: yaml
        region_1:
          index:
            - 0
            - 1
          street_id:
            - DEHE04620000AGYW
            - DEHE04620000AGYV
        region_2:
          name:
            - "Main Street"

    For each region, the row selector specifies which rows are in the region.
    In the example, region_1 contains all rows with index 0 and 1, and also those rows with specified "street_id".
    Region_2 on the other hand contains all rows that have the name "Main Street".

    Parameters
    ----------
    streets_data: gpd.GeoDataFrame | Path
        A GeoDataFrame containing the street data or a path to a file that can be read into a GeoDataFrame.
    grouping: dict[str, dict[str, list[Any]]] | Path
        A dict mapping region names to row selectors or a path to a YAML file that evaluates to such a dict.
    region_id_column: str
        The column name where to write the region
    default_region: Any
        The default value to take for a region.

    Returns
    -------
    TopologyBuildResult
    """
    if isinstance(streets_data, Path):
        streets_data = gpd.read_file(streets_data)
    streets_data: gpd.GeoDataFrame

    if isinstance(grouping, Path):
        grouping = load_yaml(grouping)
    grouping: dict[str, dict[str, list[Any]]]

    if region_id_column in streets_data:
        logger.warning("Overwriting existing column '%s' in streets data with region assignments", region_id_column)

    streets_data[region_id_column] = default_region
    for region_name, data in grouping.items():
        for column_name, values in data.items():
            if column_name == "index":
                streets_data.loc[streets_data.index.isin(values), region_id_column] = region_name
            else:
                streets_data.loc[streets_data[column_name].isin(values), region_id_column] = region_name

    street_network = gdf_to_nx(streets_data)

    topologies = defaultdict(nx.Graph)
    for u, v, data in street_network.edges(data=True):
        topologies[data.get(region_id_column)].add_edge(u, v, **data)

    # Todo: Check if the street segments of a region are connected
    return TopologyBuildResult(network=street_network,
                               region_topologies=topologies,
                               streets=streets_data)
