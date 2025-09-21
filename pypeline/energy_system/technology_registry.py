from collections import defaultdict
from typing import Type

from pypeline.energy_system.technology import Technology


class TechnologyNotFoundError(Exception):
    def __init__(self, cls: Type[Technology], name: str):
        super().__init__(f"Technology with name '{name}' not found.")
        self.cls = cls
        self.name = name


class TechnologyRegistry:
    def __init__(self):
        # Group technologies by their class (e.g., IndividualTechnology, Demand, etc.)
        self._technologies: dict[str, Technology] = {}

    def register(self, tech: Technology):
        if not isinstance(tech, Technology):
            raise TypeError(f"{tech} is not an instance of Technology")
        if tech.name not in self._technologies:
            self._technologies[tech.name] = tech
        else:
            raise ValueError(f"Technology with name {tech.name} already registered")

    def get_by_name(self, name: str) -> Technology:
        if name not in self._technologies:
            raise TechnologyNotFoundError(Technology, name)
        return self._technologies[name]

    def get_names(self) -> list[str]:
        """
        Returns a list of all technology names registered in the registry.
        """
        return list(self._technologies.keys())

    def get_by_output_commodity(self, commodity: str, return_type: str = "instance") -> list[Technology] | list[str]:
        """
        return_type: can be "name" or "instance"
        Returns a list of technologies that produce the specified commodity.
        """
        if return_type not in ["name", "instance"]:
            raise ValueError("return_type must be either 'name' or 'instance'")
        if return_type == "name":
            return [tech.name for tech in self._technologies.values() if tech.commodity_out == commodity]
        if return_type == "instance":
            return [tech for tech in self._technologies.values() if tech.commodity_out == commodity]

    def get_all(self, return_type: str = "instance") -> list[Technology] | list[str]:
        """:return_type: can be "name" or "instance"""
        if return_type not in ["name", "instance"]:
            raise ValueError("return_type must be either 'name' or 'instance'")
        if return_type == "name":
            return list(self._technologies.keys())
        if return_type == "instance":
            return list(self._technologies.values())

    def load_from_default(self):
        """
        Load technologies from the global registry, filtering by allowed classes if specified.
        """
        if not DEFAULT_TECHNOLOGY_REGISTRY.get_all():
            raise ValueError("No technologies registered in the default technology registry.")
        for tech in DEFAULT_TECHNOLOGY_REGISTRY.get_all():
            self.register(tech)

    def remove(self, name: str):
        """
        Remove a technology by its name.
        """
        if name in self._technologies:
            del self._technologies[name]
        else:
            raise TechnologyNotFoundError(Technology, name)

    def has_technology(self, name: str) -> bool:
        """
        Verify if a technology with the given name exists in the registry.
        Returns True if it exists, False otherwise.
        """
        return name in self._technologies
    
    def subregistry_by_flags(self, flags: dict[str, bool]) -> "TechnologyRegistry":
        """
        Create a new registry that only contains technologies whose name is True in `flags`.
        Missing names are ignored. Order/instances are preserved.
        """
        sub = TechnologyRegistry()
        for name, tech in self._technologies.items():
            if flags.get(name, False):
                sub.register(tech)
        return sub


DEFAULT_TECHNOLOGY_REGISTRY = TechnologyRegistry()

def default_technology_registry(cls):
    DEFAULT_TECHNOLOGY_REGISTRY.register(cls())
    return cls




