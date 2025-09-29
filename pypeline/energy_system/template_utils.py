# -*- coding: utf-8 -*-
"""Template loading utilities for general purpose energy system configuration."""
from pathlib import Path
from typing import Dict, Any, Optional, Union

from tools.cesm_template_utils import merge_with_template, load_cesm_config

def load_run_config(config_path: Optional[Union[str, Path]] = None, **overrides) -> Dict[str, Any]:
    """Load run configuration, optionally from a custom path with overrides. This function delegates to CESM config loading.
    
    Args:
        config_path: Optional path to custom config JSON
        **overrides: Key-value pairs to override in the config
        
    Returns:
        Complete run configuration dictionary
    """
    return load_cesm_config(config_path, **overrides)