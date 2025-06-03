from abc import ABC


class Technology(ABC):
    def __init__(self, commodity_in: str,
                 commodity_out: str,
                 efficiency: float = 1.0,
                technical_lifetime: int = 100,
                opex_cost_energy: float = 0,
                opex_cost_power: float = 0,
                capex_cost_power: float = 0):
        self.commodity_in = commodity_in
        self.commodity_out = commodity_out
        self.efficiency = efficiency
        self.technical_lifetime = technical_lifetime
        self.opex_cost_energy = opex_cost_energy
        self.opex_cost_power = opex_cost_power
        self.capex_cost_power = capex_cost_power
        



class IndividualTechnology(Technology):
    ...

class CentralTechnology(Technology):
    ...

class HeatGrid(Technology):
    ...

class GridConnection(Technology):
    ...

class Demand(Technology):
    ...


class TechnologyService:
    _individual_technologies = {}
    _central_technologies = {}
    _heat_grids = {}
    _grid_connections = {}

    @classmethod
    def register_individual_technology(cls, technology_name: str):
        def decorator(technology_class):
            if not issubclass(technology_class, IndividualTechnology):
                raise TypeError(f"Class {technology_class.__name__} must inherit from IndividualTechnology.")
            if technology_name in cls._individual_technologies:
                raise ValueError(f"Individual technology '{technology_name}' already registered.")
            cls._individual_technologies[technology_name] = technology_class
            return technology_class
        return decorator

    @classmethod
    def register_central_technology(cls, technology_name: str):
        def decorator(technology_class):
            if not issubclass(technology_class, CentralTechnology):
                raise TypeError(f"Class {technology_class.__name__} must inherit from CentralTechnology.")
            if technology_name in cls._central_technologies:
                raise ValueError(f"Central technology '{technology_name}' already registered.")
            cls._central_technologies[technology_name] = technology_class
            return technology_class
        return decorator

    @classmethod
    def register_heat_grid(cls, heat_grid_name: str):
        def decorator(heat_grid_class):
            if not issubclass(heat_grid_class, HeatGrid):
                raise TypeError(f"Class {heat_grid_class.__name__} must inherit from HeatGrid.")
            if heat_grid_name in cls._heat_grids:
                raise ValueError(f"Heat grid '{heat_grid_name}' already registered.")
            cls._heat_grids[heat_grid_name] = heat_grid_class
            return heat_grid_class
        return decorator

    @classmethod
    def register_grid_connection(cls, connection_name: str):
        def decorator(connection_class):
            if not issubclass(connection_class, GridConnection):
                raise TypeError(f"Class {connection_class.__name__} must inherit from GridConnection.")
            if connection_name in cls._grid_connections:
                raise ValueError(f"Grid connection '{connection_name}' already registered.")
            cls._grid_connections[connection_name] = connection_class
            return connection_class
        return decorator

    def get_individual_technology(self, technology_name: str):
        return self._individual_technologies.get(technology_name)

    def get_central_technology(self, technology_name: str):
        return self._central_technologies.get(technology_name)

    def get_heat_grid(self, heat_grid_name: str):
        return self._heat_grids.get(heat_grid_name)

    def get_grid_connection(self, connection_name: str):
        return self._grid_connections.get(connection_name)