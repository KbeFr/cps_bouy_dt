# =============================================================================
# core/georef.py — Georeferencing: local river coords <-> GPS
#                  + GPS polyline -> River topology conversion
# =============================================================================

import numpy as np
from scipy.spatial import cKDTree

METRES_PER_DEG_LAT = 111_320.0



class GeoReference:
    """
    Coordinates transformations using a Discretized KDTree approach.
    This guarantees a 1:1 mapping between the simulation's curvilinear grid
    and the real-world GPS map, eliminating 'zigzag' tracking artifacts.
    """

    def __init__(self):
        self.gps_points:   list[tuple]       = []
        self._origin_lat:  float             = 0.0
        self._origin_lon:  float             = 0.0
        self._is_set:      bool              = False
        self._is_built:    bool              = False
        
        # Discretized mapping arrays
        self.ds_length:    float             = 0.0
        self.xc:           np.ndarray        = np.array([])
        self.yc:           np.ndarray        = np.array([])
        self.gps_lats:     np.ndarray        = np.array([])
        self.gps_lons:     np.ndarray        = np.array([])
        self.nx_m:         np.ndarray        = np.array([])
        self.ny_m:         np.ndarray        = np.array([])
        self.sim_tree:     cKDTree | None    = None
        self.heading:      float             = 0.0

    # ------------------------------------------------------------------
    # Primary setup & Topology
    # ------------------------------------------------------------------

    def set_gps_polyline(self, points: list[tuple]):
        if len(points) < 2:
            raise ValueError("Need at least 2 GPS points.")
        self.gps_points  = points
        self._origin_lat = points[0][0]
        self._origin_lon = points[0][1]
        self._is_set = True

    def to_river_topology(self, bend_radius=60.0, merge_window_m=400.0, min_angle_deg=2.0) -> list:
        """Converts raw GPS polyline to (straight/bend) topology."""
        pts = self.gps_points
        mx = np.array([(lon - self._origin_lon) * metres_per_deg_lon(self._origin_lat) for _, lon in pts])
        my = np.array([(lat - self._origin_lat) * METRES_PER_DEG_LAT for lat, _ in pts])

        dx, dy = np.diff(mx), np.diff(my)
        lengths, headings = np.sqrt(dx**2 + dy**2), np.arctan2(dy, dx)

        cum_s = np.zeros(len(pts))
        for i in range(1, len(pts)): cum_s[i] = cum_s[i - 1] + lengths[i - 1]

        raw_bends = []
        for i in range(1, len(pts) - 1):
            delta = (headings[i] - headings[i - 1] + np.pi) % (2 * np.pi) - np.pi
            #we could check for max angle diff to merge but it becomes finicky to draw
            raw_bends.append([cum_s[i], delta])

        if not raw_bends: return [(0, float(cum_s[-1]))]

        merged, group_s, group_angles = [], [raw_bends[0][0]], [raw_bends[0][1]]
        for s, angle in raw_bends[1:]:
            if (s - group_s[0] <= merge_window_m): #and np.sign(angle) == np.sign(group_angles[-1])): , this could be good for certain bends but als finicky
                group_s.append(s)
                group_angles.append(angle)
            else:
                merged.append(_flush_group(group_s, group_angles, bend_radius))
                group_s, group_angles = [s], [angle]
        merged.append(_flush_group(group_s, group_angles, bend_radius))

        topology, cursor_s = [], 0.0
        for s_centre, total_angle_rad, window_len in merged:
            r = max(1.0, window_len / max(abs(total_angle_rad), 1e-6))
            arc_len = r * abs(total_angle_rad)
            straight_len = max(1.0, s_centre - (arc_len / 2.0) - cursor_s)
            topology.append((0, straight_len))
            cursor_s = s_centre + (arc_len / 2.0)
            topology.append((1, (r, np.degrees(total_angle_rad))))

        topology.append((0, max(1.0, float(cum_s[-1]) - cursor_s)))

        return topology

    # ------------------------------------------------------------------
    # New Discretized Tree Engine
    # ------------------------------------------------------------------

    def build_discretized_tree(self, topology: list, ds_length: float):
        self.ds_length = ds_length
        
        # Calculate Initial Real-World Heading from GPS
        lat0, lon0 = self.gps_points[0]
        lat1, lon1 = self.gps_points[1]
        dx = (lon1 - lon0) * metres_per_deg_lon(lat0)
        dy = (lat1 - lat0) * METRES_PER_DEG_LAT
        self.heading = np.arctan2(dy, dx)

        # Build Unrotated Centerline (Matches Simulation Exactly)
        x_cl, y_cl = [0.0], [0.0]
        theta = 0.0  
        
        for seg_type, measures in topology:
            if seg_type == 0:
                n_steps = max(1, int(measures / ds_length))
                for _ in range(n_steps):
                    x_cl.append(x_cl[-1] + ds_length * np.cos(theta))
                    y_cl.append(y_cl[-1] + ds_length * np.sin(theta))
            elif seg_type == 1:
                radius, angle_deg = measures
                n_steps = max(1, int((radius * abs(np.radians(angle_deg))) / ds_length))
                d_theta = np.radians(angle_deg) / n_steps
                for _ in range(n_steps):
                    theta += d_theta
                    x_cl.append(x_cl[-1] + ds_length * np.cos(theta))
                    y_cl.append(y_cl[-1] + ds_length * np.sin(theta))

        self.xc = np.array(x_cl)
        self.yc = np.array(y_cl)

        # Compute normal vectors in Unrotated Space
        dx_arr = np.gradient(self.xc)
        dy_arr = np.gradient(self.yc)
        lengths = np.sqrt(dx_arr**2 + dy_arr**2)
        lengths[lengths == 0] = 1e-9
        self.nx_m = -dy_arr / lengths
        self.ny_m = dx_arr / lengths

        # Build KDTree in UNROTATED Simulation Space
        self.sim_tree = cKDTree(np.column_stack((self.xc, self.yc)))

        #  Pre-calculate GPS coords (Apply Rotation to World Space)
        c, s = np.cos(self.heading), np.sin(self.heading)
        x_rot = self.xc * c - self.yc * s
        y_rot = self.xc * s + self.yc * c

        self.gps_lats = self._origin_lat + y_rot / METRES_PER_DEG_LAT
        self.gps_lons = self._origin_lon + x_rot / metres_per_deg_lon(self._origin_lat)
        
        self._is_built = True

    def gps_to_local(self, lat: float, lon: float) -> tuple[float, float]:
        """Convert GPS back to unrotated simulation Cartesian coordinates (x, y)."""
        if not self._is_built: raise RuntimeError("Tree not built.")

        # GPS to World Flat Space (relative to origin)
        mx = (lon - self._origin_lon) * metres_per_deg_lon(self._origin_lat)
        my = (lat - self._origin_lat) * METRES_PER_DEG_LAT

        # Un-rotate World Flat back to Simulation Space
        c, s = np.cos(-self.heading), np.sin(-self.heading)
        sim_x = mx * c - my * s
        sim_y = mx * s + my * c

        # We return pure x, y. The river_model already uses this exact space!
        return float(sim_x), float(sim_y)

    def sim_cartesian_to_gps(self, buoy_x: float, buoy_y: float) -> tuple[float, float]:
        """Translates a buoy's Cartesian coords directly to GPS."""
        if not self._is_built: raise RuntimeError("Tree not built.")

        # Apply Rigid Body Rotation and translate to the GPS origin
        c, s = np.cos(self.heading), np.sin(self.heading)
        x_rot = buoy_x * c - buoy_y * s
        y_rot = buoy_x * s + buoy_y * c

        lat = self._origin_lat + y_rot / METRES_PER_DEG_LAT
        lon = self._origin_lon + x_rot / metres_per_deg_lon(self._origin_lat)
        
        return float(lat), float(lon)


    def gps_heading_to_sim(self, mag_heading_world_rad) -> float:
        """
        mag_heading_world_rad : arctan2(my, mx) from magnetometer — math convention.
        """
        return float(_wrap_angle(mag_heading_world_rad - self.heading))


    def gps_components_to_sim(self,  north_component : float  ,east_component : float  )-> tuple[float, float]:
        """
        Converts GPS based coorindate system components to local sim system
        """
        #Note :  lon (measure for "southness") -> x_local 
        #     :  lat (measure for "northness") -> y_local 
        x_local_comp = east_component * np.cos(self.heading) + north_component * np.sin(self.heading)
        y_local_comp = - east_component * np.sin(self.heading) + north_component * np.cos(self.heading)
        return float(x_local_comp) ,  float(y_local_comp)



    def river_centerline_as_gps(self):
        coor = []
        for i in range(len(self.gps_lats)):
            coor.append((self.gps_lats[i] , self.gps_lons[i]))        
        return coor

    @property
    def is_set(self):
        return self._is_set


def _wrap_angle(a: float) -> float:
    """Wrap angle to [-π, π]."""
    return (a + np.pi) % (2 * np.pi) - np.pi

def metres_per_deg_lon(lat_deg: float) -> float:
    return METRES_PER_DEG_LAT * np.cos(np.radians(lat_deg))

def _flush_group(group_s, group_angles, fallback_radius):
    s_centre      = (group_s[0] + group_s[-1]) / 2.0
    total_angle   = sum(group_angles)
    window_length = max(1.0, group_s[-1] - group_s[0])
    return (s_centre, total_angle, window_length)
