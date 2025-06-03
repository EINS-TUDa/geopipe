from collections import defaultdict
from typing import Type

from core.energy_system.technology import Technology


class TechnologyRegistry:
    def __init__(self):
        # Group technologies by their class (e.g., IndividualTechnology, Demand, etc.)
        self._technologies: dict[Type[Technology], list[Technology]] = defaultdict(list)

    def register(self, tech: Technology, base_class: type[Technology]):
        if not isinstance(tech, base_class):
            raise TypeError(f"{tech} is not an instance of {base_class}")
        if tech not in self._technologies[base_class]:
            self._technologies[base_class].append(tech)
        else:
            raise ValueError(f"Technology {tech.name} already registered under {base_class.__name__}")

    def get_by_class(self, cls: Type[Technology]) -> list[Technology]:
        return self._technologies.get(cls, [])

    def get_by_class_and_name(self, cls: Type[Technology], name: str) -> Technology | None:
        for tech in self._technologies.get(cls, []):
            if tech.name == name:
                return tech
        return None

    def all(self) -> list[Technology]:
        return [tech for techs in self._technologies.values() for tech in techs]

    def load_from_default(self, allowed_classes: list[Type[Technology]] = None):
        """
        Load technologies from the global registry, filtering by allowed classes if specified.
        """
        for tech_class, techs in DEFAULT_TECHNOLOGY_REGISTRY._technologies.items():
            if allowed_classes is None or tech_class in allowed_classes:
                for tech in techs:
                    self.register(tech, tech_class)


DEFAULT_TECHNOLOGY_REGISTRY = TechnologyRegistry()

def default_technology_registry(base_class: type[Technology]):
    def decorator(cls_or_instance):
        instance = cls_or_instance() if isinstance(cls_or_instance, type) else cls_or_instance
        DEFAULT_TECHNOLOGY_REGISTRY.register(instance, base_class)
        return cls_or_instance
    return decorator




