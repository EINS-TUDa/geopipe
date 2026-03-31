"""YAML-based run configuration loader."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union
import yaml


def _deep_merge(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge two dictionaries without mutating inputs."""
    result = dict(base)
    for key, value in overrides.items():
        if (
            isinstance(value, dict)
            and key in result
            and isinstance(result[key], dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml_mapping(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at root of {path}, got {type(data).__name__}")
    return data


def load_run_config(
    config_path: Optional[Union[str, Path]] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """Load run configuration from YAML, allowing ad-hoc overrides."""
    base: Dict[str, Any] = {}
    if config_path is not None:
        base = _load_yaml_mapping(Path(config_path))
    return _deep_merge(base, overrides)