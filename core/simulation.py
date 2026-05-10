# =============================================================================
# core/simulation.py — Simulation state manager 
# =============================================================================

import numpy as np
import time

import config
from core.buoy_dt.buoy_controller import BuoyController, MissionCommand
from core.georef import GeoReference
from core.river_model import River, DVsolver
from core.global_buoy_dt import BuoyDigitalTwin, BuoyMode

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class SimulationState:
    """
    Central state object for the digital twin.

    Setup workflow (enforced by setup_step):
        0 — waiting for width line (2-point polyline on map)
        1 — waiting for river path (multi-point polyline on map)
        2 — waiting for buoy start (marker click on map)
        3 — ready to run
    """

    # Human-readable instructions per step (shown on the map)
    STEP_HINTS = {
        0: "① Draw a line ACROSS the river to measure its width",
        1: "② Draw the river CENTERLINE upstream → downstream",
        2: "③ Place a MARKER for the buoy starting position",
        3: "✓ Ready — press START in the sidebar",
    }

    def __init__(self, buoy_dt: BuoyDigitalTwin):
        # --- Setup workflow ---
        self.buoy_dt: BuoyDigitalTwin = buoy_dt
        self.controller = None
        self._last_cmd = None

        self.setup_step = 0  # 0=width, 1=path, 2=start, 3=ready
        self.river_width = None  # metres, set in step 0

        # --- Geo referencing ---
        self.georef = GeoReference()

        # --- River Model ---
        self.river = None
        self.plume = None
        self.dv = None

        # --- Buoy state ---
        self.mode = BuoyMode.SIM

        # --- Contamination ---
        self.contamination_detected = False
        self.contamination_ts = 0.0
        self.contamination_local = (0.0, 0.0)
        self.contamination_gps = (config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON)
        self.backtrack_map = None
        self.adjoint = None  # AdjointDVsolver, live while backtracking
        self.backtracking = False  # True while adjoint is stepping

        # --- Simulation clock ---
        self.sim_time = 0
        self.sim_dt = 5  # seconds per step
        self.running = False

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
        lat1, lon1 = two_points[-1]  # use first and last in case more were drawn

        from core.georef import METRES_PER_DEG_LAT, metres_per_deg_lon
        dlat = (lat1 - lat0) * METRES_PER_DEG_LAT
        dlon = (lon1 - lon0) * metres_per_deg_lon((lat0 + lat1) / 2)
        self.river_width = float(np.sqrt(dlat ** 2 + dlon ** 2))
        self.setup_step = 1
        print(f"[Simulation] River width set to {self.river_width:.1f} m")

    def set_gps_polyline(self, points: list[tuple]):
        """
        Step 1: register the river centreline GPS polyline and build the model.
        """
        self.georef.set_gps_polyline(points)
        print(f"[Simulation] Georef set — {len(points)} GPS points")
        self.build_river()
        self.setup_step = 2

        # Here we can prerun the contamination already for simulation
        for i in range(100):
            self.dv.update()

    def set_buoy_start_gps(self, lat: float, lon: float):
        """
        Step 2: place the buoy at a GPS position clicked on the map.
        Converts to local coords and stores as the start position.
        """
        if not self.georef._is_set:
            print("[Simulation] Cannot set buoy start — georef not set")
            return

        self.buoy_dt.set_start_from_gps(lat=lat, lon=lon)

        self.setup_step = 3
        print(f"[Simulation] Buoy start set to local ({self.buoy_dt.local_x:.1f}, {self.buoy_dt.local_y:.1f})")

    def build_river(self, topology=None, bend_radius: float = 60.0, **kwargs):
        """Build the River model from the stored GPS polyline."""

        # Drawn width or default
        width = self.river_width or config.DEFAULT_WIDTH

        # Get topology from drawing 
        if topology is not None:
            topo = topology
        elif self.georef.is_set:
            topo = self.georef.to_river_topology(
                bend_radius=bend_radius,
                merge_window_m=kwargs.get("merge_window_m", config.DEFAULT_MERGE_WINDOW_M),
            )
            print(f"[Simulation] Derived topology: {len(topo)} segments")
        else:
            topo = config.DEFAULT_TOPOLOGY

        # Feed topology to georef for discritization and KDtree
        ds_length = kwargs.get("ds_length",
                               config.DEFAULT_DS_L)  # needs to be fixed so dynamic based on river lenght!!!!
        self.georef.build_discretized_tree(topo, ds_length)

        # Build the river model
        self.river = River(
            topology=topo,
            width=width,
            ds_length=ds_length,
            n_width=kwargs.get("n_width", config.DEFAULT_N_WIDTH),
            u_avg=kwargs.get("u_avg", config.DEFAULT_U_AVG),
            alpha_secondary=kwargs.get("alpha_secondary", config.DEFAULT_ALPHA_SEC),
        )

        # Create the forward DV solver for the contamination sim 
        self.dv = DVsolver(
            river=self.river,
            source_coords=[float(self.river.xc[0]), -50],
            source_intensity=config.DEFAULT_SOURCE_INTENSITY,
            diffusion=config.DEFAULT_DIFFUSIVITY,
            step=25,
            is_adjoint=False
        )

        # Give Dt the built river 
        self.buoy_dt.set_river(self.river)
        self.buoy_dt.set_georef(self.georef)

        # Default buoy start: beginning of river centreline
        begin_x = float(self.river.xc[0])
        begin_y = 50
        self.buoy_dt.update_coords_from_local(begin_x, begin_y)
        self.buoy_dt.start_local = (begin_x, begin_y)

        # Set the mode of the dt
        self.buoy_dt.set_mode(self.mode)
        # Create controller for the dt
        self.controller = BuoyController(
            river=self.river,
            river_heading=self.georef.heading,
        )
        self._last_cmd: MissionCommand | None = None

        self.buoy_dt.set_local_2d_coords(self.buoy_dt.local_x, self.buoy_dt.local_y )

        print(f"[Simulation] River built — {len(self.river.xc)} centreline points, width={width:.1f}m")

    # ------------------------------------------------------------------
    # Simulation step
    # ------------------------------------------------------------------

    def step(self):
        """Advance the simulation by one time step."""
        if self.river is None: 
            return
        if self.buoy_dt.model_used is None:
            print("buoy model is none, step not possible")
            return

        self.sim_time += 1

        # advance the forward DV event tho already prerun
        self.dv.update()


        # Advance buoy DT
        self.advance_buoy()

        # Recheck Contamination values [0-100]
        self._check_contamination()

        # Step adjoint if running
        if self.backtracking and self.adjoint is not None:
            for i in range(20):
                self.backtrack_map = self.adjoint.update()


        #Step the controller that will send commands to the buoy
        #self.controller_step()

    

    def advance_buoy(self):
        """
        Only call the one global buoy dt model

        If SIM is set, the buoy will just use particle to flow trough river 
        If REAL is set, the buoy will check sensor data and update EKF if needed 

        """
        print("update buoy")
        self.buoy_dt.step()

    
    def controller_step(self):

        # Get current position from the buoy dt
        x, y = self.buoy_dt.local_x, self.buoy_dt.local_y

        # Compute controller command using DE gradient
        def de_field(px, py):
            # Get the latest 2D concentration map from the solver
            cmap = self.dv.get_concentration_map()
            
            # Find the nearest grid cell to the boat's (px, py) position
            pts = np.array([[px, py]])
            _, idx = self.river.physics_tree.query(pts, k=1)
            idx = int(idx[0])
            
            # Convert the 1D tree index to 2D grid indices
            # i = streamwise index, j = cross-stream index
            n_width = self.river.n_width
            i = idx // n_width
            j = idx % n_width
            
            # Get the raw concentration value at this cell
            c = float(cmap[i, j])
            
            # Calculate the gradient (dC/ds and dC/dn) using numpy
            # np.gradient returns a list of arrays: [gradient_stream, gradient_cross]
            dn = self.river.width / self.river.n_widt
            grad_s_map, grad_n_map = np.gradient(cmap, self.river.ds_length, dn)
            
            g_s = grad_s_map[i, j]  # Gradient in stream direction
            g_n = grad_n_map[i, j]  # Gradient in cross-stream direction
            
            # Rotate the (s, n) gradient into the global (x, y) sim frame
            # We fetch the local river flow angle for this specific cell
            _, angle = self.river.grid_data[idx]
            
            gx = g_s * np.cos(angle) - g_n * np.sin(angle)
            gy = g_s * np.sin(angle) + g_n * np.cos(angle)
            
            return float(gx), float(gy), c

        cmd = self.controller.compute_command(x, y, de_field, backtrack=self.backtracking)
        self._last_cmd = cmd

        # 4. In SIM mode: push to particle
        if self.mode == BuoyMode.SIM:
            self.buoy_dt.model_used.set_mission(cmd)
        # 5. In REAL mode: fire RPC
        elif self.mode == BuoyMode.REAL:
            self.buoy_dt.comm_dt.send_rpc_async(
                "setMotor",
                {"heading": cmd.heading_world, "thrust": cmd.thrust}
            )



    # ------------------------------------------------------------------
    # Contamination
    # ------------------------------------------------------------------

    def _check_contamination(self, activate=False):

        sensor_data = self.buoy_dt.sensor.data

        ph = sensor_data.ph or 0.0
        ec = sensor_data.ec or 0.0
        do = sensor_data.do or 0.0

        if (not self.contamination_detected and ph > config.CONTAMINATION_THRESHOLD) or activate:
            self.contamination_detected = True
            self.contamination_ts = time.time()
            self.contamination_local = (self.buoy_dt.local_x, self.buoy_dt.local_y)
            self.contamination_gps = (self.buoy_dt.lat, self.buoy_dt.lon)
            print(f"[Simulation] Contamination detected at ({self.buoy_dt.local_x:.1f}, "
                  f"{self.buoy_dt.local_y:.1f}), step {self.sim_time}")
            self.start_backtrack()

    def start_backtrack(self):
        """
        Initialise the adjoint solver at the current detection point and
        start stepping it on every simulation tick.
        """
        if self.river is None:
            print("[Simulation] Cannot backtrack — river not built")
            return
        self.contamination_local = [self.buoy_dt.local_x, self.buoy_dt.local_y]

        self.adjoint = DVsolver(
            river=self.river,
            source_coords=self.contamination_local,
            source_intensity=config.DEFAULT_SOURCE_INTENSITY_BACK,
            diffusion=config.DEFAULT_DIFFUSIVITY_BACK,
            step=30,
            is_adjoint=True
        )
        self.backtrack_map = self.adjoint.get_concentration_map()
        self.backtracking = True

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
        self.backtrack_map = None
        self.adjoint = None
        self.backtracking = False

    def reset_buoy(self):
        """Return buoy to its chosen start position and clear its track."""
        self.buoy_dt.reset()
        self.sim_time = 0
        self.running = False



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

    @property
    def step_hint(self) -> str:
        return self.STEP_HINTS.get(self.setup_step, "")
