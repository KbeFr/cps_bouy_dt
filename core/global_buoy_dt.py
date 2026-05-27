from enum import Enum
import numpy as np

import config
from core.buoy_dt.buoy_comm import BuoyComm
from core.buoy_dt.buoy_models import BuoyParticle, BuoyEKF5
from core.buoy_dt.buoy_sensor import BuoySensor
from core.georef import GeoReference
from core.river_model import River


class BuoyMode(str, Enum):
    SIM = "simulated"
    REAL = "real"
    HIL = "hil"


class BuoyDigitalTwin:
    def __init__(self):
        self.mode: BuoyMode | None = None

        self.river: River | None = None
        self.georef: GeoReference | None = None

        # --- Locational state ---
        self.local_x: float | None = None
        self.local_y: float | None = None
        self.start_local: tuple | None = None
        self.lat: float | None = None
        self.lon: float | None = None
        self.buoy_history_gps: list[tuple] = []

        self.model_used: BuoyParticle | BuoyEKF5 | None = None

        self.last_time_real_used = 0.0
        self.dt = 1.0   # set per-tick by SimulationState (real-time)

        # -------- Sensors --------
        self.sensor: BuoySensor = BuoySensor()

        # -------- Communication (ThingsBoard) --------
        self.comm_dt = BuoyComm()
        self.comm_dt.login()
        self.comm_dt.start_polling(callback=self.sensor.update_sensor)

    # ------------------------------------------------------------------
    def step(self):
        if self.model_used is None:
            return

        if self.mode == BuoyMode.SIM or self.mode == BuoyMode.HIL:
            self.model_used.update_sim(self.dt)

        elif self.mode == BuoyMode.REAL:
            if not isinstance(self.model_used, BuoyEKF5):
                if not self._init_real_model_from_latest_sample():
                    return

            if self.last_time_real_used >= self.comm_dt.last_update:
                # No new data — dead-reckon with river flow
                if self.local_x is None or self.local_y is None or self.river is None:
                    return
                pts = np.array([[self.local_x, self.local_y]])
                _, idx = self.river.physics_tree.query(pts, k=1)
                speed, angle = self.river.grid_data[int(idx[0])]
                river_vx = float(speed * np.cos(angle))
                river_vy = float(speed * np.sin(angle))
                self.model_used.propagate_dead_reckoning(river_vx, river_vy, 0.0, 0.0, self.dt)
            else:
                gps_local = self.get_local_gps_from_sensor()
                if gps_local is not None:
                    x_sim, y_sim, vx_sim, vy_sim = gps_local
                    self.model_used.update_gps(x_sim, y_sim, vx_sim, vy_sim)

                imu_batch = self.sensor.data.imu or []
                self.sensor.data.imu = []
                for sample in imu_batch:
                    if sample.dt > 0:
                        self.model_used.propagate_imu(ax=sample.ax, ay=sample.ay,
                                                       gz=sample.gz, dt=sample.dt)

                self.last_time_real_used = self.comm_dt.last_update

        new_x, new_y = self.model_used.position
        self.update_coords_from_local(new_x, new_y)

        # Track GPS history (guard against None until georef is wired)
        if self.lat is not None and self.lon is not None:
            self.buoy_history_gps.append((self.lat, self.lon))
            if len(self.buoy_history_gps) > 500:
                self.buoy_history_gps = self.buoy_history_gps[-500:]

    # ------------------------------------------------------------------
    def get_local_gps_from_sensor(self):
        gps = self.sensor.data.gps
        if gps is None or self.georef is None:
            return None
        x, y = self.georef.gps_to_local(gps.lat, gps.lon)
        vx, vy = self.georef.gps_components_to_sim(gps.vn, gps.vs)
        return x, y, vx, vy

    def update_coords_from_local(self, x: float, y: float):
        self.local_x = float(x)
        self.local_y = float(y)
        if self.georef is not None and self.georef.is_set:
            lat, lon = self.georef.sim_cartesian_to_gps(x, y)
            self.lat = lat
            self.lon = lon

    def update_coords_from_gps(self, lat: float, lon: float):
        self.lat = float(lat)
        self.lon = float(lon)
        if self.georef is not None and self.georef.is_set:
            x, y = self.georef.gps_to_local(lat, lon)
            self.local_x = x
            self.local_y = y

    def set_start_from_gps(self, lat: float, lon: float):
        self.update_coords_from_gps(lat, lon)
        if self.model_used is not None:
            self.model_used.position = (self.local_x, self.local_y)

    def set_start_from_local(self, x: float, y: float):
        self.update_coords_from_local(x, y)
        if self.model_used is not None:
            self.model_used.position = (self.local_x, self.local_y)

    # ------------------------------------------------------------------
    def _init_real_model_from_latest_sample(self) -> bool:
        """Create the EKF once the first live GPS sample is available."""
        if self.sensor.data.gps is None:
            return False

        gps_local = self.get_local_gps_from_sensor()
        if gps_local is None:
            return False

        ekf = BuoyEKF5()
        x, y, vx, vy = gps_local
        theta = 0.0
        imu_batch = self.sensor.data.imu or []
        if imu_batch and self.georef is not None:
            imu = imu_batch[0]
            theta = self.georef.gps_heading_to_sim(np.arctan2(imu.my, imu.mx))
        ekf.initialize(x_sim=x, y_sim=y, vx_sim=vx, vy_sim=vy, theta_sim=theta)
        self.model_used = ekf
        self.last_time_real_used = self.comm_dt.last_update
        self.update_coords_from_local(x, y)
        print("[BUOY] REAL EKF initialised from live GPS")
        return True

    # ------------------------------------------------------------------
    def set_mode(self, mode):
        # Normalize: accept str or BuoyMode
        try:
            mode = BuoyMode(mode)
        except ValueError:
            mode = BuoyMode.SIM

        print(f"[BUOY] set_mode({mode.value})")

        if mode == BuoyMode.SIM or mode == BuoyMode.HIL:
            if self.local_x is None or self.local_y is None or self.river is None:
                print("[BUOY] Cannot init SIM model: river/position not ready")
                return
            self.model_used = BuoyParticle(self.river, self.local_x, self.local_y)

        elif mode == BuoyMode.REAL:
            # Need at least one GPS sample to initialise the EKF
            if not self._init_real_model_from_latest_sample():
                print("[BUOY] Waiting for first GPS sample before switching to REAL")
                # Keep a SIM particle around for display; step() will replace
                # it with an EKF as soon as live GPS telemetry arrives.
                if self.model_used is None and self.river is not None \
                   and self.local_x is not None:
                    self.model_used = BuoyParticle(self.river, self.local_x, self.local_y)
                self.mode = mode
                return

        self.mode = mode

    # ------------------------------------------------------------------
    def hard_reset(self):
        self.local_x = 0.0
        self.local_y = 0.0
        self.start_local = (0.0, 0.0)
        self.lat = config.MAP_DEFAULT_LAT
        self.lon = config.MAP_DEFAULT_LON
        self.buoy_history_gps = []

    def reset(self):
        if self.start_local is None:
            return
        x0, y0 = self.start_local
        self.local_x = x0
        self.local_y = y0
        self.buoy_history_gps = []
        if self.model_used is not None:
            self.model_used.position = (x0, y0)
        if self.georef is not None and self.georef.is_set:
            self.lat, self.lon = self.georef.sim_cartesian_to_gps(x0, y0)

    def set_river(self, river: River):
        self.river = river

    def set_georef(self, georef: GeoReference):
        self.georef = georef

    def set_local_2d_coords(self, x_local: float, y_local: float):
        self.local_x = x_local
        self.local_y = y_local

    @property
    def local_position(self) -> tuple[float, float]:
        return float(self.local_x), float(self.local_y)

    @property
    def gps_position(self) -> tuple[float, float]:
        return float(self.lat), float(self.lon)

    @property
    def formatted_local_x(self) -> str:
        return f"{self.local_x:.2f}" if self.local_x is not None else "--"

    @property
    def formatted_local_y(self) -> str:
        return f"{self.local_y:.2f}" if self.local_y is not None else "--"
