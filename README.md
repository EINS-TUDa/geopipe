# README

## GeoPipe

**Geo**graphical energy system modelling data **pipe**line

## Quickstart

### Prerequisites
- Python 3.11
- (Recommended) uv for environment management, https://docs.astral.sh/uv/getting-started/installation/
- Gurobi

### Clone 
```bash
git clone git@github.com:EINS-TUDa/geopipe.git
cd geopipe
```
### Environment
```bash
uv sync
```

### Verify setup
```bash
uv run python -c "import geopipe; print('OK')"
```

### Run first examples
Run examples in `examples/` to verify that everything is working. For example:
```bash
uv run python examples/test_case_bensheim/bensheim_test_main.py
```


## Documentation
Documentation can be found [here](https://eins-tuda.github.io/geopipe).

To preview the documentation locally, use:
```
uv sync --group docs      # or: uv sync  (gets dev + docs + runtime)       
uv run mkdocs serve
```