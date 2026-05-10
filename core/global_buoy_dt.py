from enum import auto, Enum

import numpy as np

import config
from core.buoy_dt.buoy_comm import BuoyComm
from core.buoy_dt.buoy_models import BuoyParticle, BuoyEKF5
from core.buoy_dt.buoy_sensor import BuoySensor
from core.georef import GeoReference
from core.river_model import River


# =============================================================================
# Enums & Data Structures
# =============================================================================

class BuoyMode(str, Enum):
    SIM = "simulated"
    REAL = "real"
    HIL = "hil"

class BuoyDigitalTwin():
    def __init__(self, mode: BuoyMode = BuoyMode.SIM):
        self.mode = None

        # Needs to be set separately
        self.river: River = None
        self.georef: GeoReference = None

        # --- Locational state ---
        self.local_x = None
        self.local_y = None
        self.start_local: tuple = None
        self.lat = None
        self.lon = None
        self.buoy_history_gps: list = []

        self.model_used: BuoyParticle | BuoyEKF5 = None

        self.last_time_real_used = 0.0

        self.dt = 0.5

        # -------- Buoy Sensors --------------
        self.sensor: BuoySensor = BuoySensor()

        # -------- Buoy Communication (thingsboard) --------

        # Get comm DT for real wordl data
        self.comm_dt = BuoyComm()
        # Attempt ThingsBoard login on startup (non-fatal if offline)
        self.comm_dt.login()
        # Wire ThingsBoard polling → simulation sensor cache
        self.comm_dt.start_polling(callback=self.sensor.update_sensor)

    # Step the DT one simulation step based on what mode we are in
    def step(self):
        
        if self.mode == BuoyMode.SIM:
            self.model_used.update_sim(self.dt)

        elif self.mode == BuoyMode.REAL:
            # Should be initialized, but we dont know if the data we get is already updated, check this first
            if self.last_time_real_used >= self.comm_dt.last_update:

                print("Real data used in previous updated, dead reckoning")

                # before calling dead reckoning
                pts = np.array([[self.local_x, self.local_y]])
                _, idx = self.river.physics_tree.query(pts, k=1)
                speed, angle = self.river.grid_data[int(idx[0])]
                river_vx = speed * np.cos(angle)
                river_vy = speed * np.sin(angle)
                # cmd_vx/vy from last mission command (store it on self) #TODO

                self.model_used.propagate_dead_reckoning(river_vx, river_vy, 0.0, 0.0, self.dt)

            else:
                # Use real data gathered
                x_sim, y_sim, vx_sim, vy_sim = self.get_local_gps_from_sensor()

                # Update model with GPS data
                self.model_used.update_gps(x_sim, y_sim, vx_sim, vy_sim)

                # Then we do IMU propagation through the batch
                imu_batch = self.sensor.data.imu or []
                self.sensor.data.imu = []                  # consume so it doesn't replay
                for sample in imu_batch:
                    self.model_used.propagate_imu(ax=sample.ax, ay=sample.ay,
                                                gz=sample.gz, dt=sample.dt)

                self.last_time_real_used = self.comm_dt.last_update

        elif self.mode == BuoyMode.HIL:
            # Simulate the buoy but give real steering commands
            self.model_used.update_sim(self.dt)

        # Update the Global dt based on model propagation

    
        new_x, new_y = self.model_used.position
        self.update_coords_from_local(new_x, new_y)

        # Update the history
        self.buoy_history_gps.append((self.lat, self.lon))
        if len(self.buoy_history_gps) > 500:
            self.buoy_history_gps = self.buoy_history_gps[-500:]

    def step_command(self):
        pass

    def get_local_gps_from_sensor(self):
        gps = self.sensor.data.gps
        if gps is None or self.georef is None:
            print("[BuoyDigitalTwin] GPS or GeoRef None, get_local_gps_from_sensor failed ")
            return None
        x, y = self.georef.gps_to_local(gps.lat, gps.lon)
        vx, vy = self.georef.gps_components_to_sim(gps.vn, gps.vs)
        return x, y, vx, vy

    def update_coords_from_local(self, x: float, y: float):
        self.local_x = x
        self.local_y = y
        if self.georef is not None:
            lat, lon = self.georef.sim_cartesian_to_gps(x, y)
            self.lat = lat
            self.lon = lon
        else:
            print("Georef not set yet, only local coord updated")

    def update_coords_from_gps(self, lat: float, lon: float):
        self.lat = lat
        self.lon = lon
        if self.georef is not None:
            x, y = self.georef.gps_to_local(lat=lat, lon=lon)
            self.local_x = x
            self.local_y = y
        else:
            print("Georef not set yet, only local coord updated")

    def set_start_from_gps(self , lat : float , lon : float):
        self.update_coords_from_gps(lat, lon)
        self.model_used.position = (self.local_x , self.local_y)

    def set_start_from_local(self, x : float , y : float):
        self.update_coords_from_local(x ,y )
        self.model_used.position = (self.local_x , self.local_y)

    def set_mode(self, mode: BuoyMode):
        
        print(f"[BUOY] Setmode called with mode : {mode}")

        self.model_used = BuoyParticle(self.river, self.local_x, self.local_y)


        if self.mode == mode:
            print("[BUOY] Mode set is same as buoy mode, nothing changes")
            return


        if mode == BuoyMode.SIM:
            print("[BUOY] Switching to Simulation")
            self.model_used = BuoyParticle(self.river, self.local_x, self.local_y)
        elif mode == BuoyMode.REAL:
            self.model_used = BuoyEKF5()
            if self.sensor.data.gps is None or self.sensor.data.imu is None:
                print("[BUOY] Waiting for initial sensor data to switch to REAL mode...")
                return  ## Check that this can loop tho
            print("[BUOY] Switching to real data, checking thingsboard sensor data ... ")

            gps_data = self.sensor.data.gps

            self.lat = gps_data.lat
            self.lon = gps_data.lon

            x, y, vx, vy = self.get_local_gps_from_sensor()

            self.local_x = x
            self.local_y = y

            # Use first imu data of batch for initial theta guess
            imu = self.sensor.data.imu[0]

            # TODO : Check if this calc is right for theta calculations
            heading_local = self.georef.gps_heading_to_sim(np.arctan2(imu.my, imu.mx))

            self.model_used.initialize(x_sim=x, y_sim=y, vx_sim=vx, vy_sim=vy, theta_sim=heading_local)
            self.model_used.initialized = True
            self.last_time_real_used = self.comm_dt.last_update

        elif (mode == BuoyMode.HIL):
            # Simulate the buoy but give real steering commands
            print("[BUOY] Buoy mode to HIL")
            self.model_used = BuoyParticle(self.river, self.local_x, self.local_y)

        self.mode = mode 

    """Called from control_panel for reset all"""
    def hard_reset(self):
        self.local_x = 0.0
        self.local_y = 0.0
        self.start_local = (0.0, 0.0)
        self.lat = config.MAP_DEFAULT_LAT
        self.lon = config.MAP_DEFAULT_LON
        self.buoy_history_gps = []

    def reset(self):
        x0, y0 = self.start_local
        self.local_x = x0
        self.local_y = y0
        self.buoy_history_gps = []

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
