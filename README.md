# README
## PYPELINE
PYthon-based data piPELINe for Energy system modeling

## How to run with CESM
1. Clone the repository
    ```
   git clone https://git.rwth-aachen.de/carolin.ayasse/data_pipeline_esm/
    ```
2. Change into the directory
    ```
    cd data_pipeline_esm
    ```
3. Initialize and update submodules
    ``` 
    git submodule update --init --recursive
    ```
4. Create an environment
    ```
    python -m venv .venv
    ```
5. Activate the environment
    ```
    .venv/Scripts/activate
    ```
6. Install the package
    ```
    pip install -e . 
    ```
    For development, use:
    ```
    pip install -e .[dev]
7. Install the submodule
    ```
    pip install -e ./cesm
    ```

### Unified CESM Plugin (New)

The CESM integration has been consolidated into a single module: `tools.cesm_plugin`.

Previously separate modules (`cesm_backend`, `cesm_writer`, `cesm_runner`) have been removed after consolidation. Update any legacy imports to the unified form:

```python
from tools.cesm_plugin import CESMBackend, write_cesm_inputs_from_data
```

Minimal usage inside a pipeline context:

```python
from pypeline.energy_system.catalog import register_default_technologies
from pypeline import TechnologyRegistry

tech_reg = TechnologyRegistry()
register_default_technologies(tech_reg)  # JSON-only technology sources

backend = CESMBackend(
    workdir="CESM",
    model_name="Bensheim",
    scenario_name="Base4twk",
    tss_name="Bensheim_TSS",
    input_writer=write_cesm_inputs_from_data,
)

# Given an OMContext `ctx` produced by your model building logic:
solution = backend.optimize(ctx)
print(solution.results.keys())  # KPI tables parsed from CESM SQLite output
```

CLI runner (equivalent to old `cesm_runner.py`):

```bash
python tools/cesm_plugin.py --help
```

Or, if you package the project:

```bash
python -m tools.cesm_plugin --help
```

See `CHANGELOG.md` for migration details.

