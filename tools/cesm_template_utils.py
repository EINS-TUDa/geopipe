"""CESM template utilities."""
from pathlib import Path
import json
from typing import Any, Dict, Optional, Union

def get_cesm_templates_dir() -> Path:
    """Get the CESM templates directory."""
    return Path(__file__).parent / 'cesm_templates'

def load_cesm_template(name: str) -> Optional[Dict[str, Any]]:
    """Load a CESM template by name.
    
    Args:
        name: Template file name (with .json extension)
        
    Returns:
        Template dict if found, None otherwise
    """
    template_path = get_cesm_templates_dir() / name
    if template_path.exists():
        return json.loads(template_path.read_text(encoding='utf-8'))
    return None

def merge_with_template(base: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge a template with override values.
    
    Args:
        base: Base template dictionary
        overrides: Values to override
        
    Returns:
        Merged dictionary
    """
    result = base.copy()
    for k, v in overrides.items():
        if isinstance(v, dict) and k in result and isinstance(result[k], dict):
            result[k] = merge_with_template(result[k], v)
        else:
            result[k] = v    
    return result

def load_cesm_config(config_path: Optional[Union[str, Path]] = None, **overrides) -> Dict[str, Any]:
    """Load CESM run configuration.
    
    Args:
        config_path: Optional custom config path
        **overrides: Config override values
        
    Returns:
        Complete config dictionary
    """
    if config_path:
        base_config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    else:
        base_config = load_cesm_template('cesm_run_config_template.json') or {}
    
    return merge_with_template(base_config, overrides)