from dataclasses import dataclass
import numpy as np
from core.georef import _wrap_angle
from core.river_model import River


@dataclass
class MissionCommand:
    """
    High-level command exchanged between controller and buoy instances.
    Python sends heading_world to ESP32 via RPC.
    Sim receives heading_sim directly.
    """
    heading_sim: float  # radians, sim frame
    heading_world: float  # radians, world/compass frame (send to ESP32)
    thrust: float = 0.5  # 0–1 forward effort scale
    reason: str = "de"  # "de" | "centerline" | "stop"


class BuoyController:
    """
    Mission planner that reads the diffusion field and produces MissionCommands.

    Design principles
    -----------------
    * Works in sim-frame throughout.  World-frame heading is derived only
      at the moment of sending an RPC command.
    * Issues heading commands at ~0.5 Hz.  ESP32 runs its own IMU-based
      heading-hold PID at 20 Hz — the network delay is irrelevant for
      stabilisation.
    * Cross-stream displcenterlineacement is bounded to ±max_cross_angle from the
      upstream  direction to keep the boat away from banks and
      river traffic.
    * Falls back to "go upstream along centerline" when the DE gradient
      is too weak to trust.
    """

    def __init__(
            self,
            river: River,
            river_heading: float,  # georef.heading — sim↔world rotation
            max_cross_angle: float = np.deg2rad(30),  # max steer off-centerline
            gradient_min: float = 1e-4,  # below this |∇C|, ignore gradient
            safety_band: float = 0.65,  # stay within this fraction of half_width
    ):
        self.river = river
        self.river_heading = river_heading
        self.max_cross_angle = max_cross_angle
        self.gradient_min = gradient_min
        self.safety_band = safety_band

    # ------------------------------------------------------------------
    def compute_command(
            self,
            x_sim: float,
            y_sim: float,
            de_field,  # callable: (x, y) → (grad_x, grad_y, concentration)
            backtrack: bool = True,
    ) -> MissionCommand:
        """
        Compute the next mission command for the boat.

        Parameters
        ----------
        x_sim, y_sim : current position in sim frame
        de_field     : function returning (∇C_x, ∇C_y, C) at a point
        backtrack    : True = go toward source (up-gradient); False = passive
        """

        # Upstream centerline direction at current position
        upstream_heading = self._upstream_heading(x_sim, y_sim)

        if not backtrack:
            return self._make_command(upstream_heading, thrust=0.0, reason="centerline")

        # DE gradient at current position
        gx, gy, conc = de_field(x_sim, y_sim)
        grad_mag = np.sqrt(gx ** 2 + gy ** 2)

        if grad_mag < self.gradient_min:
            # Gradient too weak: go straight upstream, low thrust
            return self._make_command(upstream_heading, thrust=0.3, reason="centerline")

        # 3. Direction toward source = opposite of ∇C (diffusion spreads downstream)
        #    ∇C points from low→high concentration, i.e. toward source
        source_heading = np.arctan2(gy, gx)  # toward high-C = toward source

        # 4. Clamp cross-stream component
        #    Project source_heading onto [-max_cross, +max_cross] around upstream
        angle_diff = _wrap_angle(source_heading - upstream_heading)
        clamped = np.clip(angle_diff, -self.max_cross_angle, self.max_cross_angle)
        heading = upstream_heading + clamped

        # 5. Safety check — would this heading push us toward the bank?
        heading = self._safety_clamp(x_sim, y_sim, heading, upstream_heading)

        thrust = np.clip(grad_mag / (grad_mag + self.gradient_min * 10), 0.3, 0.9)
        return self._make_command(heading, thrust=thrust, reason="de")




    # ------------------------------------------------------------------
    def _upstream_heading(self, x: float, y: float) -> float:
        """
        Direction pointing upstream (against flow) at the nearest grid cell.
        Returns angle in sim frame.
        """
        pts = np.array([[x, y]])
        _, idx = self.river.physics_tree.query(pts, k=1)
        _, flow_angle = self.river.grid_data[int(idx[0])]
        return float(flow_angle + np.pi)  # flip downstream → upstream

    # ------------------------------------------------------------------
    def _safety_clamp(
            self,
            x: float, y: float,
            heading: float,
            fallback: float,
            look_ahead: float = 5.0,  # metres to project forward for safety check
    ) -> float:
        """
        If the commanded heading would move the boat outside the safety band,
        fall back to the centerline-upstream heading.
        """
        x_proj = x + look_ahead * np.cos(heading)
        y_proj = y + look_ahead * np.sin(heading)

        pts = np.array([[x_proj, y_proj]])
        dist, _ = self.river.centerline_tree.query(pts, k=1)
        limit = self.river.half_width * self.safety_band

        if float(dist[0]) > limit:
            return fallback
        return heading

    # ------------------------------------------------------------------
    def _make_command(self, heading_sim: float, thrust: float, reason: str) -> MissionCommand:
        """Convert sim-frame heading to world frame and package as MissionCommand."""
        heading_world =  _wrap_angle(heading_sim + self.river_heading)
        return MissionCommand(
            heading_sim=float(heading_sim),
            heading_world=float(heading_world),
            thrust=float(thrust),
            reason=reason,
        )
