# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Buoy Digital Twin** — a real-time geospatial simulation and monitoring dashboard for buoys deployed in rivers to detect and backtrack contamination sources. Combines ESRI satellite imagery, river hydrodynamics, Lagrangian particle tracking, adjoint-based source backtracking, and live ThingsBoard IoT sensor data.

## Running the App

```bash
pip install -r requirements.txt
python app.py
# Open http://localhost:8050
```

The app runs in debug mode by default (auto-reload on file changes). No build step required.

## Configuration

All credentials and physics tuning constants live in `config.py`:
- `TB_HOST`, `TB_EMAIL`, `TB_PASSWORD`, `TB_DEVICE_ID`, `TB_TOKEN` — ThingsBoard IoT cloud credentials
- `MAP_DEFAULT_LAT/LON` — map center (currently Antwerp/Schelde, should be set to target river segment)
- Physics defaults: `DEFAULT_WIDTH`, `DEFAULT_U_AVG`, `DEFAULT_D_L`, `DEFAULT_D_T`, `DEFAULT_N_PARTICLES`, `CONTAMINATION_THRESHOLD`, etc.

ThingsBoard device is expected to publish these telemetry keys: `latitude`, `longitude`, `ph`, `ec`, `do`.

## Architecture

### Layer Separation

| Layer | Files | Role |
|-------|-------|------|
| Entry point | `app.py` | Creates Dash app, global singletons, registers callbacks, starts ThingsBoard polling |
| State machine | `core/simulation.py` | All mutable simulation state; enforces 5-step setup ordering |
| Physics | `core/river_model.py` | River grid, LagrangianParticles, DVsolver, BuoyParticle, AdjointDVsolver |
| IoT client | `core/buoy_comm.py` | ThingsBoard HTTP + JWT auth, background polling thread |
| Coordinates | `core/georef.py` | GPS ↔ arc-length/cross-stream ↔ Cartesian transforms |
| UI components | `components/*.py` | Dash callback registration + layout for each panel |

### Setup State Machine (enforced order)

The app requires these steps before simulation can start:

```
Step 0: Draw river WIDTH line (polyline across river → metres)
Step 1: Draw river CENTERLINE (polyline upstream→downstream) → triggers build_river()
Step 2: Place CONTAMINATION SOURCE marker
Step 3: Place BUOY START marker
Step 4: Press ▶ START
```

`SimulationState` in `simulation.py` tracks which step is complete and blocks invalid transitions.

### Three Coordinate Systems

This is critical to avoid bugs. Every coordinate exists in one of:

1. **GPS (lat, lon)** — user-facing, drawn on map
2. **Arc-length / cross-stream (s, n)** — DVsolver PDE mesh space (`s` = distance along centerline, `n` = perpendicular offset)
3. **Simulation Cartesian (x, y)** — river geometry and particle positions

Conversion path: `GPS → (s,n) via georef.gps_to_local() → Cartesian via _gps_to_sim_cartesian()`

### Physics Step (every 500 ms, advances 5 simulation seconds)

Each `SimulationState.step()` call:
1. Lagrangian particle advection-diffusion (plume)
2. Respawn 20% of particles at contamination source
3. DVsolver implicit concentration field update
4. Buoy advection through flow field
5. Contamination threshold check → triggers `AdjointDVsolver` backtracking if exceeded

### Dual Operation Modes

- **Simulated:** `BuoyParticle` moves through synthetic flow, synthetic pH/EC generated from concentration
- **Real:** Buoy GPS and sensors read from ThingsBoard live; particles/visualization still run

### Singleton Pattern

`app.py` creates two global singletons shared across all Dash callbacks:
- `sim_state` — `SimulationState` instance
- `buoy_dt` — `BuoyComm` instance

Callbacks in component modules receive these via closure (registered from `app.py`).

## Known Incomplete Areas

- `_check_contamination()` in `simulation.py` — needs proper trigger logic
- `AdjointDVsolver` backtracking — broken; location not respected, parameters not optimized
- `core/river_model.py` — classes are referenced but implementations may be incomplete (check import guard at `simulation.py:15-21`; prior git commit has fuller versions)
- GPS→local coordinate transform not precomputed (optimization opportunity)
- River grid spacing hardcoded (may be inadequate for large segments)

## ThingsBoard Integration

`BuoyComm` handles JWT auth with auto-refresh every hour. Key methods:
- `get_latest()` → current telemetry
- `get_history(start, end)` → historical range
- `send_rpc(method, params)` → motor control commands to device
- `start_polling(callback)` → background thread, polls every `POLL_INTERVAL_S` seconds
