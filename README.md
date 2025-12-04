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
3. Initialize and update submodules (make sure to be on correct branch which contains the submodule)
   ```
   git submodule update --init --recursive
   ```
4. Setup environment using uv (recommended). Make sure uv is installed (https://docs.astral.sh/uv/getting-started/installation/).
   ```
   uv sync
   ```
