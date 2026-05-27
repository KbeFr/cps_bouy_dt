# =============================================================================
# core/simulation.py — Simulation state manager
# =============================================================================

import json
import os

import numpy as np
import time

import config

# Persisted user-drawn centerline lives here so it survives restarts.
CENTERLINE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "centerline.json"
)
from core.buoy_dt.buoy_controller import BuoyController
from core.georef import GeoReference
from core.river_model import River, DVsolver
from core.global_buoy_dt import BuoyDigitalTwin, BuoyMode


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
        self.backtrack_map = None             # 2-D heatmap (N_stream, N_width)
        self.backtracking = False             # True while estimator is running

        # Detection history — every DETECTION_SAMPLE_S seconds while detected,
        # we snapshot (t, x_local, y_local, lat, lon, intensity) so the river
        # plot can show the full track of contamination encounters.
        self.detection_history: list[dict] = []
        self._last_detection_sample_t: float = -1e9
        self.DETECTION_SAMPLE_S = 15.0   # seconds between detection-history samples

        # --- Measurement log (for batch backtracking) ---
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

        # --- Preload the abstract Meuse river ---
        self.build_river()

    # ------------------------------------------------------------------
    # River setup
    # ------------------------------------------------------------------
    def build_river(self):
        """
        Build the river. If a user-drawn centerline has been persisted, use
        that; otherwise fall back to the abstract Meuse topology in config.
        """
        ds = config.DEFAULT_DS_L
        gps_polyline, saved_width = self._load_river_state()
        if saved_width is not None and saved_width > 0:
            self.river_width = saved_width

        if gps_polyline is not None and len(gps_polyline) >= 2:
            # Use the drawn polyline → derive topology via georef
            self.georef = GeoReference()
            self.georef.set_gps_polyline(gps_polyline)
            topo = self.georef.to_river_topology(
                bend_radius=60.0,
                merge_window_m=config.DEFAULT_MERGE_WINDOW_M if hasattr(config, "DEFAULT_MERGE_WINDOW_M") else 50.0,
            )
            self.georef.build_discretized_tree(topo, ds)
            print(f"[Simulation] Loaded drawn centerline ({len(gps_polyline)} GPS pts -> {len(topo)} segments)")
        else:
            # Abstract fallback
            topo = config.MEUSE_TOPOLOGY
            heading_rad = np.deg2rad(30.0)
            self.georef.preload_from_origin(
                origin_lat=config.MEUSE_CENTER_LAT,
                origin_lon=config.MEUSE_CENTER_LON,
                heading_rad=heading_rad,
                topology=topo,
                ds_length=ds,
            )
            print("[Simulation] Using abstract Meuse topology (no drawn centerline yet)")

        self.river = River(
            topology=topo,
            width=self.river_width,
            ds_length=ds,
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
        )

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
    # Persisted centerline (load / save)
    # ------------------------------------------------------------------
    def _load_river_state(self) -> tuple[list[tuple] | None, float | None]:
        """Return (centerline_pts, width_m) from the saved file, or (None, None)."""
        try:
            with open(CENTERLINE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            pts = [tuple(p) for p in data.get("points", []) if len(p) == 2]
            pts = pts if len(pts) >= 2 else None
            width = data.get("width_m")
            return pts, (float(width) if width else None)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None, None

    def _save_river_state(self,
                          points: list[tuple] | None = None,
                          width_m: float | None = None):
        """Merge update: keep whichever of points/width isn't being changed."""
        existing_pts, existing_w = self._load_river_state()
        out_pts   = points  if points  is not None else existing_pts
        out_width = width_m if width_m is not None else existing_w
        os.makedirs(os.path.dirname(CENTERLINE_FILE), exist_ok=True)
        payload = {}
        if out_pts is not None:
            payload["points"] = [list(p) for p in out_pts]
        if out_width is not None:
            payload["width_m"] = float(out_width)
        with open(CENTERLINE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        print(f"[Simulation] River state saved -> {CENTERLINE_FILE} "
              f"(pts={len(out_pts) if out_pts else 0}, width="
              f"{out_width if out_width else 'default'})")

    def set_drawn_centerline(self, gps_points: list[tuple]):
        """Persist a user-drawn GPS centerline and rebuild the river from it."""
        if len(gps_points) < 2:
            print("[Simulation] Need at least 2 points to define a centerline")
            return
        self._save_river_state(points=gps_points)
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
        self._save_river_state(width_m=width)
        self.running = False
        self.reset_contamination()
        self.measurement_log.clear()
        self.buoy_dt.buoy_history_gps.clear()
        self.build_river()
        self.set_toast(f"River width set to {width:.1f} m", "#69f0ae", duration_s=4.0)
        print(f"[Simulation] River width set to {width:.1f} m")

    def clear_drawn_centerline(self):
        """Revert to the abstract Meuse fallback by deleting the saved file."""
        try:
            os.remove(CENTERLINE_FILE)
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
        # Cap per-tick sim-time. With 50x speed and a 0.5s wall tick that's
        # 25 s of sim-time per call; we allow up to 30 s before clamping so
        # the slider actually delivers its requested speed.
        dt = min(30.0, wall_dt * speed)

        self.sim_time += dt

        # Forward plume evolution (visualization only)
        self.dv.update()

        # Advance the buoy
        self.buoy_dt.dt = dt
        self.buoy_dt.step()

        # SIM mode: synthesize sensor readings from the local concentration
        if self.mode == BuoyMode.SIM:
            self._synthesize_sim_sensors()

        # Log + check alarm rules
        self._log_and_check()

    # ------------------------------------------------------------------
    # SIM mode synthetic sensors
    # ------------------------------------------------------------------
    def _synthesize_sim_sensors(self):
        """
        SIM mode: synthesize realistic pH/EC/DO sensor readings from the
        local pollution concentration.

        Modeled as an ALKALINE industrial discharge (typical of paper/textile
        effluents):
          * pH rises (alkaline)  — neutral 7.5 -> up to ~9.8 in strong plume
          * EC rises             — clean ~400 µS/cm -> up to ~1300 in plume
          * DO drops             — saturated 9 mg/L -> down to ~3.5 in plume
        Each follows the local concentration intensity (0..1) plus small
        sensor noise. With this profile, the configured ThingsBoard-style
        alarm rules fire clearly inside the plume.
        """
        if self.dv is None:
            return
        cmap = self.dv.get_concentration_map()
        try:
            pts = np.array([[self.buoy_dt.local_x, self.buoy_dt.local_y]])
            _, idx = self.river.physics_tree.query(pts, k=1)
            idx = int(idx[0])
            n_width = self.river.n_width
            i = idx // n_width
            j = idx %  n_width
            c = float(cmap[i, j])
        except Exception:
            c = 0.0

        cmax = float(cmap.max() or 1.0)
        raw_intensity = max(0.0, min(1.0, c / cmax))
        if raw_intensity < getattr(config, "SIM_SENSOR_MIN_INTENSITY", 0.0):
            raw_intensity = 0.0
        # Non-linear sensor dose-response.  Real ion-selective probes and
        # spectrophotometric sensors saturate quickly: even low-concentration
        # contamination produces large reading shifts because the threshold
        # values are well above the natural baseline noise.  Power 0.35 puts
        # 10% local intensity at ~46% of full deflection — matches the
        # ~order-of-magnitude sensitivity of real EC / DO / pH probes near
        # their environmental detection limits.
        eff = float(raw_intensity ** 0.35) if raw_intensity > 0 else 0.0

        rng = np.random.default_rng()
        n_ph   = rng.normal(0.0, 0.05)
        n_ec   = rng.normal(0.0, 12.0)
        n_do   = rng.normal(0.0, 0.10)
        n_temp = rng.normal(0.0, 0.20)

        # All three sensors deviate inside the plume so alarm rules fire.
        self.buoy_dt.sensor.data.ph          = 7.5 + 2.5 * eff + n_ph     # baseline 7.5 -> 10.0 (>9 = crit)
        self.buoy_dt.sensor.data.ec          = 400.0 + 900.0 * eff + n_ec # 400 -> 1300 (>1000 = crit)
        self.buoy_dt.sensor.data.do          = 9.0 - 5.5 * eff + n_do     # 9 -> 3.5 (<5 = crit)
        self.buoy_dt.sensor.data.temperature = 15.0 + 0.4 * eff + n_temp
        # Stash for callers (logger, history)
        self._last_concentration = c
        self._last_intensity = raw_intensity

    # ------------------------------------------------------------------
    # Pollution score (used by estimator as the observed concentration)
    # ------------------------------------------------------------------
    @staticmethod
    def pollution_score(ph: float | None, ec: float | None, do: float | None) -> float:
        """
        Map raw pH/EC/DO readings to a single [0, 1] "pollution-likeness" score.

        Each sensor contributes a per-channel score in [0, 1] based on how far
        it has deviated from its clean-water baseline toward the configured
        critical alarm threshold:

          pH deviation toward 9.0 (alkaline) or 6.0 (acidic):   [0, 1]
          EC rising from 400 toward 1000 µS/cm:                 [0, 1]
          DO falling from 9.0 toward 5.0 mg/L:                  [0, 1]

        The overall score is the MEAN over contributing sensors — this
        averages out single-sensor noise while still rewarding consensus
        between channels. Returns 0.0 when no readings are available.

        This is the value the Bayesian estimator treats as the "observed
        concentration" c_obs in its likelihood — it works equally well in
        SIM mode (where the readings are synthesised) and REAL mode (where
        they come straight from the ThingsBoard live feed).
        """
        scores = []
        if ph is not None:
            if ph >= 7.5:
                scores.append(max(0.0, min(1.0, (ph - 7.5) / (9.0 - 7.5))))
            else:
                scores.append(max(0.0, min(1.0, (7.5 - ph) / (7.5 - 6.0))))
        if ec is not None:
            scores.append(max(0.0, min(1.0, (ec - 400.0) / (1000.0 - 400.0))))
        if do is not None:
            scores.append(max(0.0, min(1.0, (9.0 - do) / (9.0 - 5.0))))
        return float(np.mean(scores)) if scores else 0.0

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
        s = self.buoy_dt.sensor.data
        ph = s.ph; ec = s.ec; do = s.do
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
            "pollution_score": self.pollution_score(ph, ec, do),
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

    # ------------------------------------------------------------------
    # Bayesian source estimator
    # ------------------------------------------------------------------
    def estimate_source(self):
        """
        Bayesian source localization using the analytic 2-D Gaussian plume
        as the forward model.

        For a continuous point source at (s_x, s_y) with downstream
        distance Δs = x_buoy - x_source projected on the centerline, the
        steady-state concentration at the buoy is:

            c(s -> b) = K / sqrt(Δs) * exp( -Δn² * U / (4 D_T Δs) )    if Δs > 0
                      = 0                                              if Δs ≤ 0

        where K is a constant we don't need to know (we normalise).

        For every candidate source cell s and every logged measurement m
        we compute this expected concentration, then form a likelihood:

          * If the measurement triggered an alarm (severity != None)
            with intensity i_obs, the candidate's score gets +w · c(s→b),
            where w = 1 for warning / 3 for critical.
          * If the measurement did NOT trigger the alarm but the candidate
            WOULD have produced a strong plume at the buoy (c(s→b) is high),
            the candidate is inconsistent → subtract a smaller penalty.

        After all measurements are folded in, the score map is clipped to
        non-negative and normalised to a probability distribution over
        candidate source cells. The peak of this map is the most-likely
        source.  Multiple buoy passes naturally accumulate evidence.

        Properties:
          * Candidates outside the river never get counted (the score map
            is indexed by river-grid cells only — no boundary pile-up).
          * The FIRST detection point pins down an upper bound on source
            stream-position (source must be upstream of it).
          * Lateral information from each measurement constrains cross-stream
            position via the Gaussian's narrow lateral profile.
          * NEW measurements multiply into the score → estimate sharpens.
        """
        if self.river is None or not self.measurement_log:
            print("[Estimator] No data to estimate from.")
            self.backtrack_map = None
            self.backtracking = False
            return

        r = self.river
        n_stream, n_width = r.vis_v.shape
        ds = r.ds_length
        dn = r.dn
        U  = max(0.1, float(config.DEFAULT_U_AVG))
        D_T = max(1e-3, float(config.DEFAULT_D_T))
        sigma2 = float(getattr(config, "ESTIMATOR_NOISE_SIGMA", 0.15)) ** 2

        # ----- 1. Gather georeferenced measurements -----
        valid_measurements = [
            m for m in self.measurement_log
            if m["x_local"] is not None and m["y_local"] is not None
        ]
        # Cap to most-recent N if log is very long (keeps newest evidence)
        max_meas = int(getattr(config, "ESTIMATOR_MAX_MEASUREMENTS", 2000))
        if len(valid_measurements) > max_meas:
            valid_measurements = valid_measurements[-max_meas:]
        if not valid_measurements:
            print("[Estimator] No georeferenced measurements.")
            self.backtrack_map = None
            return

        pts = np.array([[m["x_local"], m["y_local"]] for m in valid_measurements])
        _, b_flat = r.physics_tree.query(pts, k=1)
        b_i_all = (b_flat // n_width).astype(int)
        b_j_all = (b_flat %  n_width).astype(int)

        # ----- 2. Build observation vector c_obs (length M) -----
        # Prefer the raw concentration value (SIM mode: directly from DV field;
        # always has the widest dynamic range and best discrimination).
        # In REAL mode fall back to inverting the sensor dose-response curve
        # used by _synthesize_sim_sensors: eff = score, intensity = eff^(1/0.35).
        # This restores the linear-in-concentration scale the analytic plume
        # model expects, so the profile-MLE for Q is unbiased.
        def _obs_for_estimator(m: dict) -> float:
            c = m.get("conc", 0.0) or 0.0
            if c > 0.0:
                return float(c)
            score = m.get("pollution_score",
                          self.pollution_score(m.get("ph"), m.get("ec"), m.get("do")))
            if score <= 0.0:
                return 0.0
            return float(score) ** (1.0 / 0.35)   # undo sqrt-style compression

        c_obs = np.array([_obs_for_estimator(m) for m in valid_measurements],
                         dtype=np.float64)

        # ----- 3. For each candidate cell s, build "shape function" g_k(s) =
        #         analytic-plume value at measurement k assuming source at s
        #         with UNIT strength.  Then maximize over Q analytically:
        #
        #             Q*(s) = (Σ_k g_k(s) c_obs_k)  /  (Σ_k g_k(s)²)
        #
        #         Profile log-likelihood (Q marginalised at MLE):
        #
        #             log L(s) = - ||c_obs - Q*(s)·g(s)||² / (2σ²)
        #                      = - (||c||² - Q*² · Σg²) / (2σ²)
        #
        # We accumulate Σg², Σ(g·c) over measurements in one vectorised pass.
        # -----
        i_grid, j_grid = np.meshgrid(np.arange(n_stream), np.arange(n_width),
                                     indexing="ij")
        sum_g2 = np.zeros((n_stream, n_width), dtype=np.float64)
        sum_gc = np.zeros((n_stream, n_width), dtype=np.float64)
        sum_c2 = float(np.sum(c_obs ** 2))

        for k, m in enumerate(valid_measurements):
            b_i = int(b_i_all[k]); b_j = int(b_j_all[k])

            # Analytic Gaussian continuous-source plume (advection-dominated)
            d_s = (b_i - i_grid) * ds                    # downstream distance
            d_n = (b_j - j_grid) * dn                    # lateral offset
            valid = d_s > ds                             # buoy must be downstream
            with np.errstate(divide="ignore", invalid="ignore"):
                g = np.where(
                    valid,
                    (1.0 / np.sqrt(np.maximum(d_s, ds))) *
                    np.exp(-(d_n * d_n * U) / (4.0 * D_T * np.maximum(d_s, ds))),
                    0.0,
                )
            sum_g2 += g * g
            sum_gc += g * c_obs[k]

        # ----- 4. Profile-MLE for source strength Q at every candidate -----
        # eps avoids divide-by-zero for cells that no measurement could see.
        eps = max(1e-9, 1e-6 * float(sum_g2.max() or 1.0))
        Q_star = sum_gc / (sum_g2 + eps)
        Q_star = np.clip(Q_star, 0.0, None)              # Q ≥ 0 prior

        # Residual sum of squares (after substituting Q*):
        #   ||c - Q*g||² = ||c||² - 2 Q* (g·c) + Q*² (g·g)
        # at Q* = (g·c)/(g·g)  →  RSS = ||c||² − (g·c)² / (g·g)
        rss = sum_c2 - (sum_gc * sum_gc) / (sum_g2 + eps)
        rss = np.maximum(rss, 0.0)

        # Profile log-likelihood (cells with no coverage get -inf-ish penalty)
        log_L = -rss / (2.0 * sigma2)

        # ----- 5. Convert to probability (softmax with max-subtraction) -----
        # Mask cells that no measurement could see (their g is identically 0
        # so RSS = ||c||² — a constant; they get equal probability among
        # themselves. That's not informative, so down-weight them.)
        coverage_mask = sum_g2 > 1e-12 * float(sum_g2.max() or 1.0)
        log_L = np.where(coverage_mask, log_L, log_L.min() - 1e3)

        log_L -= log_L.max()
        prob = np.exp(log_L)
        total = prob.sum()
        if total <= 0 or not np.isfinite(total):
            print("[Estimator] Degenerate posterior; widen ESTIMATOR_NOISE_SIGMA or collect more data.")
            self.backtrack_map = None
            return
        prob /= total

        self.backtrack_map = prob
        self.backtracking = False

        # ----- 6. Report -----
        i, j = np.unravel_index(np.argmax(prob), prob.shape)
        est_x = float(r.vis_x[i, j])
        est_y = float(r.vis_y[i, j])
        Q_at_peak = float(Q_star[i, j])
        est_lat, est_lon = self.georef.sim_cartesian_to_gps(est_x, est_y)

        n_det = sum(1 for m in valid_measurements if m.get("severity"))
        n_non = len(valid_measurements) - n_det

        # 1-sigma equivalent radius from the posterior (rough credible region)
        # Estimated by computing the standard deviation of distance from the peak,
        # weighted by the posterior probability.
        xs_grid = r.vis_x
        ys_grid = r.vis_y
        dx2 = (xs_grid - est_x) ** 2
        dy2 = (ys_grid - est_y) ** 2
        var_r = float(np.sum(prob * (dx2 + dy2)))
        std_r = float(np.sqrt(var_r))

        if self.source_local is not None:
            tx, ty = self.source_local
            err = float(np.hypot(est_x - tx, est_y - ty))
            print(f"[Estimator] {len(valid_measurements)} measurements "
                  f"({n_det} alarms, {n_non} non-alarm); Q*={Q_at_peak:.3f}; "
                  f"peak ({est_x:.1f}, {est_y:.1f}); true ({tx:.1f}, {ty:.1f}); "
                  f"error = {err:.1f} m;  1-sigma ~ {std_r:.1f} m")
        else:
            print(f"[Estimator] {len(valid_measurements)} measurements "
                  f"({n_det} alarms); Q*={Q_at_peak:.3f}; "
                  f"peak local ({est_x:.1f}, {est_y:.1f}) "
                  f"GPS ({est_lat:.6f}, {est_lon:.6f});  1-sigma ~ {std_r:.1f} m")

    def reset_contamination(self):
        self.contamination_detected = False
        self.contamination_severity = None
        self.contamination_rules_hit = []
        self.detection_history = []
        self._last_detection_sample_t = -1e9
        self.backtrack_map = None
        self.backtracking = False

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
        if self.backtrack_map is None or self.river is None:
            return None
        i, j = np.unravel_index(np.argmax(self.backtrack_map), self.backtrack_map.shape)
        return self.georef.sim_cartesian_to_gps(float(self.river.vis_x[i, j]),
                                                 float(self.river.vis_y[i, j]))

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
