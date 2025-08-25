from dataclasses import dataclass


@dataclass
class RegionConnection:
    region_id_in: int
    region_id_out: int
    connecting_commodity: list[str]
