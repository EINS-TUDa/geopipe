import warnings

# cesm.core.input_parser imports the deprecated pkg_resources, which emits a
# UserWarning on import (setuptools >= 80). Suppress it here, before the cesm
# import below fires, so every entry point using the CESM backend stays quiet.
warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API",
)

from pypeline.optimization.cesm.backend import CESMOptimizationBackend
from pypeline.optimization.cesm.cesm_plotter import CesmPlotter, PlotType

