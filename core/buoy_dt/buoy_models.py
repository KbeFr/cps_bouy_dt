import numpy as np

from core.buoy_dt.buoy_controller import MissionCommand
from core.river_model import River

import config


# =============================================================================
# BuoyEKF  5 State model 
# =============================================================================

class BuoyEKF5:
    """
    5-state EKF: x = [x, y, vx, vy, θ]  all in sim frame.

    Propagation input (IMU):
        ax, ay : accelerometer in body frame (m/s²) — rotated to sim frame using θ
        gz     : gyro Z yaw rate (rad/s)             — frame invariant
        dt     : seconds since last sample

    Observation (GPS):
        x_sim, y_sim   : from georef.gps_to_local()
        vx_sim, vy_sim : from georef.gps_components_to_sim(north_speed, east_speed)

    Dead-reckoning (no data):
        river_vx, river_vy : from river.grid_data at current position
        cmd_vx, cmd_vy     : boat thrust contribution from last MissionCommand
    """

    def __init__(
            self,
            xy_gps_std: float = 2.0,  # GPS position noise  (m)
            v_gps_std: float = 0.1,  # GPS velocity noise  (m/s)
            ax_std: float = 0.3,  # accelerometer noise (m/s²)
            ay_std: float = 0.3,
            gz_std: float = 0.05,  # gyro noise          (rad/s)
            theta_init_std: float = 0.5,
    ):
        self.x = None  # [x, y, vx, vy, θ]
        self.P = None  # 5×5 covariance
        self.initialized = False

        # Observation noise R — GPS gives us 4 of 5 states directly
        self.R_gps = np.diag([
            xy_gps_std ** 2,
            xy_gps_std ** 2,
            v_gps_std ** 2,
            v_gps_std ** 2,
        ])

        # Process noise Q — driven by IMU uncertainty
        self._ax_std = ax_std
        self._ay_std = ay_std
        self._gz_std = gz_std
        self._theta_init_std = theta_init_std
        self._xy_std = xy_gps_std
        self._v_std = v_gps_std

    # ------------------------------------------------------------------
    def initialize(
            self,
            x_sim: float, y_sim: float,
            vx_sim: float, vy_sim: float,
            theta_sim: float,
    ):
        self.x = np.array([x_sim, y_sim, vx_sim, vy_sim, theta_sim], dtype=float)
        self.P = np.diag([
            self._xy_std ** 2,
            self._xy_std ** 2,
            self._v_std ** 2,
            self._v_std ** 2,
            self._theta_init_std ** 2,
        ])
        self.initialized = True

    # ------------------------------------------------------------------
    def propagate_imu(self, ax: float, ay: float, gz: float, dt: float):
        """
        IMU prediction step.

        ax, ay are in the BODY frame — we rotate them to sim frame using
        the current heading estimate θ before integrating into velocity.

        gz is frame-invariant — use directly.
        """
        if not self.initialized:
            return

        x, y, vx, vy, θ = self.x

        # Rotate body accelerations to sim frame
        cθ, sθ = np.cos(θ), np.sin(θ)
        ax_sim = ax * cθ - ay * sθ
        ay_sim = ax * sθ + ay * cθ

        # State update
        x_new = x + vx * dt + 0.5 * ax_sim * dt ** 2
        y_new = y + vy * dt + 0.5 * ay_sim * dt ** 2
        vx_new = vx + ax_sim * dt
        vy_new = vy + ay_sim * dt
        θ_new = θ + gz * dt

        self.x = np.array([x_new, y_new, vx_new, vy_new, θ_new])

        # Jacobian F (5×5) — linearised around current θ
        # Non-trivial entries: ∂vx/∂θ, ∂vy/∂θ, and their effect on position
        dvx_dθ = (-ax * sθ - ay * cθ) * dt
        dvy_dθ = (ax * cθ - ay * sθ) * dt
        dx_dθ = (-ax * sθ - ay * cθ) * 0.5 * dt ** 2
        dy_dθ = (ax * cθ - ay * sθ) * 0.5 * dt ** 2

        F = np.array([
            [1, 0, dt, 0, dx_dθ],
            [0, 1, 0, dt, dy_dθ],
            [0, 0, 1, 0, dvx_dθ],
            [0, 0, 0, 1, dvy_dθ],
            [0, 0, 0, 0, 1],
        ])

        # Process noise Q — mapped from sensor noise to state space
        # [ax_sim, ay_sim, gz] → [x, y, vx, vy, θ]
        L = np.array([
            [0.5 * dt ** 2 * cθ, -0.5 * dt ** 2 * sθ, 0],
            [0.5 * dt ** 2 * sθ, 0.5 * dt ** 2 * cθ, 0],
            [dt * cθ, -dt * sθ, 0],
            [dt * sθ, dt * cθ, 0],
            [0, 0, dt],
        ])
        Q_sensor = np.diag([self._ax_std ** 2, self._ay_std ** 2, self._gz_std ** 2])
        Q = L @ Q_sensor @ L.T

        self.P = F @ self.P @ F.T + Q

    # ------------------------------------------------------------------
    def propagate_dead_reckoning(
            self,
            river_vx: float, river_vy: float,
            cmd_vx: float, cmd_vy: float,
            dt: float,
    ):
        """
        Dead-reckoning when no IMU data is available.

        Uses river flow + boat command as the velocity model instead of
        integrating accelerations.  Much gentler covariance growth than
        propagating with zero input.

        river_vx/vy : from river.grid_data at current estimated position
        cmd_vx/vy   : thrust vector from last MissionCommand
                      = thrust * boat_speed * [cos(θ_cmd), sin(θ_cmd)]
        """
        if not self.initialized:
            return

        x, y, vx, vy, θ = self.x

        # Best velocity estimate: blend EKF velocity with physical model
        # Weight toward physical model as uncertainty grows
        vx_model = river_vx + cmd_vx
        vy_model = river_vy + cmd_vy

        # Use model velocity directly (GPS velocity state becomes stale)
        x_new = x + vx_model * dt
        y_new = y + vy_model * dt

        self.x = np.array([x_new, y_new, vx_model, vy_model, θ])

        # Covariance growth — position uncertainty grows with model error
        # Heading stays constant (no yaw rate input)
        F = np.eye(5)
        F[0, 2] = dt
        F[1, 3] = dt

        # Dead-reckoning process noise — larger than IMU since we're guessing
        dr_pos_std = 0.5  # m per second of dead-reckoning
        dr_vel_std = 0.2  # m/s model mismatch
        Q_dr = np.diag([
            (dr_pos_std * dt) ** 2,
            (dr_pos_std * dt) ** 2,
            (dr_vel_std * dt) ** 2,
            (dr_vel_std * dt) ** 2,
            (self._gz_std * dt) ** 2,  # heading drifts slowly
        ])

        self.P = F @ self.P @ F.T + Q_dr

    # ------------------------------------------------------------------
    def update_gps(
            self,
            x_sim: float, y_sim: float,
            vx_sim: float, vy_sim: float,
    ):
        """
        GPS correction step — observes all 4 of [x, y, vx, vy].
        θ is unobserved by GPS (heading from mag/gyro only).
        """
        if not self.initialized:
            return

        H = np.array([
            [1, 0, 0, 0, 0],
            [0, 1, 0, 0, 0],
            [0, 0, 1, 0, 0],
            [0, 0, 0, 1, 0],
        ], dtype=float)

        z = np.array([x_sim, y_sim, vx_sim, vy_sim])
        z_hat = H @ self.x
        S = H @ self.P @ H.T + self.R_gps
        K = self.P @ H.T @ np.linalg.inv(S)

        self.x = self.x + K @ (z - z_hat)
        self.P = (np.eye(5) - K @ H) @ self.P

    # ------------------------------------------------------------------
    @property
    def position(self) -> tuple[float, float]:
        return float(self.x[0]), float(self.x[1])

    @position.setter
    def position(self, value: tuple[float, float]):
        if len(value) != 2:
            raise ValueError("Position must be a tuple of (x, y)")
        
        self.x = [float(value[0]), float(value[1])]

    @property
    def velocity_sim(self) -> tuple[float, float]:
        return float(self.x[2]), float(self.x[3])

    @property
    def heading_sim(self) -> float:
        return float(self.x[4])

    @property
    def speed(self) -> float:
        return float(np.sqrt(self.x[2] ** 2 + self.x[3] ** 2))

    @property
    def position_std(self) -> tuple[float, float]:
        """1-sigma position uncertainty from diagonal of P."""
        return float(np.sqrt(self.P[0, 0])), float(np.sqrt(self.P[1, 1]))


# =============================================================================
# BuoyParticle  (simulation instance)
# =============================================================================

class BuoyParticle:
    """
    Passive advection + optional active steering for initial validation.

    When a MissionCommand is active, the flow advection is augmented with
    a thrust component in the commanded heading direction.  The boundary
    enforcer still applies so the sim buoy cannot exit the channel.
    """

    def __init__(self, river: River, x0: float, y0: float):
        self.river = river
        self.D_T = config.DEFAULT_D_T_BUOY
        self.x = float(x0)
        self.y = float(y0)
        self.theta = 0.0

        self._mission: MissionCommand | None = None

    # ------------------------------------------------------------------
    def set_mission(self, cmd: MissionCommand | None):
        """Push a new heading command from the controller."""
        self._mission = cmd

    # ------------------------------------------------------------------
    def update_sim(self, dt: float):
        """
        Passive flow + optional active heading thrust.

        Active thrust is added in the commanded direction and scaled by
        cmd.thrust.  River flow still dominates — the boat is not fast
        enough to fight the current, it can only steer within it.
        """
        pts = np.array([[self.x, self.y]])
        _, idx = self.river.physics_tree.query(pts, k=1)
        speed, angle = self.river.grid_data[int(idx[0])]

        # Base passive advection
        dx = speed * dt * np.cos(angle)
        dy = speed * dt * np.sin(angle)

        # Surface turbulence (cross-stream noise)
        noise = np.random.normal(0, np.sqrt(2 * self.D_T * dt))
        dx += noise * (-np.sin(angle))
        dy += noise * (np.cos(angle))

        # Active steering contribution
        if self._mission is not None and self._mission.reason != "stop":
            boat_speed = speed * 0.6 * self._mission.thrust  # boat ~ 60% of river speed
            θ_cmd = self._mission.heading_sim
            dx += boat_speed * dt * np.cos(θ_cmd)
            dy += boat_speed * dt * np.sin(θ_cmd)
            self.theta = θ_cmd

        self.x += dx
        self.y += dy
        self._enforce_boundaries()


    # ------------------------------------------------------------------
    def _enforce_boundaries(self):
        pts = np.array([[self.x, self.y]])
        dist, cl_idx = self.river.centerline_tree.query(pts, k=1)
        dist = float(dist[0])
        cl_idx = int(cl_idx[0])
        limit = self.river.half_width * 0.95

        if dist <= limit:
            return

        cx = self.river.xc[cl_idx]
        cy = self.river.yc[cl_idx]
        vx = self.x - cx
        vy = self.y - cy
        norm = np.sqrt(vx ** 2 + vy ** 2)
        if norm > 1e-9:
            self.x = cx + (vx / norm) * limit
            self.y = cy + (vy / norm) * limit

    def reset(self, x0: float, y0: float):
        self.x, self.y = float(x0), float(y0)

    @property
    def position(self) -> tuple[float, float]:
        return (self.x, self.y)

    @position.setter
    def position(self, value: tuple[float, float]):
        if len(value) != 2:
            raise ValueError("Position must be a tuple of (x, y)")
        
        self.x = value[0]
        self.y = value[1]


    @property
    def spoofed_gps(self) -> tuple[float, float]:
        """Sim position expressed as GPS — for ThingsBoard map display."""
        return self.river.georef.sim_cartesian_to_gps(self.x, self.y)


# =============================================================================
# Helpers
# =============================================================================

def _wrap_angle(a: float) -> float:
    """Wrap angle to [-π, π]."""
    return (a + np.pi) % (2 * np.pi) - np.pi
