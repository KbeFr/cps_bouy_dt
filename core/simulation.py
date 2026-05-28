# =============================================================================
# core/simulation.py — Simulation state manager
# =============================================================================

import os
import numpy as np
import time
import config
from core.buoy_dt.buoy_controller import BuoyController
from core.georef import GeoReference
from core.river_model import River, DVsolver
from core.global_buoy_dt import BuoyDigitalTwin, BuoyMode
from core.estimator import Estimator
from core.river_config import load_autosave, save_autosave, PRESETS_DIR

# Persisted user-drawn centerline lives here so it survives restarts.
CENTERLINE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "centerline.json"
)

class SimulationState:
    """
    Central state object for the digital twin.

    The river loads from the latest user-drawn centerline in
    data/centerline.json. If none exists, it falls back to the abstract
    Meuse topology from config. The user can redraw the river, set width,
    and choose source / buoy start positions from the map.
    """

    PLACE_NONE   = None
    PLACE_SOURCE = "source"
    PLACE_BUOY   = "buoy"
    PLACE_RIVER  = "river"
    PLACE_WIDTH  = "width"

    PLACEMENT_HINTS = {
        PLACE_NONE:   "✓ Ready — adjust source/buoy or press START",
        PLACE_SOURCE: "Click on the map to place the CONTAMINATION SOURCE",
        PLACE_BUOY:   "Click on the map to place the BUOY START",
        PLACE_RIVER:  "Draw a POLYLINE along the river centerline (upstream → downstream)",
        PLACE_WIDTH:  "Draw a 2-point line ACROSS the river to set its width",
    }

    def __init__(self, buoy_dt: BuoyDigitalTwin):
        self.buoy_dt: BuoyDigitalTwin = buoy_dt
        self.buoy_dt.set_sim_sensor(self.get_concentration_data)

        self.controller = None
        self._last_cmd = None

        self.river_width = config.MEUSE_WIDTH_M

        # --- Geo referencing ---
        self.georef = GeoReference()

        # --- River Model ---
        self.river = None
        self.plume = None
        self.dv = None

        # --- Buoy state ---
        self.mode = BuoyMode.SIM

        # --- Source placement state ---
        self.placement_mode = self.PLACE_NONE
        # Stored as local sim coords (x, y)
        self.source_local = None
        self.source_gps = (config.MEUSE_CENTER_LAT, config.MEUSE_CENTER_LON)

        # --- Contamination ---
        self.contamination_detected = False
        self.contamination_severity = None    # "warning" | "critical" | None
        self.contamination_rules_hit: list[str] = []
        self.contamination_ts = 0.0
        self.contamination_local = (0.0, 0.0)
        self.contamination_gps = (config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON)
        self.probability_map = None             # 2-D heatmap (N_stream, N_width)
        self.estimating = False             # True while estimator is running
        self.estimator = None
        # Detection history — every DETECTION_SAMPLE_S seconds while detected,
        # we snapshot (t, x_local, y_local, lat, lon, intensity) so the river
        # plot can show the full track of contamination encounters.
        self.detection_history: list[dict] = []
        self._last_detection_sample_t: float = -1e9
        self.DETECTION_SAMPLE_S = 15.0   # seconds between detection-history samples

        # --- Measurement log (for batch estimating) ---
        # Each entry: dict(t, lat, lon, x_local, y_local, ph, ec, do, severity)
        self.measurement_log: list[dict] = []

        # --- Simulation clock (real-time 1:1 by default) ---
        self.sim_time = 0.0           # accumulated SIM seconds since start
        self.running = False
        self._last_tick_t: float = 0.0
        # Speed multiplier — only applied in SIM mode (REAL is always 1x to
        # stay aligned with the wall-clock arrival rate of live telemetry).
        self.speed_multiplier: float = 1.0

        # --- Transient toast / hint shown to the user for a few seconds ---
        self._toast_text: str | None = None
        self._toast_color: str = "#69f0ae"
        self._toast_until_ts: float = 0.0

        self.build_river()
 
    # ------------------------------------------------------------------
    # River setup
    # ------------------------------------------------------------------
    def build_river(self):
        """
        Build the river. If a user-drawn centerline has been persisted, use
        that, otherwise fall back to the abstract Meuse topology in config.
        """
        gps_polyline, saved_width = load_autosave()
        if saved_width is not None and saved_width > 0:
            self.river_width = saved_width

        if gps_polyline is not None and len(gps_polyline) >= 2:
            # Use the drawn polyline → derive topology via georef
            self.georef.set_gps_polyline(gps_polyline)
            topo = self.georef.to_river_topology(
                bend_radius=config.DEFAULT_BEND_RADIUS if hasattr(config, "DEFAULT_BEND_RADIUS") else 60.0,
                merge_window_m=config.DEFAULT_MERGE_WINDOW_M if hasattr(config, "DEFAULT_MERGE_WINDOW_M") else 100.0,
            )

            ds_length = self.calculate_ds(topo)            

            self.georef.build_discretized_tree(topo, ds_length)
            print(f"[Simulation] Loaded drawn centerline ({len(gps_polyline)} GPS pts -> {len(topo)} segments)")
        else:
            # Abstract fallback
            topo = config.MEUSE_TOPOLOGY

            ds_length = self.calculate_ds(topo)

            heading_rad = np.deg2rad(30.0)
            self.georef.preload_from_origin(
                origin_lat=config.MEUSE_CENTER_LAT,
                origin_lon=config.MEUSE_CENTER_LON,
                heading_rad=heading_rad,
                topology=topo,
                ds_length=ds_length,
            )
            print("[Simulation] Using abstract Meuse topology (no drawn centerline yet)")

        self.river = River(
            topology=topo,
            width=self.river_width,
            ds_length=ds_length,
            n_width=config.DEFAULT_N_WIDTH,
            u_avg=config.DEFAULT_U_AVG,
            alpha_secondary=config.DEFAULT_ALPHA_SEC,
        )

        # Default source: ~upstream third of the river, mid-channel offset.
        src_idx = max(1, len(self.river.xc) // 4)
        sx = float(self.river.xc[src_idx])
        sy = float(self.river.yc[src_idx]) + min(10.0, self.river.half_width * 0.3)
        self.source_local = (sx, sy)
        self.source_gps = self.georef.sim_cartesian_to_gps(sx, sy)

        self._rebuild_dv()

        # Wire river/georef into the buoy DT
        self.buoy_dt.set_river(self.river)
        self.buoy_dt.set_georef(self.georef)

        # Default buoy start: upstream end, centerline, so SIM mode naturally
        # drifts through the plume and creates evidence for source estimation.
        start_x = float(self.river.xc[0])
        start_y = float(self.river.yc[0])
        self.buoy_dt.update_coords_from_local(start_x, start_y)
        self.buoy_dt.start_local = (start_x, start_y)

        self.buoy_dt.set_mode(self.mode)
        self.controller = BuoyController(
        river=self.river,
        river_heading=self.georef.heading,
        avoid_threshold=0.72,   # steer back when within 28 % of bank
        sweep_angle=np.deg2rad(25),
        sweep_period=55.0,      # seconds per sweep leg (tune to your river length)
        enable_sweep=False,
        )    
        self.estimator = Estimator(river=self.river, georef=self.georef)

        print(f"[Simulation] River preloaded — {len(self.river.xc)} centreline points, "
              f"width={self.river_width:.1f}m")



    def _rebuild_dv(self):
        """
        Rebuild forward DVsolver for current source location, using the
        physically-motivated anisotropic Fischer dispersion coefficients
        (D_L >> D_T for natural rivers).
        """
        sx, sy = self._clip_to_channel(*self.source_local)
        self.source_local = (sx, sy)
        self.dv = DVsolver(
            river=self.river,
            source_coords=[sx, sy],
            source_intensity=config.DEFAULT_SOURCE_INTENSITY,
            D_L=config.DEFAULT_D_L,
            D_T=config.DEFAULT_D_T,
            step=25,
            is_adjoint=False,
        )
        # Prime the field so the plume is visible immediately
        for _ in range(60):
            self.dv.update()

    def _clip_to_channel(self, x: float, y: float) -> tuple[float, float]:
        """Project (x, y) inside the channel half-width."""
        if self.river is None:
            return x, y
        pts = np.array([[x, y]])
        dist, idx = self.river.centerline_tree.query(pts, k=1)
        dist = float(dist[0])
        idx = int(idx[0])
        limit = self.river.half_width * 0.85
        if dist <= limit:
            return x, y
        cx = self.river.xc[idx]
        cy = self.river.yc[idx]
        vx = x - cx
        vy = y - cy
        norm = float(np.sqrt(vx*vx + vy*vy)) or 1.0
        return float(cx + vx / norm * limit), float(cy + vy / norm * limit)

    # ------------------------------------------------------------------
    # Drawing set
    # ------------------------------------------------------------------

    def set_drawn_centerline(self, gps_points: list[tuple]):
        """Persist a user-drawn GPS centerline and rebuild the river from it."""
        if len(gps_points) < 2:
            print("[Simulation] Need at least 2 points to define a centerline")
            return
        save_autosave(points=gps_points)
        # Rebuild river from scratch (loads the just-saved file)
        self.running = False
        self.reset_contamination()
        self.measurement_log.clear()
        self.buoy_dt.buoy_history_gps.clear()
        self.build_river()

    def set_drawn_width(self, two_gps_points: list[tuple]):
        """Two map points across the river -> width in metres."""
        if len(two_gps_points) < 2:
            print("[Simulation] Need 2 points to measure width")
            return
        from core.georef import METRES_PER_DEG_LAT, metres_per_deg_lon
        lat0, lon0 = two_gps_points[0]
        lat1, lon1 = two_gps_points[-1]
        dlat = (lat1 - lat0) * METRES_PER_DEG_LAT
        dlon = (lon1 - lon0) * metres_per_deg_lon((lat0 + lat1) / 2.0)
        width = float(np.sqrt(dlat * dlat + dlon * dlon))
        if width < 1.0:
            print(f"[Simulation] Drawn width {width:.2f} m too small, ignoring")
            self.set_toast("Drawn width is too small, try a longer line.", "#ff9800")
            return
        self.river_width = width
        save_autosave(width_m=width)
        self.running = False
        self.reset_contamination()
        self.measurement_log.clear()
        self.buoy_dt.buoy_history_gps.clear()
        self.build_river()
        self.set_toast(f"River width set to {width:.1f} m", "#69f0ae", duration_s=4.0)
        print(f"[Simulation] River width set to {width:.1f} m")

    def clear_drawn_centerline(self):
        """Revert to the abstract Meuse fallback by deleting the saved file."""
        autosave_path = os.path.join(PRESETS_DIR, "autosave.json")
        try:
            os.remove(autosave_path)
            print(f"[Simulation] Centerline cleared")
        except FileNotFoundError:
            pass
            
        self.running = False
        self.river_width = config.MEUSE_WIDTH_M
        self.reset_contamination()
        self.measurement_log.clear()
        self.buoy_dt.buoy_history_gps.clear()
        self.build_river()


    # ------------------------------------------------------------------
    # User placement (called by map_panel callbacks)
    # ------------------------------------------------------------------
    def set_placement_mode(self, mode):
        if mode not in (self.PLACE_NONE, self.PLACE_SOURCE,
                        self.PLACE_BUOY, self.PLACE_RIVER, self.PLACE_WIDTH):
            return
        self.placement_mode = mode

    def handle_map_click(self, lat: float, lon: float):
        """
        Handle a generic map click. Only disarms placement_mode when we
        actually consumed the click. Draw modes are handled by leaflet-draw,
        so ordinary map clicks must not cancel them.
        """
        if self.placement_mode == self.PLACE_SOURCE:
            self.set_source_gps(lat, lon)
            self.placement_mode = self.PLACE_NONE
        elif self.placement_mode == self.PLACE_BUOY:
            self.set_buoy_start_gps(lat, lon)
            self.placement_mode = self.PLACE_NONE
        # PLACE_RIVER, PLACE_WIDTH and PLACE_NONE: do nothing, leave mode unchanged.

    def set_source_gps(self, lat: float, lon: float):
        if not self.georef.is_set or self.river is None:
            return
        x, y = self.georef.gps_to_local(lat, lon)
        x, y = self._clip_to_channel(x, y)
        self.source_local = (x, y)
        self.source_gps = self.georef.sim_cartesian_to_gps(x, y)
        self._rebuild_dv()
        # Source change invalidates accumulated detections
        self.reset_contamination()
        self.measurement_log.clear()
        self.set_toast("✓ Source moved", "#ff1744")
        print(f"[Simulation] Source moved to local ({x:.1f}, {y:.1f})")

    def set_buoy_start_gps(self, lat: float, lon: float):
        if not self.georef.is_set or self.river is None:
            return
        x, y = self.georef.gps_to_local(lat, lon)
        x, y = self._clip_to_channel(x, y)
        self.buoy_dt.update_coords_from_local(x, y)
        self.buoy_dt.start_local = (x, y)
        self.buoy_dt.buoy_history_gps.clear()
        if self.buoy_dt.model_used is not None and hasattr(self.buoy_dt.model_used, "position"):
            self.buoy_dt.model_used.position = (x, y)
        self.set_toast("✓ Buoy start moved", "#69f0ae")
        print(f"[Simulation] Buoy start moved to local ({x:.1f}, {y:.1f})")

    # ------------------------------------------------------------------
    # Simulation step (driven by wall-clock)
    # ------------------------------------------------------------------
    def step(self):
        """Advance the simulation by the elapsed wall-clock dt."""
        if not self.running:
            return
        if self.river is None or self.buoy_dt.model_used is None:
            return

        now = time.time()
        if self._last_tick_t == 0.0:
            self._last_tick_t = now
            return
        wall_dt = max(0.0, min(2.0, now - self._last_tick_t))   # clamp huge jumps
        self._last_tick_t = now

        # Apply the speed multiplier in SIM mode only; REAL mode stays 1:1.
        speed = self.speed_multiplier if self.mode == BuoyMode.SIM else 1.0
        dt = min(30.0, wall_dt * speed)

        self.sim_time += dt

        # Forward plume evolution
        self.dv.update()

        # Advance the buoy
        self.buoy_dt.dt = dt
        self.buoy_dt.step()

        # Check contrller for commands
        if self.controller is not None and self.buoy_dt.model_used is not None:
            x = self.buoy_dt.local_x
            y = self.buoy_dt.local_y
    
            cmd = self.controller.compute_command(x_sim=x, y_sim=y, dt=dt)
            self._last_cmd = cmd
    
            if hasattr(self.buoy_dt.model_used, "set_mission"):
                self.buoy_dt.model_used.set_mission(cmd)
    
        # Log + check alarm rules
        self._log_and_check()


    # ------------------------------------------------------------------
    # Logging + alarm rule evaluation
    # ------------------------------------------------------------------
    def _log_and_check(self):
        """
        Log every tick and flag contamination when the latest sample matches
        any of the configured ThingsBoard alarm rules.

        Severity grading:
          * critical: at least one critical rule hit.
          * warning : at least one warning rule hit and no critical hit.
          * (none)  : no configured rule hit.
        """
        s = self.buoy_dt.sensor_data
        ph = s.ph
        ec = s.ec
        do = s.do

        if ph is None and ec is None and do is None:
            return

        # Evaluate every rule; remember which sensor key fired at which severity.
        hits = []                            # list of (key, severity, "key=v/sev")
        by_key_severity: dict[str, str] = {}  # key -> highest severity observed
        for key, pred, severity in config.ALARM_RULES:
            v = {"ph": ph, "ec": ec, "do": do}.get(key)
            if v is None:
                continue
            try:
                if pred(v):
                    hits.append((key, severity, f"{key}={v:.2f}/{severity}"))
                    cur = by_key_severity.get(key)
                    if cur != "critical":      # promote to critical if any rule says so
                        by_key_severity[key] = severity
            except Exception:
                continue

        crit_keys = sum(1 for sv in by_key_severity.values() if sv == "critical")
        warn_keys = sum(1 for sv in by_key_severity.values() if sv == "warning")

        if crit_keys >= 1:
            sev = "critical"
        elif warn_keys >= 1:
            sev = "warning"
        else:
            sev = None

        entry = {
            "t":        self.sim_time,
            "lat":      self.buoy_dt.lat,
            "lon":      self.buoy_dt.lon,
            "x_local":  self.buoy_dt.local_x,
            "y_local":  self.buoy_dt.local_y,
            "ph":       ph, "ec": ec, "do": do,
            "severity": sev,
            "n_sensors_triggered": len(by_key_severity),
            "intensity":  getattr(self, "_last_intensity", 0.0),
            "conc":       getattr(self, "_last_concentration", 0.0),
            "pollution_score": self.estimator.pollution_score(ph, ec, do),
        }
        self.measurement_log.append(entry)
        if len(self.measurement_log) > 5000:
            self.measurement_log = self.measurement_log[-5000:]

        if sev is not None:
            # Latch detection state; keep most severe observed.
            if not self.contamination_detected:
                self.contamination_ts = time.time()
                self.contamination_local = (self.buoy_dt.local_x, self.buoy_dt.local_y)
                if self.buoy_dt.lat is not None:
                    self.contamination_gps = (self.buoy_dt.lat, self.buoy_dt.lon)
            self.contamination_detected = True
            self.contamination_rules_hit = [h[2] for h in hits]
            if sev == "critical" or self.contamination_severity != "critical":
                self.contamination_severity = sev

            # Sample the detection history every DETECTION_SAMPLE_S seconds.
            if self.sim_time - self._last_detection_sample_t >= self.DETECTION_SAMPLE_S:
                self._last_detection_sample_t = self.sim_time
                self.detection_history.append({
                    "t":         self.sim_time,
                    "x_local":   self.buoy_dt.local_x,
                    "y_local":   self.buoy_dt.local_y,
                    "lat":       self.buoy_dt.lat,
                    "lon":       self.buoy_dt.lon,
                    "intensity": entry["intensity"],
                    "conc":      entry["conc"],
                    "severity":  sev,
                })
                if len(self.detection_history) > 1000:
                    self.detection_history = self.detection_history[-1000:]

    
    def estimate_source(self):
        """Run the Bayesian estimator and update simulation state."""
        
        
        # Run the math (pass it the logs and the true source for debugging)
        result = self.estimator.estimate(
            measurement_log=self.measurement_log, 
            true_source_local=self.source_local
        )
        
        # Update the state based on the result
        if result is None:
            self.probability_map = None
            self.estimating = False
            return
            
        prob_map, peak_coords = result
        
        self.probability_map = prob_map
        self.estimating = False
        

    def reset_contamination(self):
        self.contamination_detected = False
        self.contamination_severity = None
        self.contamination_rules_hit = []
        self.detection_history = []
        self._last_detection_sample_t = -1e9
        self.probability_map = None
        self.estimating = False

    def reset_buoy(self):
        """Return buoy to its chosen start position and clear its track."""
        self.buoy_dt.reset()
        self.sim_time = 0.0
        self._last_tick_t = 0.0
        self.running = False
        # Reset the detection-sample throttle, otherwise its last-fire time
        # is stuck at the previous run's sim_time and no new detection points
        # would be appended until the new run accumulates that much time again.
        self._last_detection_sample_t = -1e9

    def clear_log(self):
        self.measurement_log.clear()

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

    def get_estimated_source_gps(self):
        """Return (lat, lon) of the current peak of the backtrack heatmap, or None."""
        if self.probability_map is None or self.river is None:
            return None
        i, j = np.unravel_index(np.argmax(self.probability_map), self.probability_map.shape)
        return self.georef.sim_cartesian_to_gps(float(self.river.vis_x[i, j]),
                                                 float(self.river.vis_y[i, j]))

    def get_concentration_data(self, x: float, y: float) -> tuple[float, float]:
        if self.dv is None: 
            return 0.0, 1.0
                
        try:
            # Get the global max first
            cmap = self.dv.get_concentration_map()
            cmax = float(cmap.max() or 1.0)
            
            # Look up the local value
            pts = np.array([[x, y]])
            _, idx = self.river.physics_tree.query(pts, k=1)
            idx = int(idx[0])
            i = idx // self.river.n_width
            j = idx %  self.river.n_width
            c = float(cmap[i, j])
            
            return c, cmax
        except Exception:
            return 0.0, 1.0

    def calculate_ds(self, topo): 
            total_length = 0.0
            for seg_type, measures in topo:
                if seg_type == 0:
                    total_length += measures
                elif seg_type == 1:
                    radius, angle_deg = measures
                    total_length += radius * abs(np.radians(angle_deg))
            n_length = config.DEFAULT_N_LENGTH
            ds_length = total_length / n_length
            return ds_length


    @property
    def placement_hint(self) -> str:
        return self.PLACEMENT_HINTS.get(self.placement_mode, "")

    def set_toast(self, text: str, color: str = "#69f0ae", duration_s: float = 4.0):
        self._toast_text = text
        self._toast_color = color
        self._toast_until_ts = time.time() + duration_s

    def get_toast(self) -> tuple[str | None, str]:
        """Return (text, color) of an active toast, or (None, color) if expired."""
        if self._toast_text is None or time.time() >= self._toast_until_ts:
            self._toast_text = None
            return None, self._toast_color
        return self._toast_text, self._toast_color