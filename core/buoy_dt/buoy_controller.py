from dataclasses import dataclass
from math import pi
import numpy as np
from core.georef import _wrap_angle
from core.river_model.river_model import River


@dataclass
class MissionCommand:
    heading_sim: float
    heading_world: float
    thrust: float = 0.5
    reason: str = "follow"


class BuoyController:
    def __init__(self, river: River, river_heading: float,
                 max_cross_angle: float = np.deg2rad(25)):
        self.river           = river
        self.river_heading   = river_heading
        self.max_cross_angle = max_cross_angle

    def compute_command(self, x: float, y: float, reverse: bool = False) -> MissionCommand:
        pts = np.array([[x, y]])
        dist, cl_idx = self.river.centerline_tree.query(pts, k=1)
        cl_idx = int(cl_idx[0])
        dist   = float(dist[0])

        grid_idx = min(cl_idx * self.river.n_width + self.river.n_width // 2,
                    len(self.river.grid_data) - 1)
        _, flow_angle = self.river.grid_data[grid_idx]

        cross_frac = np.clip(dist / self.river.half_width, 0.0, 1.0)

        # flow_angle is arctan2(vy, vx) in sim space — no negation needed
        # reverse flips 180° to go upstream
        heading = flow_angle + np.pi
        thrust  = 10
        reason  = "backtrack" if reverse else ("follow" if cross_frac < 0.05 else "correct")

        heading_world = _wrap_angle(heading + self.river_heading)
        return MissionCommand(
            heading_sim=float(heading),
            heading_world=float(heading_world),
            thrust=float(thrust),
            reason=reason,
        )