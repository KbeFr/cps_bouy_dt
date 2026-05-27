# =============================================================================
# core/simulation.py — Simulation state manager 
# =============================================================================

import numpy as np
import time
from enum import Enum
import config
from core.buoy_dt.buoy_controller import BuoyController, MissionCommand
from core.georef import GeoReference
from core.river_model.river_model import River, DVsolver
from core.global_buoy_dt import BuoyDigitalTwin, BuoyMode
import threading
import queue

class ContaminationSeverity(Enum):
    """ 
    The contamintaion severity with the backtrack value
    (Servirity = backtrack value)
    """
    CLEAR = 1
    WARNING = config.SOURCE_INTENSITY_BACK_WARNING
    CRITICAL = config.SOURCE_INTENSITY_BACK_CRITICAL


class SimulationState:
    """
    Central simulation coordinator.
    """

    # Human-readable instructions per step for drawing riverr
    STEP_HINTS = {
        0: "① Draw a line ACROSS the river to measure its width",
        1: "② Draw the river CENTERLINE upstream → downstream",
        2: "③ Place a MARKER for the buoy starting position",
        3: "✓ Ready — press START in the sidebar",
        4:  "Place a MARKER for the contamination source"
    }

    def __init__(self, buoy_dt: BuoyDigitalTwin):
        # --- seperate thread for loop ---
        self._sim_thread: threading.Thread | None = None
        self._lock = threading.Lock()          # protects state reads from Dash
        self._stop_event = threading.Event()     

        # --- seperate thread for the adjeoint dt ----
        self._adjoint_result_queue = queue.Queue(maxsize=1)
        self._adjoint_thread: threading.Thread | None = None

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
        self.contamination_level : ContaminationSeverity = ContaminationSeverity.CLEAR
        self.contamination_ts = 0.0
        self.contamination_local = (0.0, 0.0)
        self.contamination_gps = (config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON)
        self.backtrack_map = None
        self.adjoint = None  # AdjointDVsolver, live while backtracking
        self.backtracking = False  # True while adjoint is stepping

        # --- Simulation clock ---
        self.sim_time = 0
        self.running = False

        self._step_dt = 0.5 #Seconds between steps 

    # ------------------------------------------------------------------
    # Simulation step
    # ------------------------------------------------------------------

    def step(self):
        """Advance the simulation by one time step."""
        if self.river is None or not self.running: 
            return
        if self.buoy_dt.model_used is None:
            print("buoy model is none, step not possible")
            return

        self.sim_time += 1

        # advance the forward DV event tho already prerun
        self.dv.update()


        # Advance buoy DT
        self.advance_buoy()

        # Check contamination levels
        self._check_contamination()

        # Step adjoint if running
        if self.backtracking and self.adjoint is not None:
            try:
                self.backtrack_map = self._adjoint_result_queue.get_nowait()
                self.controller_step()

            except queue.Empty:
                pass                   # use previous map

        #Step the controller that will send commands to the buoy

    def advance_buoy(self):
        """
        Only call the one global buoy dt model

        If SIM is set, the buoy will just use particle to flow trough river 
        If REAL is set, the buoy will check sensor data and update EKF if needed 

        """
        self.buoy_dt.step()
        
    def controller_step(self):
        if self.controller is None:
            return
        x, y = self.buoy_dt.local_x, self.buoy_dt.local_y
        if x is None or y is None:
            return

        cmd = self.controller.compute_command(x, y)
        self._last_cmd = cmd

        if self.mode == BuoyMode.SIM:
            self.buoy_dt.model_used.set_mission(cmd)
        elif self.mode == BuoyMode.REAL:
            self.buoy_dt.comm_dt.send_rpc_async(
                "setMotor",
                {"heading": cmd.heading_world, "thrust": cmd.thrust}
            )


    # ------------------------------------------------------------------
    # Building river model  
    # ------------------------------------------------------------------

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
            print("[Simulation] Topology is None or Georef not set")


        # Calculate total lenght for descritezed lenght calc
        total_length = 0.0
        for seg_type, measures in topo:
            if seg_type == 0:
                total_length += measures
            elif seg_type == 1:
                radius, angle_deg = measures
                total_length += radius * abs(np.radians(angle_deg))

        n_length = config.DEFAULT_N_LENGTH
        ds_length = total_length / n_length

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


        self.buoy_dt.set_georef(self.georef)
        self.buoy_dt.set_mode(self.mode)
        self.buoy_dt.set_river(self.river)

        begin_x = float(self.river.xc[0])
        begin_y = 0.0
        self.buoy_dt.set_start_from_local(begin_x, begin_y)   

        self.controller = BuoyController(
            river=self.river,
            river_heading=self.georef.heading,
        )
        self._last_cmd = None

        print(f"[Simulation] River built — {len(self.river.xc)} centreline points, width={width:.1f}m")


    # ------------------------------------------------------------------
    # Setup for drawing the river on initialization 
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

    def set_contamination_source_gps(self, lat: float, lon: float):
        """
        Step 4: place a new contamination source at the clicked GPS position
        and rebuild the forward DV solver.
        """
        if not self.georef._is_set or self.river is None:
            print("[Simulation] Cannot set source — georef or river not built")
            return

        local_x, local_y = self.georef.gps_to_local(lat, lon)
        print(f"[Simulation] Source placed at local ({local_x:.1f}, {local_y:.1f})")

        # Create a new forward DV solver starting at these coordinates
        self.dv = DVsolver(
            river=self.river,
            source_coords=[local_x, local_y],
            source_intensity=config.DEFAULT_SOURCE_INTENSITY,
            diffusion=config.DEFAULT_DIFFUSIVITY,
            step=25,
            is_adjoint=False
        )
        
        # Prerun it so there's an initial plume ready to be detected
        print("RUN")
        for _ in range(150):
            self.dv.update()
        print("RUN")

        # Revert to Ready state
        self.setup_step = 3
    # ------------------------------------------------------------------
    # Contamination
    # ------------------------------------------------------------------

    def _check_contamination(self, activate=False):

        sensor_data = self.buoy_dt.sensor.data

        ph = sensor_data.ph if sensor_data.ph is not None else 7.0
        ec = sensor_data.ec if sensor_data.ec is not None else 0.0
        do = sensor_data.do if sensor_data.do is not None else 10.0
        
        contamination_flagged = False
        
        #Mirror alarms from thingboard 
        if do <= 5.0 or ec >= 1000 or ph <= 6.0:
            #Critical
            self.contamination_level = ContaminationSeverity.CRITICAL
            contamination_flagged = True
        elif do <= 7.0 or ec >= 700:
            #Warninf
            self.contamination_level = ContaminationSeverity.WARNING
            contamination_flagged = True
        else:
            #Clear
            self.contamination_level = ContaminationSeverity.CLEAR

        #For debugging
        contamination_flagged = False

        if contamination_flagged or activate:
            self.contamination_detected = True
            self.contamination_ts = time.time()
            self.contamination_local = (self.buoy_dt.local_x, self.buoy_dt.local_y)
            self.contamination_gps = (self.buoy_dt.lat, self.buoy_dt.lon)
            print(f"[Simulation] Contamination detected at ({self.buoy_dt.local_x:.1f}, "
                  f"{self.buoy_dt.local_y:.1f}), step {self.sim_time}")
            self.start_backtrack(self.contamination_level.value)

    def start_backtrack(self, source_intensity = config.DEFAULT_SOURCE_INTENSITY_BACK):
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
            source_intensity=source_intensity,
            diffusion=config.DEFAULT_DIFFUSIVITY_BACK,
            step=30,
            is_adjoint=True
        )
        self.backtrack_map = self.adjoint.get_concentration_map()
        self.backtracking = True

        #Start a seperate loop for the backtracking 
        self._adjoint_thread = threading.Thread(
            target=self._adjoint_loop, daemon=True
        )
        self._adjoint_thread.start()

        print(f"[Simulation] Backtrack started from "
              f"({self.contamination_local[0]:.1f}, {self.contamination_local[1]:.1f})")
        
    def _adjoint_loop(self):
        """Runs adjoint solver continuously, pushes latest map to queue."""
        while self.backtracking:
            result = self.adjoint.update()
            # Non-blocking put — drop old result if consumer is slow
            try:
                self._adjoint_result_queue.get_nowait()
            except queue.Empty:
                pass
            self._adjoint_result_queue.put(result)

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

    # ------------------------------------------------------------------
    # Behind the scene
    # ------------------------------------------------------------------

    def _sim_loop(self):
        """Manage the simulation loop (step)"""
        while not self._stop_event.is_set():
            t0 = time.perf_counter()
            if self.running:
                self.step()
            elapsed = time.perf_counter() - t0
            sleep_for = max(0.0, self._step_dt - elapsed)
            time.sleep(sleep_for)

    def start_sim_thread(self):
        """Entrypoint to start seperate thread for simulation loop"""
        if self._sim_thread and self._sim_thread.is_alive():
            return
        self._stop_event.clear()
        self._sim_thread = threading.Thread(target=self._sim_loop, daemon=True)
        self._sim_thread.start()