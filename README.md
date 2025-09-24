# README
## PYPELINE
PYthon-based data piPELINe for Energy system modeling

## How to run with CESM
1. Clone the repository
    ```
    > git clone https://git.rwth-aachen.de/carolin.ayasse/data_pipeline_esm/
    ```
2. Initialize and update submodules
    ``` 
    > git submodule update --init --recursive
    ```
3. Create an environment
    ```
    > python -m venv .venv
    ```
4. Activate the environment
    ```
    > .venv/Scripts/activate
    ```
5. Install the package
    ```
    > pip install -e . 
    ```
    For development, use:
    ```
    > pip install -e .[dev]
    ```
6. Install the submodule
    ```
    > pip install -e ./cesm
    ```
