# =============================================================================
# core/buoy_dt/simple_controller.py
# Drop-in replacement for BuoyController — designed for the demo.
#
# Three behaviours, in priority order:
#
#   1. BANK_AVOID  — triggered when the buoy is within `avoid_threshold`
#                    fraction of the half-width from the bank.
#                    → steer toward centerline, thrust = 0.7, reason = "avoid"
#
#   2. SWEEP       — zigzag cross-stream to collect measurements at varying
#                    lateral offsets.  This is the fastest way to give the
#                    Bayesian estimator the lateral discrimination it needs.
#                    → alternates ±sweep_angle off the upstream direction
#                    → reason = "sweep"
#
#   3. DRIFT       — passive; let the river carry the buoy
#                    → thrust = 0.0, reason = "drift"
#
# Commands are packaged as MissionCommand (same dataclass as BuoyController),
# so they drop straight into BuoyParticle.set_mission() for the sim AND into
# BuoyComm.send_rpc_async() for the real buoy — see wire_controller_rpc() below.
# =============================================================================

from __future__ import annotations
from dataclasses import dataclass

import numpy as np
from enum import Enum, auto

# Re-use the existing MissionCommand dataclass — no schema change needed.
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


# ---------------------------------------------------------------------------
# Internal state labels (not sent over the wire)
# ---------------------------------------------------------------------------
class _CtrlState(Enum):
    BANK_AVOID = auto()
    SWEEP      = auto()
    DRIFT      = auto()


# =============================================================================
# SimpleBuoyController
# =============================================================================

class BuoyController:
    """
    Reactive buoy controller for the digital-twin demo.
    """

    def __init__(
        self,
        river: River,
        river_heading: float,
        avoid_threshold: float = 0.72,
        sweep_angle: float = np.deg2rad(25),
        sweep_period: float = 55.0,
        enable_sweep: bool = True,
    ):
        self.river           = river
        self.river_heading   = river_heading
        self.avoid_threshold = avoid_threshold
        self.sweep_angle     = sweep_angle
        self.sweep_period    = sweep_period
        self.enable_sweep    = enable_sweep

        # Internal sweep state
        self._sweep_side : int   = +1    # +1 = left of upstream, -1 = right
        self._sweep_timer: float = 0.0   # accumulated sim-time on current side

        # Last computed state 
        self.last_state: _CtrlState = _CtrlState.DRIFT
        self.last_cmd:  MissionCommand | None = None

    def compute_command(
        self,
        x_sim: float,
        y_sim: float,
        dt: float = 1.0,
    ) -> MissionCommand:
        
        upstream   = self._upstream_heading(x_sim, y_sim)
        dist, vec_to_cl = self._centerline_info(x_sim, y_sim)
        limit      = self.river.half_width * self.avoid_threshold

        # -- Bank avoidance --
        if dist > limit:
            toward_cl = np.arctan2(vec_to_cl[1], vec_to_cl[0])
            
            # Blend via vector addition to safely handle the -pi/pi boundary
            v_x = 0.6 * np.cos(toward_cl) + 0.4 * np.cos(upstream)
            v_y = 0.6 * np.sin(toward_cl) + 0.4 * np.sin(upstream)
            heading = float(np.arctan2(v_y, v_x))

            # Clamped thrust back to the 0.0 - 1.0 scale
            cmd = self._cmd(heading, thrust=1.5, reason="avoid")
            self.last_state = _CtrlState.BANK_AVOID
            self.last_cmd   = cmd
            return cmd

        # --  Sweep --
        if self.enable_sweep:
            self._sweep_timer += dt
            if self._sweep_timer >= self.sweep_period:
                self._sweep_timer = 0.0
                self._sweep_side  = -self._sweep_side   # flip direction

            heading = _wrap_angle(upstream + self._sweep_side * self.sweep_angle)

            if not self._heading_is_safe(x_sim, y_sim, heading):
                self._sweep_side  = -self._sweep_side   # flip immediately
                self._sweep_timer = 0.0
                heading = _wrap_angle(upstream + self._sweep_side * self.sweep_angle)

            cmd = self._cmd(heading, thrust=1.5, reason="sweep")
            self.last_state = _CtrlState.SWEEP
            self.last_cmd   = cmd
            return cmd

        #  -- Passive drift --
        cmd = self._cmd(upstream, thrust=0.0, reason="drift")
        self.last_state = _CtrlState.DRIFT
        self.last_cmd   = cmd
        return cmd

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _upstream_heading(self, x: float, y: float) -> float:
        """Direction pointing upstream (against flow) in sim frame."""
        pts = np.array([[x, y]])
        _, idx = self.river.physics_tree.query(pts, k=1)
        _, flow_angle = self.river.grid_data[int(idx[0])]
        return float(flow_angle)   

    def _centerline_info(
        self, x: float, y: float
    ) -> tuple[float, tuple[float, float]]:
        """
        Returns
        -------
        dist         : distance from (x, y) to nearest centreline point (m)
        vec_to_cl    : (dx, dy) unit-ish vector from buoy to centreline
        """
        pts = np.array([[x, y]])
        dist, idx = self.river.centerline_tree.query(pts, k=1)
        dist = float(dist[0])
        idx  = int(idx[0])
        cx   = float(self.river.xc[idx])
        cy   = float(self.river.yc[idx])
        return dist, (cx - x, cy - y)

    def _heading_is_safe(
        self,
        x: float,
        y: float,
        heading: float,
        look_ahead_m: float = 6.0,
    ) -> bool:
        """
        Project the buoy `look_ahead_m` metres along `heading`.
        FIX 3: Safe if the projected point is inside the threshold, OR if the 
        heading actively brings the buoy closer to the centerline.
        """
        # Get current distance
        pts_current = np.array([[x, y]])
        dist_current, _ = self.river.centerline_tree.query(pts_current, k=1)
        
        # Get projected future distance
        xp = x + look_ahead_m * np.cos(heading)
        yp = y + look_ahead_m * np.sin(heading)
        pts_future = np.array([[xp, yp]])
        dist_future, _ = self.river.centerline_tree.query(pts_future, k=1)
        
        # Condition 1: Projected point is entirely within the safe channel limits
        is_within_limits = float(dist_future[0]) < self.river.half_width * self.avoid_threshold
        
        # Condition 2: The maneuver actively reduces our distance to the centerline
        is_getting_closer = float(dist_future[0]) < float(dist_current[0])
        
        return is_within_limits or is_getting_closer

    def _cmd(
        self,
        heading_sim: float,
        thrust: float,
        reason: str,
    ) -> MissionCommand:
        """The helper method that packages the command."""
        heading_world = _wrap_angle(heading_sim + self.river_heading)
        return MissionCommand(
            heading_sim   = float(heading_sim),
            heading_world = float(heading_world),
            thrust        = float(thrust),
            reason        = reason,
        )