# Getting started

## Quickstart

### Prerequisites
- Python 3.11
- (Recommended) uv for environment management, https://docs.astral.sh/uv/getting-started/installation/
- Gurobi

### Clone (with submodules) 
```bash
git clone git@git.rwth-aachen.de:carolin.ayasse/data_pipeline_esm.git
cd data_pipeline_esm
```

### Environment. 
```bash
uv sync
```

### Verify setup
```bash
uv run python -c "import pypeline; print('OK')"
```

### Run first examples
Run examples in `examples/` to verify that everything is working. For example:
```bash
uv run python examples/test_case_bensheim/bensheim_test_main.py
```
      

   
