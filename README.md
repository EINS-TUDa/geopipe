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
3. Initialize git
    ```
    git init --initial-branch=main
    ```
4. Initialize and update submodules
    ``` 
    git submodule update --init --recursive
    ```
5. Create an environment
    ```
    python -m venv .venv
    ```
6. Activate the environment
    ```
    .venv/Scripts/activate
    ```
7. Install the package
    ```
    pip install -e . 
    ```
    For development, use:
    ```
    pip install -e .[dev]
    ```
8. Install the submodule
    ```
    pip install -e ./cesm
    ```
