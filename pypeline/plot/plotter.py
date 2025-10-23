import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from shapely.geometry import Point
import matplotlib.cm as cm

from pypeline.energy_system.energy_system import EnergySystem
from pypeline.optimization.solver import Results

class EnergySystemPlotter:
    def __init__(self, energy_system: EnergySystem):
        self.energy_system = energy_system

    def plot(self, demand_name: str | None = None, kind: str = "energy"):
        if kind not in ["energy", "power"]:
            raise ValueError("kind must be either 'energy' or 'power'")

        # Create a figure and axis
        fig, ax = plt.subplots(figsize=(10, 10))

        all_technologies = {r_tech.technology.name for region in self.energy_system.regions for r_tech in region.region_technologies}
        # sort alphabetically by key
        all_technologies = sorted(all_technologies)
        color_map = {tech: color for tech, color in zip(all_technologies, cm.Set2.colors)}

        # Plot each region's polygon
        for region in self.energy_system.regions:
            region.polygon.boundary.plot(ax=ax, edgecolor="black", zorder=1)
            region.polygon.plot(ax=ax, alpha=0.3, color="grey")

            # If demand_name is provided, process the demand data
            if demand_name:
                demand = region.get_demand(demand_name)
                if demand:
                    # Get the demand value (this will determine pie size)
                    demand_value = demand.value

                    # Get technologies that supply this demand
                    if kind == "energy":
                        tech_outputs = {
                            r_tech.technology.name: r_tech.initial_energy_output
                            for r_tech in region.region_technologies
                            if r_tech.technology.commodity_out == demand.demand.commodity_in
                        }
                    elif kind == "power":
                        tech_outputs = {
                            r_tech.technology.name: r_tech.initial_capacity
                            for r_tech in region.region_technologies
                            if r_tech.technology.commodity_out == demand.demand.commodity_in
                        }

                    # Get the centroid of the polygon for pie chart placement
                    centroid = region.polygon.geometry.iloc[0].centroid
                    if isinstance(centroid, Point):
                        x, y = centroid.x, centroid.y

                        # Calculate the pie radius based on demand_value
                        pie_radius = 20 + demand_value / 100000  # Adjust formula as needed

                        # Total output from all technologies
                        total_output = sum(tech_outputs.values())

                        # If there are technologies supplying this demand with non-zero output
                        if total_output > 0:
                            # Normalize the outputs for the pie chart
                            tech_outputs_normalized = {k: v / total_output for k, v in tech_outputs.items()}
                            sizes = list(tech_outputs_normalized.values())
                            colors = [color_map[tech] for tech in tech_outputs.keys()]
                        else:
                            # If no technology supplies this demand, show a single-colored pie
                            sizes = [1]
                            colors = ["darkgray"]  # Choose a color for demands without direct technology supply

                        # Draw the pie chart
                        wedge, _ = ax.pie(
                            sizes,
                            colors=colors,
                            radius=pie_radius,
                            center=(x, y),
                            frame=True,
                            textprops={"fontsize": 6},
                            wedgeprops={"width": pie_radius / 1.5, 'linewidth': 2, 'edgecolor': 'white'}
                        )

                        [w.set_zorder(2) for w in wedge]

                        # Add the demand value as text below the pie chart
                        if kind == "energy":
                            ax.text(
                                x, y - pie_radius - 10,
                                f"Demand: {demand_value / 1000:.0f} MWh",
                                ha="center",
                                fontsize=10,
                                zorder=3
                            )
                        elif kind == "power":
                            ax.text(
                                x, y - pie_radius - 10,
                                f"Demand: {demand_value:.2f} kW",
                                ha="center",
                                fontsize=10,
                                zorder=3
                            )

        # Add a legend for the technologies
        legend_elements = [Patch(facecolor=color, label=tech) for tech, color in color_map.items()]
        if demand_name and any(
                sum(tech_outputs.values()) == 0 for region in self.energy_system.regions if region.get_demand(demand_name)):
            # Add a legend entry for demands without technology supply
            legend_elements.append(Patch(facecolor="lightgray", label="Unsupplied Demand"))
        ax.legend(handles=legend_elements, loc="upper right", title="Technologies")

        # Set axis labels and title
        ax.set_xlabel("Longitude")
        ax.set_ylabel("Latitude")
        ax.set_title(
            f"Energy System: {self.energy_system.name} - Demand: {demand_name}" if demand_name else f"Energy System: {self.energy_system.name}")

        # Show the plot
        plt.show()



class ResultsPlotter:
    def __init__(self, results: Results):
        self.results = results

    def plot(self):
        ...