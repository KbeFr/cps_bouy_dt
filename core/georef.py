# =============================================================================
# core/georef.py — Georeferencing: local river coords <-> GPS
#                  + GPS polyline -> River topology conversion
# =============================================================================

import numpy as np

METRES_PER_DEG_LAT = 111_320.0

def metres_per_deg_lon(lat_deg: float) -> float:
    return METRES_PER_DEG_LAT * np.cos(np.radians(lat_deg))

def _flush_group(group_s, group_angles, fallback_radius):
    s_centre      = (group_s[0] + group_s[-1]) / 2.0
    total_angle   = sum(group_angles)
    window_length = max(1.0, group_s[-1] - group_s[0])
    return (s_centre, total_angle, window_length)


class GeoReference:
    """
    Two responsibilities:
      1. Convert a user-drawn GPS polyline into a River topology description.
      2. Transform between local river coordinates (metres) and GPS (lat/lon).

    Coordinate conventions
    ----------------------
    Local x = arc-length distance along the river centreline (metres from start)
    Local y = offset perpendicular to the centreline (+ = left bank when looking downstream)

    All normal/tangent vectors are computed in metre-space so they are
    physically correct regardless of the segment's compass heading.
    (Degree-space normals are distorted because 1° lat ≠ 1° lon in metres.)
    """

    def __init__(self):
        self.gps_points:   list[tuple]       = []
        self._cum_s:       np.ndarray | None = None
        self._total_s:     float             = 0.0
        self._origin_lat:  float             = 0.0
        self._origin_lon:  float             = 0.0
        self._is_set:      bool              = False
        self._width_gps:   list[tuple]       = []
        self._width_local: float             = 0.0

    # ------------------------------------------------------------------
    # Primary setup
    # ------------------------------------------------------------------

    def set_gps_polyline(self, points: list[tuple]):
        """Register the GPS centreline polyline (at least 2 points)."""
        if len(points) < 2:
            raise ValueError("Need at least 2 GPS points.")
        self.gps_points  = points
        self._origin_lat = points[0][0]
        self._origin_lon = points[0][1]
        self._build_arc_length()
        self._is_set = True

    def set_gps_width(self, points: list[tuple]):
        """Measure river width from a 2-point line drawn across the river."""
        self._width_gps = points
        p1, p2 = points[0], points[-1]
        d_lon = (p1[1] - p2[1]) * metres_per_deg_lon((p1[0] + p2[0]) / 2)
        d_lat = (p1[0] - p2[0]) * METRES_PER_DEG_LAT
        self._width_local = float(np.round(np.sqrt(d_lon**2 + d_lat**2), 2))

    def get_local_river_width(self) -> float:
        return self._width_local

    @property
    def is_set(self) -> bool:
        return self._is_set

    # ------------------------------------------------------------------
    # Topology conversion
    # ------------------------------------------------------------------

    def to_river_topology(
        self,
        bend_radius:    float = 60.0,
        merge_window_m: float = 400.0,
        min_angle_deg:  float = 2.0,
    ) -> list:
        """
        Convert the GPS polyline into a River topology list, merging
        clusters of small consecutive bend events into single clean arcs.

        Parameters
        ----------
        bend_radius : float
            Fallback radius (m) when a group's arc length is negligible.
        merge_window_m : float
            Max arc-length span (m) within which consecutive bend events
            are considered part of the same physical bend.
        min_angle_deg : float
            Heading changes below this threshold are ignored (noise filter).
        """
        pts = self.gps_points
        n   = len(pts)

        # GPS → flat metres (origin = first point)
        mx = np.array([(lon - self._origin_lon) * metres_per_deg_lon(self._origin_lat)
                       for _, lon in pts])
        my = np.array([(lat - self._origin_lat) * METRES_PER_DEG_LAT
                       for lat, _ in pts])

        dx       = np.diff(mx)
        dy       = np.diff(my)
        lengths  = np.sqrt(dx**2 + dy**2)
        headings = np.arctan2(dy, dx)

        cum_s = np.zeros(n)
        for i in range(1, n):
            cum_s[i] = cum_s[i - 1] + lengths[i - 1]

        # Raw bend events at interior vertices
        raw_bends = []
        for i in range(1, n - 1):
            delta = headings[i] - headings[i - 1]
            delta = (delta + np.pi) % (2 * np.pi) - np.pi
            #if abs(delta) > np.radians(min_angle_deg):
            raw_bends.append([cum_s[i], delta])

        if not raw_bends:
            return [(0, float(cum_s[-1]))]

        # Merge nearby same-sign bends
        merged       = []
        group_s      = [raw_bends[0][0]]
        group_angles = [raw_bends[0][1]]

        for s, angle in raw_bends[1:]:
            if (s - group_s[0] <= merge_window_m and
                    np.sign(angle) == np.sign(group_angles[-1])):
                group_s.append(s)
                group_angles.append(angle)
            else:
                merged.append(_flush_group(group_s, group_angles, bend_radius))
                group_s      = [s]
                group_angles = [angle]
        merged.append(_flush_group(group_s, group_angles, bend_radius))

        # Build topology
        topology = []
        cursor_s = 0.0
        for s_centre, total_angle_rad, window_len in merged:
            r        = max(1.0, window_len / max(abs(total_angle_rad), 1e-6))
            arc_len  = r * abs(total_angle_rad)
            half_arc = arc_len / 2.0

            straight_len = max(1.0, s_centre - half_arc - cursor_s)
            topology.append((0, straight_len))
            cursor_s = s_centre + half_arc
            topology.append((1, (r, np.degrees(total_angle_rad))))

        remaining = max(1.0, float(cum_s[-1]) - cursor_s)
        topology.append((0, remaining))
        return topology

    def total_length_m(self) -> float:
        return float(self._total_s)

    # ------------------------------------------------------------------
    # Coordinate transforms
    # ------------------------------------------------------------------


        
    def sim_cartesian_to_gps(self, buoy_x: float, buoy_y: float, river) -> tuple[float, float]:
        """
        Translates a buoy's flat Cartesian simulation coordinates into real-world GPS.
        """
        if not self.is_set:
            raise RuntimeError("GeoReference polyline must be set first.")

        # 1. Find the nearest centerline point in the simulation's KDTree
        dist, idx_array = river.centerline_tree.query([[buoy_x, buoy_y]], k=1)
        idx = int(idx_array[0])

        # 2. Calculate arc-length (s) down the simulated river
        # Since the river is built in ds_length steps, arc-length is simply index * ds_length
        s = idx * river.ds_length

        # 3. Calculate cross-stream offset (n)
        cx = river.xc[idx]
        cy = river.yc[idx]
        vx = buoy_x - cx
        vy = buoy_y - cy

        # Get local tangent vector of the simulation centerline to determine left/right bank
        if idx < len(river.xc) - 1:
            tx = river.xc[idx + 1] - river.xc[idx]
            ty = river.yc[idx + 1] - river.yc[idx]
        else:
            tx = river.xc[idx] - river.xc[idx - 1]
            ty = river.yc[idx] - river.yc[idx - 1]

        # Create left-pointing normal vector
        length = np.sqrt(tx**2 + ty**2)
        if length < 1e-9:
            nx, ny = 0.0, 0.0
        else:
            nx = -ty / length
            ny = tx / length

        # Signed distance (dot product with normal) to get left/right offset
        n = vx * nx + vy * ny

        # 4. Let your existing function do the heavy lifting!
        # It will correctly map the arc-length and offset to the true GPS headings.
        return self.local_to_gps(float(s), float(n))



    def local_to_gps(self, x: float, y: float) -> tuple[float, float]:
        """
        Local river coords (x along stream, y across) → (lat, lon).
 
        Normal vectors are blended smoothly at segment junctions so the GPS
        position never jumps as the buoy crosses from one polyline segment to
        the next.  Without blending, a cross-stream offset y produces a
        discontinuous position jump at every vertex (clearly visible as
        zigzagging in the GPS track).
 
        Blending strategy
        -----------------
        For each interior vertex we compute an averaged normal from the two
        adjacent segments.  When x lies within one segment, we blend linearly
        between the vertex normal at the start of the segment and the vertex
        normal at the end, weighted by t (position within segment).  This gives
        a C0-continuous normal field along the whole polyline.
        """
        if not self._is_set:
            raise RuntimeError("Call set_gps_polyline() first.")
 
        pts = self.gps_points
        n   = len(pts)
 
        # ------------------------------------------------------------------
        # Build per-segment unit tangents and per-vertex blended normals
        # (done each call — cheap for typical polyline lengths of 5-30 pts)
        # ------------------------------------------------------------------
        def _seg_tangent_m(i):
            """Unit tangent of segment i in metre-space."""
            lat0, lon0 = pts[i]
            lat1, lon1 = pts[i + 1]
            mid_lat    = (lat0 + lat1) / 2
            dx = (lon1 - lon0) * metres_per_deg_lon(mid_lat)
            dy = (lat1 - lat0) * METRES_PER_DEG_LAT
            L  = np.sqrt(dx**2 + dy**2)
            if L < 1e-9:
                return np.array([1.0, 0.0])
            return np.array([dx / L, dy / L])
 
        def _normal_from_tangent(t):
            """90° CCW rotation → left-bank normal."""
            return np.array([-t[1], t[0]])
 
        # Unit normal for every segment
        seg_normals = [_normal_from_tangent(_seg_tangent_m(i)) for i in range(n - 1)]
 
        # Blended normal at every vertex (average of neighbouring segments,
        # normalised).  End vertices use the single adjacent segment's normal.
        def _vertex_normal(v):
            if v == 0:
                return seg_normals[0]
            if v == n - 1:
                return seg_normals[-1]
            avg = seg_normals[v - 1] + seg_normals[v]
            L   = np.linalg.norm(avg)
            return avg / L if L > 1e-9 else seg_normals[v]
 
        # ------------------------------------------------------------------
        # Find which segment x falls on
        # ------------------------------------------------------------------
        x_c = float(np.clip(x, 0, self._total_s))
        idx = int(np.clip(
            np.searchsorted(self._cum_s, x_c, side='right') - 1,
            0, n - 2
        ))
 
        s0, s1 = self._cum_s[idx], self._cum_s[idx + 1]
        t      = (x_c - s0) / max(s1 - s0, 1e-9)   # 0 = start vertex, 1 = end vertex
 
        lat0, lon0 = pts[idx]
        lat1, lon1 = pts[idx + 1]
 
        # Centreline interpolation
        lat_c = lat0 + t * (lat1 - lat0)
        lon_c = lon0 + t * (lon1 - lon0)
 
        # Blend between the vertex normals at each end of this segment
        n0 = _vertex_normal(idx)         # normal at start vertex of segment
        n1 = _vertex_normal(idx + 1)     # normal at end   vertex of segment
        nx_m, ny_m = (1 - t) * n0 + t * n1   # linear blend
 
        # Re-normalise the blend (small magnitude change from linear interp)
        blend_len = np.sqrt(nx_m**2 + ny_m**2)
        if blend_len > 1e-9:
            nx_m /= blend_len
            ny_m /= blend_len
 
        # Apply y-offset in metres, convert back to degrees
        lat = lat_c + (ny_m * y) / METRES_PER_DEG_LAT
        lon = lon_c + (nx_m * y) / metres_per_deg_lon(lat_c)
 
        return float(lat), float(lon)

    def gps_to_local(self, lat: float, lon: float) -> tuple[float, float]:
        """
        GPS (lat, lon) → local river coords (x along stream, y across).

        Finds the nearest point on the GPS polyline by projecting onto each
        segment in metre-space, which gives the arc-length x and signed
        cross-stream distance y.
        """
        if not self._is_set:
            raise RuntimeError("Call set_gps_polyline() first.")

        # Point in flat metres relative to origin
        mx = (lon - self._origin_lon) * metres_per_deg_lon(self._origin_lat)
        my = (lat - self._origin_lat) * METRES_PER_DEG_LAT

        best_x, best_y, best_dist = 0.0, 0.0, np.inf

        for i in range(len(self.gps_points) - 1):
            lat0, lon0 = self.gps_points[i]
            lat1, lon1 = self.gps_points[i + 1]

            ax = (lon0 - self._origin_lon) * metres_per_deg_lon(self._origin_lat)
            ay = (lat0 - self._origin_lat) * METRES_PER_DEG_LAT
            bx = (lon1 - self._origin_lon) * metres_per_deg_lon(self._origin_lat)
            by = (lat1 - self._origin_lat) * METRES_PER_DEG_LAT

            seg_len_m = np.sqrt((bx - ax)**2 + (by - ay)**2)
            if seg_len_m < 1e-9:
                continue

            t  = np.clip(((mx - ax)*(bx - ax) + (my - ay)*(by - ay)) / seg_len_m**2, 0, 1)
            cx = ax + t * (bx - ax)
            cy = ay + t * (by - ay)

            dist = np.sqrt((mx - cx)**2 + (my - cy)**2)
            if dist < best_dist:
                best_dist = dist
                best_x    = self._cum_s[i] + t * seg_len_m
                # Signed y: cross product gives left/right sense
                cross  = (bx - ax) * (my - ay) - (by - ay) * (mx - ax)
                best_y = np.sign(cross) * dist

        return float(best_x), float(best_y)

    def river_centerline_as_gps(self, x_local_array: np.ndarray) -> list[tuple]:
        """Convert centreline x positions to GPS [(lat, lon), ...]."""
        return [self.local_to_gps(float(x), 0.0) for x in x_local_array]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_arc_length(self):
        pts = self.gps_points
        s   = [0.0]
        for i in range(1, len(pts)):
            dlat = (pts[i][0] - pts[i-1][0]) * METRES_PER_DEG_LAT
            dlon = (pts[i][1] - pts[i-1][1]) * metres_per_deg_lon(pts[i-1][0])
            s.append(s[-1] + np.sqrt(dlat**2 + dlon**2))
        self._cum_s   = np.array(s)
        self._total_s = s[-1]