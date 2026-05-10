# =============================================================================
# core/river_model.py — River physics model
# =============================================================================
#
# Classes
#   River               — builds the 2D curvilinear river grid and flow field
#   LagrangianParticles — stochastic particle-tracking (advection + diffusion)
#   DVsolver            — implicit advection-diffusion PDE solver (FVM)

import numpy as np
from scipy.spatial import cKDTree
from scipy.ndimage import gaussian_filter1d
from discretize import TensorMesh
from scipy.sparse.linalg import splu
from discretize.utils import sdiag, mkvc


# =============================================================================
# River
# =============================================================================

class River:
    """
    Builds a 2D curvilinear river grid from a topology description and
    computes the steady-state flow field at every grid cell.

    Parameters
    ----------
    topology : list of (int, info)
        Sequence of river segments:
            (0, length)          — straight section, length in metres
            (1, (radius, angle)) — bend, radius in metres, angle in degrees
                                   positive angle = left turn, negative = right turn
    width : float
        River width in metres.
    ds_length : float
        Grid spacing along the stream direction in metres.
    n_width : int
        Number of grid points across the river width.
    u_avg : float
        Depth-averaged flow speed in straight sections (m/s).
    alpha_secondary : float
        Scaling factor for the cross-stream (secondary) flow in bends.

    Attributes (read-only after construction)
    -----------------------------------------
    xc, yc : ndarray (N,)
        Cartesian coordinates of the river centreline.
    curv : ndarray (N,)
        Local curvature at each centreline point (1/m).
    vis_x, vis_y : ndarray (N, n_width)
        Cartesian coordinates of every grid cell.
    vis_v : ndarray (N, n_width)
        Streamwise velocity magnitude at every grid cell (m/s).
    vis_vn : ndarray (N, n_width)
        Cross-stream (secondary) velocity magnitude at every grid cell (m/s).
    grid_data : ndarray (N*n_width, 2)
        Flat array of [speed, direction_angle] for every grid cell.
        Used by the KD-tree for fast nearest-cell lookup.
    physics_tree : cKDTree
        Spatial index over all grid cell centres.
    centerline_tree : cKDTree
        Spatial index over the centreline points (used for boundary enforcement).
    ds_length : float
        Grid spacing (stored for use by DVsolver).
    dn : float
        Grid spacing across the width (derived from n_width).
    """

    def __init__(
        self,
        topology: list,
        width: float,
        ds_length : float,
        n_width: int,
        u_avg: float,
        alpha_secondary: float
    ):
        self.width      = width
        self.half_width = width / 2.0
        self.ds_length  = ds_length 
        self.n_width    = n_width

        self.u_avg = u_avg
        # ------------------------------------------------------------------
        # Build centreline
        # ------------------------------------------------------------------
        x_cl, y_cl = [0.0], [0.0]
        theta = 0.0     # current heading angle (radians)
        curv  = []      # curvature at each step

    
        for seg_type, measures in topology:

            if seg_type == 0:
                # Straight section
                length = measures
                n_steps = max(1, int(length / ds_length))
                for _ in range(n_steps):
                    x_cl.append(x_cl[-1] + ds_length * np.cos(theta))
                    y_cl.append(y_cl[-1] + ds_length * np.sin(theta))
                    curv.append(0.0)

            elif seg_type == 1:
                # Curved bend
                radius, angle_deg = measures
                arc_length = radius * abs(np.radians(angle_deg))
                n_steps    = max(1, int(arc_length / ds_length))
                d_theta    = np.radians(angle_deg) / n_steps
                C_step     = d_theta / ds_length   # curvature = dθ/ds

                for _ in range(n_steps):
                    theta += d_theta
                    x_cl.append(x_cl[-1] + ds_length * np.cos(theta))
                    y_cl.append(y_cl[-1] + ds_length * np.sin(theta))
                    curv.append(C_step)

            else:
                raise ValueError(f"Unknown segment type '{seg_type}'. Use 0 (straight) or 1 (bend).")

        self.xc   = np.array(x_cl)
        self.yc   = np.array(y_cl)
        self.curv = np.array(curv)

        # KD-tree on centreline for boundary enforcement
        self.centerline_tree = cKDTree(np.column_stack((self.xc, self.yc)))

        # ------------------------------------------------------------------
        # Build 2D grid and flow field
        # ------------------------------------------------------------------
        N = len(self.xc)

        # Local tangent angle at every centreline point
        tangents = np.arctan2(np.gradient(self.yc), np.gradient(self.xc))

        # Cross-section sample points (symmetric about centreline)
        n_vals, self.dn = np.linspace(-width / 2, width / 2, n_width, retstep=True)

        # Initialise visualisation arrays
        self.vis_x  = np.zeros((N, n_width))
        self.vis_y  = np.zeros((N, n_width))
        self.vis_v  = np.zeros((N, n_width))
        self.vis_vn = np.zeros((N, n_width))


        # Curvature with phase lag: velocity asymmetry in a bend peaks slightly
        # downstream of the bend apex (helical flow takes time to develop).
        # The lag is scaled to the dominant bend radius in the river so it
        # stays physically meaningful regardless of ds_length or river scale.
        #
        # Typical field value: lag ≈ 0.5–1.0 × bend_radius
        # We estimate the bend radius from the maximum curvature: R = 1/|C_max|
        # and express the lag as a fraction of that.
        c_max = np.max(np.abs(self.curv)) if np.any(self.curv != 0) else 1e-6
        r_dominant    = 1.0 / max(c_max, 1e-6)          # dominant bend radius (m)
        lag_metres    = 0.3 * r_dominant                 # lag = 30% of bend radius
        lag_steps     = max(1, int(lag_metres / ds_length))
        smooth_sigma  = max(1.0, lag_steps * 0.3)        # smooth over ~30% of lag
 
        lagged_curv = gaussian_filter1d(np.roll(self.curv, lag_steps), sigma=smooth_sigma)

        grid_coords = []
        grid_data   = []

        for i in range(N):
            angle = tangents[i]

            # Unit tangent (streamwise) and normal (cross-stream) vectors
            tx, ty = np.cos(angle),  np.sin(angle)
            nx, ny = -np.sin(angle), np.cos(angle)

            # Negate so that positive curvature = left bend → outer bank on left
            C_local = -lagged_curv[i - 1]

            for j, n in enumerate(n_vals):
                # Grid cell centre coordinates
                px = self.xc[i] + nx * n
                py = self.yc[i] + ny * n

                # Streamwise speed: faster on outer bank in a bend
                perturbation = 0.7 * C_local * n
                u_stream = max(0.0, u_avg * (1.0 + perturbation))

                # Secondary (cross-stream) flow driven by centrifugal acceleration
                u_sec = alpha_secondary * C_local * u_stream

                # Combine into a single velocity vector, then extract magnitude + angle
                vx = u_stream * tx + u_sec * nx
                vy = u_stream * ty + u_sec * ny

                speed = np.sqrt(vx**2 + vy**2)
                angle_out = np.arctan2(vy, vx)

                grid_coords.append([px, py])
                grid_data.append([speed, angle_out])

                self.vis_x[i, j]  = px
                self.vis_y[i, j]  = py
                self.vis_v[i, j]  = u_stream
                self.vis_vn[i, j] = u_sec

        self.grid_data   = np.array(grid_data)
        self.physics_tree = cKDTree(grid_coords)


# =============================================================================
# DVsolver
# =============================================================================

class DVsolver:
    """
    Steady-state advection-diffusion solver for contamination plume display.

        ∇·(u c) - ∇·(D ∇c) = q

    Solves directly for the time-independent concentration field — no
    time-stepping needed.  This gives a permanent plume shape that stays
    anchored to the source and never drifts away.

    Boundary conditions
    -------------------
    Upstream (x=0)  : Neumann (no-flux) — contamination cannot re-enter
    Downstream (x=L): Dirichlet c=0    — concentration exits freely
    Banks (y=±W/2)  : Neumann (no-flux) — impermeable banks

    With a zero-concentration outlet, the steady-state solution exists and
    is unique.  The plume extends downstream from the source and decays to
    zero at the outlet — exactly what you want to visualise.

    Parameters
    ----------
    river : River
    source_coords : [x, y]      Local coords of the pollution source.
    source_intensity : float    Source strength (concentration·m²/s).
    diffusion : float            Isotropic diffusion coefficient D (m²/s).
    step : float                 Unused — kept for API compatibility.
    """

    def __init__(
        self,
        river: River,
        source_coords: list,
        source_intensity: float,
        diffusion: float,
        step: float,
        is_adjoint : bool           
    ):
        self.river = river

        n_stream = len(river.vis_x)
        n_cross  = len(river.vis_x[0])

        hx = np.ones(n_stream) * river.ds_length
        hy = np.ones(n_cross)  * river.dn
        self.mesh = TensorMesh([hx, hy], x0='0C')

        # ------------------------------------------------------------------
        # Face-normal velocity field
        # ------------------------------------------------------------------
        
        #convert to face vector
        direction = -1.0 if is_adjoint else 1.0

        vx_cc = (river.vis_v * direction).flatten(order='F')
        vy_cc = (river.vis_vn * direction).flatten(order='F')        
        # Get the averaging matrix
        Av = self.mesh.average_cell_to_face
        
        # A. Map X-velocity to all faces, but keep only the X-faces (first nFx)
        # This gives us u_x on the vertical interfaces
        ux_on_all_faces = Av @ vx_cc
        ux_faces = ux_on_all_faces[:self.mesh.nFx]
        
        # B. Map Y-velocity to all faces, but keep only the Y-faces (last nFy)
        # This gives us u_y on the horizontal interfaces
        uy_on_all_faces = Av @ vy_cc
        uy_faces = uy_on_all_faces[self.mesh.nFx:]
        
        # C. Combine them into the final face-normal velocity vector
        self.u = np.r_[ux_faces, uy_faces]
        
        # ------------------------------------------------------------------
        # Point source
        # ------------------------------------------------------------------
        
        #  Find the nearest cell in the river's Cartesian physics tree
        _, idx_c = self.river.physics_tree.query([source_coords], k=1)
        idx_c = int(idx_c[0])

        # Map the C-order flat index (from tree) to Fortran-order  (for TensorMesh)
        # Row-Major to Column-Major !!

        # i is the streamwise index, j is the cross-stream index
        i = idx_c // n_cross
        j = idx_c % n_cross

        # TensorMesh flattens using Fortran order (streamwise varies fastest)
        k = j * n_stream + i

        #Make the point source 1 in array
        q = np.zeros(self.mesh.nC)
        q[k] = source_intensity

        # ------------------------------------------------------------------
        # Steady-state operator  M·p = s
        # "neumann" = free (natural), "dirichlet" = zero value.
        # ------------------------------------------------------------------
        
        # Define diffusivity within each cell
        a = mkvc(diffusion * np.ones(self.mesh.nC))


        # Define the matrix M
        Afc = self.mesh.dim * self.mesh.aveF2CC  # modified averaging operator to sum dot product
        Mf_inv = self.mesh.get_face_inner_product(invert_matrix=True)
        Mc = sdiag(self.mesh.cell_volumes)
        Mc_inv = sdiag(1 / self.mesh.cell_volumes)
        Mf_alpha_inv = self.mesh.get_face_inner_product(a, invert_model=True, invert_matrix=True)


        self.mesh.set_cell_gradient_BC(["neumann", "neumann"])  # Set Neumann BC
        G = self.mesh.cell_gradient
        D = self.mesh.face_divergence


        # Steady-state advection-diffusion operator
        M = -D * Mf_alpha_inv * G * Mc + Afc * sdiag(self.u) * Mf_inv * G * Mc
        
        
        self.p = np.zeros(self.mesh.nC)  # Initial conditions p(t=0)=0

        I = sdiag(np.ones(self.mesh.nC))  # Identity matrix
        B = I + step * M
        self.s = Mc_inv * q

        self.Binv = splu(B)


    def update(self) -> np.ndarray:
        self.p = self.Binv.solve(self.p + self.s)

        # I would think that p is a 1D array with every value of the DV for every cell of the river in it. 
        # How the cells are indext i think is first for the first x-axis all the y-axis values are defined then we traverse like this
        # so if we want to "bend" the shape back into the river we would need to compact the [L*W] matrix to [L,W] indexxed matrix, then we can use pcolormesh like we did before

        # Reshape the solution column first and return
        return self.p.reshape((self.mesh.shape_cells[0], self.mesh.shape_cells[1]), order='F') 

    """ BROKEN
    def move_source(self, source_coords: list):
        
        ##Re-solve with a new source location without rebuilding the operator.
        ##Useful for interactive contamination injection.
        
        xy_cc   = self.mesh.gridCC
        dist_sq = ((xy_cc[:, 0] - source_coords[0])**2 +
                   (xy_cc[:, 1] - source_coords[1])**2)
        k       = np.argmin(dist_sq)
        q       = np.zeros(self.mesh.nC)
        q[k]    = self.s.max() * self.mesh.cell_volumes[k]  # preserve intensity
        Mc_inv  = sdiag(1.0 / self.mesh.cell_volumes)
        self.s  = Mc_inv * q
        self.p  = np.maximum(self.M_lu.solve(self.s), 0.0)
    """
    def get_concentration_map(self) -> np.ndarray:
        """Return the steady-state concentration field as (N_stream, N_width)."""
        return self.p.reshape((self.mesh.shape_cells[0], self.mesh.shape_cells[1]), order='F') 

    



# =============================================================================
# LagrangianParticles
# =============================================================================

class LagrangianParticles:
    """
    Stochastic Lagrangian particle tracker for contaminant plume simulation.

    Particles are advected by the local flow field and dispersed by
    random-walk diffusion in both the streamwise and cross-stream directions.

    Parameters
    ----------
    river : River
        The river model providing the flow field.
    num_particles : int
        Number of tracer particles to release.
    x0, y0 : float
        Release coordinates (metres, local river frame).
    D_L : float
        Longitudinal (streamwise) diffusion coefficient (m²/s).
    D_T : float
        Transverse (cross-stream) diffusion coefficient (m²/s).
    """

    def __init__(
        self,
        river: River,
        num_particles: int,
        x0: float,
        y0: float,
        D_L: float,
        D_T: float,
    ):
        self.river = river
        self.num   = num_particles
        self.D_L   = D_L
        self.D_T   = D_T

        # Scatter the initial release around (x0, y0) with a small Gaussian spread
        self.x = np.full(num_particles, x0) + np.random.normal(0, 0.5, num_particles)
        self.y = np.full(num_particles, y0) + np.random.normal(0, 0.5, num_particles)

    # ------------------------------------------------------------------

    def update(self, dt: float):
        """
        Advance all particles by one time step dt (seconds).

        Steps:
          1. Look up the nearest grid cell velocity for each particle.
          2. Advect along the streamwise direction.
          3. Add Gaussian random-walk noise in both L and T directions.
          4. Rotate displacement from (L, T) frame back to (x, y).
          5. Enforce channel boundaries (elastic clamp).
        """
        points = np.column_stack((self.x, self.y))

        # Nearest grid cell → local speed and flow angle
        _, indices = self.river.physics_tree.query(points, k=1)
        cell_data   = self.river.grid_data[indices]
        U_local     = cell_data[:, 0]
        theta_local = cell_data[:, 1]

        # Streamwise advection + longitudinal diffusion
        dL = U_local * dt + np.random.normal(0, 1, self.num) * np.sqrt(2 * self.D_L * dt)

        # Transverse diffusion only (no mean cross-stream flow for particles)
        dT = np.random.normal(0, 1, self.num) * np.sqrt(2 * self.D_T * dt)

        # Rotate from local (L, T) frame to global (x, y) frame
        c = np.cos(theta_local)
        s = np.sin(theta_local)
        self.x += dL * c - dT * s
        self.y += dL * s + dT * c

        self._enforce_boundaries()

    # ------------------------------------------------------------------

    def _enforce_boundaries(self):
        """
        Clamp particles that have escaped outside the channel banks.

        Any particle further than 95% of the half-width from the nearest
        centreline point is pushed back to that limit along the radial direction.
        """
        points = np.column_stack((self.x, self.y))
        dists, indices = self.river.centerline_tree.query(points, k=1)

        limit = self.river.half_width * 0.95
        oob   = dists > limit

        if not np.any(oob):
            return

        cx = self.river.xc[indices[oob]]
        cy = self.river.yc[indices[oob]]
        px = self.x[oob]
        py = self.y[oob]

        # Radial vector from centreline point to particle
        vx = px - cx
        vy = py - cy
        norm = np.sqrt(vx**2 + vy**2)
        norm[norm == 0] = 1.0   # guard against zero-length vector

        # Push back to the boundary
        self.x[oob] = cx + (vx / norm) * limit
        self.y[oob] = cy + (vy / norm) * limit


