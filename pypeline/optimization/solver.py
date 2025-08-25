from abc import ABC

class OptimizationModel(ABC):
    def __init__(self, conversion_sub_processes, conversion_processes, commodities, tss):
        self.conversion_sub_processes = ...
        self.conversion_processes = ...
        self.commodities = ...
        self.tss = ...

    def optimize(self, model):
        """Optimize the provided model."""
        raise NotImplementedError("This method should be implemented by subclasses.")

class Solution:
    def __init__(self):
        self.energy_system = None
        self.scenario = None
        self.results = None
