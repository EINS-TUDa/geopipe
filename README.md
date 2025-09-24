# README
## PYPELINE
PYthon-based data piPELINe for Energy system modeling

## How to run with CESM
1. Clone the repository
``` git clone``` 
2. Initialize and update submodules
``` git submodule update --init --recursive```
3. Create an environment
``` pythin -m venv .venv```
4. Activate the environment
``` source .venv/bin/activate```
5. Install the required packages
``` pip install -r requirements.txt```
6. Install the submodule
``` pip install -e ./cesm``` 