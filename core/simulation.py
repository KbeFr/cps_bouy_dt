# =============================================================================
# core/simulation.py — Simulation state manager for the Digital Twin
# =============================================================================

import numpy as np
import time

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import config
from core.georef import GeoReference

try:
    from core.river_model import River, LagrangianParticles, DVsolver, BuoyParticle
    _MODEL_AVAILABLE = True
except ImportError:
    _MODEL_AVAILABLE = False
    print("[Simulation] WARNING: river_model not found — physics disabled")


class SimulationState:
    """
    Central state object for the digital twin.

    Setup workflow (enforced by setup_step):
        0 — waiting for width line (2-point polyline on map)
        1 — waiting for river path (multi-point polyline on map)
        2 — waiting for buoy start (marker click on map)
        3 — ready to run
    """

    MODE_REAL      = "real"
    MODE_SIMULATED = "simulated"

    # Human-readable instructions per step (shown on the map)
    STEP_HINTS = {
        0: "① Draw a line ACROSS the river to measure its width",
        1: "② Draw the river CENTERLINE upstream → downstream",
        2: "③ Place a MARKER for the buoy starting position",
        3: "✓ Ready — press START in the sidebar",
    }

    def __init__(self):
        # --- Setup workflow ---
        self.setup_step  = 0          # 0=width, 1=path, 2=start, 3=ready
        self.river_width = None       # metres, set in step 0

        # --- Georeferencing ---
        self.georef = GeoReference()

        # --- River Model ---
        self.river = None
        self.plume = None
        self.dv    = None
        self.buoy  = None   # BuoyParticle

        # --- Buoy state ---
        self.mode              = self.MODE_SIMULATED
        self.buoy_local_x      = 0.0
        self.buoy_local_y      = 0.0
        self.buoy_start_local  = (0.0, 0.0)   # remembered for reset
        self.buoy_gps_lat      = config.MAP_DEFAULT_LAT
        self.buoy_gps_lon      = config.MAP_DEFAULT_LON
        self.buoy_history_gps: list[tuple] = []

        # --- Live sensor data ---
        self.sensor: dict = {
            "temperature": None,
            "latitude":    None,
            "longitude":   None,

            #UPDATE these for sensor reading topics 
            "ph" : None,
            "ec"  : None,
            "do" :None,

        }

        # --- Contamination ---
        self.contamination_detected  = False
        self.contamination_ts        = 0.0
        self.contamination_local     = (0.0, 0.0)
        self.backtrack_map           = None
        self.adjoint                 = None   # AdjointDVsolver, live while backtracking
        self.backtracking            = False  # True while adjoint is stepping

        # --- Simulation clock ---
        self.sim_time = 0
        self.sim_dt   = 5    # seconds per step
        self.running  = False

    # ------------------------------------------------------------------
    # Setup — called by map_panel callbacks in order
    # ------------------------------------------------------------------

    def set_gps_width(self, two_points: list[tuple]):
        """
        Step 0: measure river width from a 2-point line drawn across the river.
        Computes the distance in metres and stores it as self.river_width.
        """
        if len(two_points) < 2:
            return
        lat0, lon0 = two_points[0]
        lat1, lon1 = two_points[-1]   # use first and last in case more were drawn

        from core.georef import METRES_PER_DEG_LAT, metres_per_deg_lon
        dlat = (lat1 - lat0) * METRES_PER_DEG_LAT
        dlon = (lon1 - lon0) * metres_per_deg_lon((lat0 + lat1) / 2)
        self.river_width = float(np.sqrt(dlat**2 + dlon**2))
        self.setup_step  = 1
        print(f"[Simulation] River width set to {self.river_width:.1f} m")

    def set_gps_polyline(self, points: list[tuple]):
        """
        Step 1: register the river centreline GPS polyline and build the model.
        """
        self.georef.set_gps_polyline(points)
        print(f"[Simulation] Georef set — {len(points)} GPS points")
        self.build_river()
        self.setup_step = 2

    def set_buoy_start_gps(self, lat: float, lon: float):
        """
        Step 2: place the buoy at a GPS position clicked on the map.
        Converts to local coords and stores as the start position.
        """
        if not self.georef._is_set:
            print("[Simulation] Cannot set buoy start — georef not set")
            return

        x, y = self.georef.gps_to_local(lat, lon)
        """
        # Clamp to river bounds
        if self.river is not None:
            x = float(np.clip(x, self.river.xc[0], self.river.xc[-1]))
            half_w = self.river.half_width * 0.9
            y = float(np.clip(y, -half_w, half_w))
        """
        self.buoy_local_x     = x
        self.buoy_local_y     = y
        self.buoy_start_local = (x, y)
        self.buoy_gps_lat     = lat
        self.buoy_gps_lon     = lon
        if self.buoy is not None:
            self.buoy.reset(x, y)
        self.setup_step = 3
        print(f"[Simulation] Buoy start set to local ({x:.1f}, {y:.1f})")

    def build_river(self, topology=None, bend_radius: float = 60.0, **kwargs):
        """Build the River model from the stored GPS polyline."""
        if not _MODEL_AVAILABLE:
            print("[Simulation] river_model not available")
            return

        width = self.river_width or config.DEFAULT_WIDTH

        if topology is not None:
            topo = topology
        elif self.georef._is_set:
            topo = self.georef.to_river_topology(
                bend_radius    = bend_radius,
                merge_window_m = kwargs.get("merge_window_m", config.DEFAULT_MERGE_WINDOW_M),
            )
            print(f"[Simulation] Derived topology: {len(topo)} segments")
        else:
            topo = config.DEFAULT_TOPOLOGY


        #feed topology to georef for discritization and KDtree
        ds_length       = kwargs.get("ds_length",       config.DEFAULT_DS_L) #needs to be fixed so dynamic based on river lenght!!!!
        self.georef.build_discretized_tree(topo , ds_length )

        self.river = River(
            topology        = topo,
            width           = width,
            ds_length       = ds_length,
            n_width         = kwargs.get("n_width",         config.DEFAULT_N_WIDTH),
            u_avg           = kwargs.get("u_avg",           config.DEFAULT_U_AVG),
            alpha_secondary = kwargs.get("alpha_secondary", config.DEFAULT_ALPHA_SEC),
        )

        self.plume = LagrangianParticles(
            river         = self.river,
            num_particles = kwargs.get("num_particles", config.DEFAULT_N_PARTICLES),
            x0            = float(self.river.xc[0]),
            y0            = 0.0,
            D_L           = config.DEFAULT_D_L,
            D_T           = config.DEFAULT_D_T,
        )

        self.dv = DVsolver(
            river            = self.river,
            source_coords    = [float(self.river.xc[0]), 0.0],
            source_intensity = config.DEFAULT_SOURCE_INTENSITY,
            diffusion        = config.DEFAULT_DIFFUSIVITY,
            step             = self.sim_dt,
            is_adjoint       = False
        )

        # Default buoy start: beginning of river centreline
        self.buoy_local_x     = float(self.river.xc[0])
        self.buoy_local_y     = 0.0
        self.buoy_start_local = (self.buoy_local_x, self.buoy_local_y)
        self.buoy = BuoyParticle(
            river = self.river,
            x0    = self.buoy_local_x,
            y0    = self.buoy_local_y,
            D_T   = config.DEFAULT_D_T,
        )
        print(f"[Simulation] River built — {len(self.river.xc)} centreline points, width={width:.1f}m")

    # ------------------------------------------------------------------
    # Simulation step
    # ------------------------------------------------------------------

    def step(self):
        """Advance the simulation by one time step."""
        if not self.running or self.river is None:
            return

        self.sim_time += 1

        # Advance physics, not really used

        #self.plume.update(self.sim_dt)
        #self.dv.update()

        # Advance buoy
        if self.mode == self.MODE_SIMULATED:
            self._advance_buoy_simulated()
        else:
            self._advance_buoy_real()

        # Convert local → GPS and record track
        if self.georef._is_set:
            lat, lon = self.georef.sim_cartesian_to_gps(self.buoy_local_x, self.buoy_local_y)
            self.buoy_gps_lat = lat
            self.buoy_gps_lon = lon
            self.buoy_history_gps.append((lat, lon))
            if len(self.buoy_history_gps) > 500:
                self.buoy_history_gps = self.buoy_history_gps[-500:]

        self._check_contamination()

        # Step adjoint if running
        if self.backtracking and self.adjoint is not None:
            self.backtrack_map = self.adjoint.update()

    def _advance_buoy_simulated(self):
        """Delegate entirely to BuoyParticle — all physics and boundary logic lives there."""
        if self.buoy is None:
            return
        self.buoy.update(self.sim_dt)
        self.buoy_local_x, self.buoy_local_y = self.buoy.position

    def _advance_buoy_real(self):
        """Sync buoy position from live ThingsBoard GPS."""
        lat = self.sensor.get("latitude")
        lon = self.sensor.get("longitude")
        if lat and lon and self.georef._is_set:
            self.buoy_gps_lat = lat
            self.buoy_gps_lon = lon
            x, y = self.georef.gps_to_local(lat, lon)
            self.buoy_local_x = x
            self.buoy_local_y = y

    # ------------------------------------------------------------------
    # Contamination
    # ------------------------------------------------------------------

    def _check_contamination(self, activate=False):
        ph = self.sensor.get("ph") or 0.0
        ec = self.sensor.get("ec") or 0.0
        do = self.sensor.get("do") or 0.0

        if (not self.contamination_detected and ph > config.CONTAMINATION_THRESHOLD) or activate:
            self.contamination_detected = True
            self.contamination_ts       = time.time()
            self.contamination_local    = (self.buoy_local_x, self.buoy_local_y)
            print(f"[Simulation] Contamination detected at ({self.buoy_local_x:.1f}, "
                  f"{self.buoy_local_y:.1f}), step {self.sim_time}")
            self.start_backtrack()

    def start_backtrack(self):
        """
        Initialise the adjoint solver at the current detection point and
        start stepping it on every simulation tick.
        """
        if self.river is None:
            print("[Simulation] Cannot backtrack — river not built")
            return
        self.contamination_local = [self.buoy_local_x, self.buoy_local_y]
        self.adjoint = DVsolver(
            river            = self.river,
            source_coords = self.contamination_local,
            source_intensity = config.DEFAULT_SOURCE_INTENSITY_BACK,
            diffusion        = config.DEFAULT_DIFFUSIVITY_BACK,
            step             = 30,
            is_adjoint       = True
        )
        self.backtrack_map = self.adjoint.get_concentration_map()
        self.backtracking  = True
        print(f"[Simulation] Backtrack started from "
              f"({self.contamination_local[0]:.1f}, {self.contamination_local[1]:.1f})")

    def stop_backtrack(self):
        """Freeze the current probability map and stop stepping."""
        self.backtracking = False
        if self.adjoint:
            print(f"[Simulation] Backtrack stopped at τ={self.adjoint.tau:.0f}s")

    def run_backtrack(self):
        """Alias for start_backtrack — kept for compatibility."""
        self.start_backtrack()

    def reset_contamination(self):
        self.contamination_detected = False
        self.backtrack_map          = None
        self.adjoint                = None
        self.backtracking           = False

    def reset_buoy(self):
        """Return buoy to its chosen start position and clear its track."""
        x0, y0 = self.buoy_start_local
        self.buoy_local_x     = x0
        self.buoy_local_y     = y0
        if self.buoy is not None:
            self.buoy.reset(x0, y0)
        self.buoy_history_gps = []
        self.sim_time         = 0
        self.running          = False

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_river_overlay_gps(self) -> list[tuple]:
        if self.river is None or not self.georef._is_set:
            return []
        return self.georef.river_centerline_as_gps()

    def get_concentration_map(self):
        if self.dv is None:
            return None
        return self.dv.get_concentration_map()

    def update_sensor(self, data: dict):
        self.sensor.update(data)

    @property
    def step_hint(self) -> str:
        return self.STEP_HINTS.get(self.setup_step, "")