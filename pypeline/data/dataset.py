# === Global Dataset Registry ===
from abc import ABC, abstractmethod
from typing import Any

import geopandas as gpd


class Dataset(ABC):
    def __init__(self, types: list[str], path: str):
        self.types: list[str] = types
        self.path = path
        self.data = self.load_data()

    @abstractmethod
    def load_data(self) -> Any:
        raise NotImplementedError

    @abstractmethod
    def query(self, query: dict) -> Any:
        raise NotImplementedError

    def is_in_region(self, region: gpd.GeoDataFrame) -> bool:
        return True  # Default implementation, can be overridden in subclasses

    def is_type(self, type_: str) -> bool:
        return type_ in self.types

    def check(self, type_: str, region: gpd.GeoDataFrame = None):
        if not self.is_type(type_) or not self.is_in_region(gpd.GeoDataFrame()):
            raise ValueError(f"Dataset {self.__class__.__name__} is not applicable for type '{type_}'. in the region {region}.")

class SpatialDataset(Dataset, ABC):
    def __init__(
        self,
        types: list[str],
        path: str,
        crs: str = None,
        regional_validity: gpd.GeoDataFrame = None,
    ):
        self.crs = crs
        self.regional_validity = regional_validity
        super().__init__(types=types, path=path)

    def is_in_region(self, region: gpd.GeoDataFrame) -> bool:
        if self.regional_validity is not None:
            if region.crs != self.regional_validity.crs:
                region = region.to_crs(self.regional_validity.crs)
            # Check that all region geometries are within the validity area
            return self.regional_validity.unary_union.contains(region.unary_union)
        return True

class TemporalDataset(Dataset, ABC):
    def __init__(self, types: list[str], path: str, time_resolution: str = "hourly"):
        self.time_resolution = time_resolution
        super().__init__(types=types, path=path)

    def check(self, type_: str, **kwargs):
        super().check(type_)



class SpatialTemporalDataset(SpatialDataset, TemporalDataset, ABC):
    def __init__(self, types: list[str], path: str, crs: str = None, regional_validity: gpd.GeoDataFrame = None,
                 time_resolution: str = "hourly"):
        SpatialDataset.__init__(self, types=types, path=path, crs=crs, regional_validity=regional_validity)
        self.time_resolution = time_resolution  # override if needed

    def check(self, type_: str, region: gpd.GeoDataFrame, **kwargs):
        super().check(type_=type_, region=region)




