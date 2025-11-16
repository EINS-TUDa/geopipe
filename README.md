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
8. (temperory solution) Replace files in /CESM/core with files from /patch_CESM_core (drag and drop).

