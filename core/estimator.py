import numpy as np
import config



class Estimator():
    def __init__(self , river , georef):
        self.river = river
        self.georef = georef 


    def estimate(self, measurement_log: list[dict], true_source_local: tuple[float, float] | None = None) -> tuple[np.ndarray, tuple[float, float]] | None:
        """
        Cite:
        Andrew Keats, Eugene Yee, Fue-Sang Lien,
        Bayesian inference for source determination with applications to a complex urban environment,
        Atmospheric Environment,
        Volume 41, Issue 3,
        2007,
        Pages 465-479,
        ISSN 1352-2310,
        https://doi.org/10.1016/j.atmosenv.2006.08.044.
        (https://www.sciencedirect.com/science/article/pii/S1352231006008703)

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
        if self.river is None or not measurement_log:
            print("[Estimator] No data to estimate from.")
            return None

        r = self.river
        n_stream, n_width = r.vis_v.shape
        ds = r.ds_length
        dn = r.dn
        U  = max(0.1, float(config.DEFAULT_U_AVG))
        D_T = max(1e-3, float(config.DEFAULT_D_T))
        sigma2 = float(getattr(config, "ESTIMATOR_NOISE_SIGMA", 0.15)) ** 2

        # -----  Gather georeferenced measurements -----
        valid_measurements = [
            m for m in measurement_log
            if m["x_local"] is not None and m["y_local"] is not None
        ]
        # Cap to most-recent N if log is very long (keeps newest evidence)
        max_meas = int(getattr(config, "ESTIMATOR_MAX_MEASUREMENTS", 2000))
        if len(valid_measurements) > max_meas:
            valid_measurements = valid_measurements[-max_meas:]
        if not valid_measurements:
            print("[Estimator] No georeferenced measurements.")
            return None

        pts = np.array([[m["x_local"], m["y_local"]] for m in valid_measurements])
        _, b_flat = r.physics_tree.query(pts, k=1)
        b_i_all = (b_flat // n_width).astype(int)
        b_j_all = (b_flat %  n_width).astype(int)

        # -----  Build observation vector c_obs (length M) -----
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
            score = m.get("pollution_score" , 0.0)
            return float(score) ** (1.0 / 0.35)   # undo sqrt-style compression

        c_obs = np.array([_obs_for_estimator(m) for m in valid_measurements],
                         dtype=np.float64)

        # -----  For each candidate cell s, build "shape function" g_k(s) =
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

        # -----  Profile-MLE for source strength Q at every candidate -----
        # eps avoids divide-by-zerso for cells that no measurement could see.
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

        # -----  Convert to probability (softmax with max-subtraction) -----
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
            return None
        prob /= total

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

        if true_source_local is not None:
            tx, ty = true_source_local
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
            
        return prob, (est_x, est_y)
    
    
    # ------------------------------------------------------------------
    # Pollution score 
    # ------------------------------------------------------------------
    @staticmethod
    def pollution_score(ph: float | None, ec: float | None, do: float | None) -> float:
        """
        Cite : 
        Stevens, S. S. (1957). On the psychophysical law. Psychological Review, 64(3), 153–181. https://doi.org/10.1037/h0046162

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
