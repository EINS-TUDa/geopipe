# Installation

Requires Python ≥ 3.11.

```bash
pip install geopipe
```

## CESM backend

The [CESM](https://github.com/EINS-TUDa/CESM) optimization backend needs
Gurobi with a valid licence. CESM is not on PyPI, so install it from Git:

```bash
pip install "geopipe[cesm]"
pip install git+https://github.com/EINS-TUDa/CESM.git@578e0bb
```

## Other optimization frameworks
Will follow in near future.



## Development setup

With [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
git clone https://github.com/EINS-TUDa/geopipe.git
cd geopipe
uv sync                  
```
