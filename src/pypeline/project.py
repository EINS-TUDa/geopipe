from pathlib import Path

from pkg_resources import ensure_directory


class PypelineProject:
    """Manages directory and file paths for a Pypeline project."""

    def __init__(self, project_root: Path, project_name: str):
        self._project_root = project_root # should be obsolete in near future
        self._project_dir = project_root / "examples" / project_name
        self._input_data_dir = self._project_dir / "input_data"
        self._output_data_dir = self._project_dir / "output_data"
        self._plots_dir = self._output_data_dir / "plots"
        self._global_data_dir = project_root / "data" # should be obsolete in near future

        self.check_directories()

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