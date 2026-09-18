## Instructions to run the example
> [!Important]  
> In order to run the example you need to download the heat demand data from the [Hesse heat atlas](https://www.waermeatlas-hessen.de). 

This example demonstrates the application of `geopipe` to prepare the input data for the energy system optimization framework  [CESM](https://github.com/EINS-TUDa/CESM). Necessary input data is provided in the [input_data](input_data) folder with further documentation in [DATA_SOURCES.md](DATA_SOURCES.md).


#### 1. Setup
Ensure you have a valid Gurobi license and that you installed `geopipe` and [CESM]([CESM](https://github.com/EINS-TUDa/CESM)) following the installation instructions.
#### 2. Download the heat demand data from the [Hesse heat atlas](https://www.waermeatlas-hessen.de)
1. Go to [https://www.waermeatlas-hessen.de](https://www.waermeatlas-hessen.de).
2. Enter "Bensheim" in the search bar and download the data.
3. Unzip the folder. 
4. Place the file `WaermeatlasHessen.gpkg` in the [`input_data`](input_data) folder of this directory.

#### 3. Run the example via [`main.py`](main.py).
```bash
uv run examples/urban_heat_planning/main.py
```
