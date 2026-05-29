import warnings

# cesm.core.input_parser imports the deprecated pkg_resources, which emits a
# UserWarning on import (setuptools >= 80). Suppress it here, before the cesm
# import below fires, so every entry point using the CESM backend stays quiet.
warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API",
)

from geopipe.optimization.cesm.backend import CESMOptimizationBackend
from geopipe.optimization.cesm.cesm_plotter import CesmPlotter, PlotType

