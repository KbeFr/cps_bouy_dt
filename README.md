# Buoy Digital Twin

## Thought behind Digital Twin
 
For a Digital Twin, real time data processing and the possibility for simulation is impoertant.

For the simulation we wanted to make this as realasitic as we could, so we set out to create a river model 
that was to scale with a real world river segment of the Meuse. 

To achieve this, a real world satellite map of the river segment was used to create and overlay the rover model.
This enabled us to do the following 

- Simulate a to scale, almost real world deployment of the bouy
- Easy importation of the bouy into a simulation environment when it is actually deployed

The second point is important if we want to use particle simulation to backtrack the source of the detected contamination. 

My vision for the backtrack method btw: 
When contamination is detected by the bouy, we start a reverse affection diffusion simulation with that point as the source.
Three important qualities of our situation could help with the effectiveness of this method :

- The bouy we deploy will behave as a regular particle/object in the river, this means it will be passively advected by the same flow field as contamination particles. So it could be argued that the moment the bouy detect contamination this will leading edge of the plume, if the plume is not spread over the whole river ofcource. *Not best argument haha

- The contamination source is assumed to be on the river banks (i think) so when we backtrack the model we can monitor the spread and see when it reached the banks. 

- We operate in relativly wide rivers with relativly high velocity, this would result in that the spread of the contamination will not be as major as in the shown dias of the presentation (if source is not massive ig).


In this project i (and ai :>) tried to implment the above Thought.


Ofcourse having a way to physically steer the board against the current and running this simulation multiple times until the contamination is not sensed anmore will pinpoint the location better.

There is the possibility to RPC to the bouy via thingsboard (see `BouyComm.send_rpc() for draft call), so motor controll from this dashboard could be possible.


## Project Structure

```
buoy_dt/
├── app.py                  ← Entry point — run this
├── config.py               ← All credentials & tuning constants
├── requirements.txt
│
├── core/
│   ├── river_model.py      ← All the models from before (River, LagrangianParticles, DVsolver) + Bouy    
│   ├── georef.py           ← GPS ↔ local coordinate transforms
│   ├── buoy_comm.py        ← ThingsBoard HTTP client
│   └── simulation.py       ← Physics state machine
│
└── components/
    ├── control_panel.py    ← Left sidebar
    ├── map_panel.py        ← Satellite map (dash-leaflet)
    └── river_panel.py      ← River model plots (plotly)
```

## Setup


# Venv?!

```bash
pip install -r requirements.txt
```

# Fill in `config.py`:
- `TB_EMAIL` / `TB_PASSWORD` — your ThingsBoard login
- `TB_DEVICE_ID` — the UUID from your device URL
- `MAP_DEFAULT_LAT/LON` — center the map on intresed rover segment, now set to anwtwerpen (schelde used)

# Topics that are expected from thingsboard device : 
- temperature (not really needed, was present on walter by default)
- latitude
- longitude
- ph 
- ec 
- do

*can also be changed in code ofc


## Run

```bash
python app.py
```

Open http://localhost:8050



## How to use

Check `config.py` to see if everything is correctly configured

Topics that are expected from thingsboard device : 
- temperature
- latitude
- longitude
- ph 
- ec 
- do

*can also be changed in code ofc


1. **Draw the width of the river (bank to bank)** on the satellite map using the `polyline tool` (toolbar top-right of map)
2. **Draw the river** on the satellite map using the `polyline tool` (toolbar top-right of map)
    If you want to draw a bend, i suggest drawing in small steps (500m) each with a angle from each other greather than 3deg. 
    If you do this the line seglents will be merged into one large bend, which will look neater.
3. **Annotate the bouy start** on the satellite map using the `waypoint tool` (toolbar top-right of map)
4. Click **▶ START** in the sidebar 

These steps are also annotated on the map.

## Left to do 
- `_check_contamination()` in `simulation.py` still needs a proper contamination trigger
- The backtracking needs to be fixed
- The bouy tracking on the gps coordinated flips on on corners 




