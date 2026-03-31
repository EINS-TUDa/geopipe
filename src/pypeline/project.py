import logging
from pathlib import Path


class PypelineProject:
    """Manages directory and file paths for a Pypeline project."""

    def __init__(self, project_root: Path, project_name: str, log_level: int = logging.DEBUG):
        self._project_root = project_root
        self._project_dir = project_root / "examples" / project_name
        self._input_data_dir = self._project_dir / "input_data"
        self._output_data_dir = self._project_dir / "output_data"
        self._plots_dir = self._output_data_dir / "plots"
        self._global_data_dir = project_root / "data" # should be obsolete in near future

        self.check_directories()
        self._setup_logging(project_name, log_level)

    def _setup_logging(self, project_name: str, log_level: int) -> None:
        """Configure logging: basicConfig for root, suppress third-party loggers."""
        log_file = self._project_dir / f"{project_name}.log"
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(log_file, mode="w", encoding="utf-8"),
            ],
        )
        # all loggers default to WARNING, pypeline logger uses specified log_level
        logging.getLogger().setLevel(logging.WARNING)
        logging.getLogger("pypeline").setLevel(log_level)

    def check_directories(self) -> None:
        """Check that all directories exist, create output directories if needed."""
        if not self._project_dir.exists():
            raise FileNotFoundError(f"Project directory not found: {self._project_dir}")
        if not self._input_data_dir.exists():
            raise FileNotFoundError(f"Input data directory not found: {self._input_data_dir}")
        if not self._global_data_dir.exists():
            raise FileNotFoundError(f"Data directory not found: {self._global_data_dir}")
        if not self._output_data_dir.exists():
            self._output_data_dir.mkdir(parents=True, exist_ok=True)
        if not self._plots_dir.exists():
            self._plots_dir.mkdir(parents=True, exist_ok=True)

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    @property
    def input_data_dir(self) -> Path:
        return self._input_data_dir

    @property
    def output_data_dir(self) -> Path:
        return self._output_data_dir

    @property
    def plots_dir(self) -> Path:
        return self._plots_dir

    @property
    def global_data_dir(self) -> Path:
        return self._global_data_dir

    def input_file(self, filename: str) -> Path:
        return self._input_data_dir / filename

    def output_file(self, filename: str) -> Path:
        return self._output_data_dir / filename