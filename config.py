# =============================================================================
# config.py — Central configuration for the Buoy Digital Twin
# =============================================================================

# --- ThingsBoard ---
TB_HOST = "https://eu.thingsboard.cloud"
TB_EMAIL = "hendrick.vandnbosse@gmail.com"
TB_PASSWORD = "cpscps"
TB_DEVICE_ID = "cf38e950-160f-11f1-9f60-d181c3f7d20f"


# Telemetry keys the Walter firmware actually publishes (see CPS_Buoy_Walter.ino)
TB_TELEMETRY_KEYS = [
    "temp", "ph", "ec", "do", "counter",
    # gps (firmware sends both nested dict and flat fields; we accept both)
    "gps", "lat", "lon", "speed_n", "speed_s",
    # imu (single sample, sent flat each cycle)
    "imu", "ax", "ay", "gz", "mx", "my",
]

# How often (seconds) to poll ThingsBoard for live data.
# Firmware cycle is ~2 s so 2 s gives us roughly real-time sampling.
POLL_INTERVAL_S = 2

# --- Abstract Meuse segment (preloaded river — no map drawing required) ---
# A short, smoothly curving stretch ~300 m long, abstract representation of
# the Meuse. Coordinates centered near Liège for realism but the geometry is
# stylised — gentle S-curve, fixed width.
MEUSE_CENTER_LAT = 50.6450
MEUSE_CENTER_LON = 5.5750
MEUSE_SEGMENT_LENGTH_M = 300.0      # total stream length (m)
MEUSE_WIDTH_M          = 80.0       # river width (m)

# Smooth abstract topology: short straight → gentle bend → short straight.
# (Used directly by River.__init__; bypasses GPS-polyline → topology conversion.)
MEUSE_TOPOLOGY = [
    (0, 80),                 # 80 m straight
    (1, (180.0, 25.0)),      # gentle right-ish bend, r=180m, 25°
    (0, 80),                 # 80 m straight
    (1, (180.0, -25.0)),     # gentle counter-bend
    (0, 80),                 # 80 m straight
]

# --- River Model Defaults ---
DEFAULT_MERGE_WINDOW_M = 50.0    # m — consecutive bends within this distance are merged
DEFAULT_WIDTH          = MEUSE_WIDTH_M
DEFAULT_DS_L           = 4       # grid spacing along stream (m)
DEFAULT_N_WIDTH        = 30      # grid points across width
DEFAULT_U_AVG          = 1.2     # m/s base velocity (Meuse-typical low flow)
DEFAULT_ALPHA_SEC      = 8       # secondary flow factor

# --- Pollution Transport (physically calibrated) ---
#
# Governing equation (2-D depth-averaged advection-dispersion):
#
#   ∂C/∂t + u ∂C/∂x + v ∂C/∂y = ∂/∂x(D_L ∂C/∂x) + ∂/∂y(D_T ∂C/∂y) + S
#
# Coefficients derived from Fischer et al. (1979) "Mixing in Inland and
# Coastal Waters" — the standard reference for river dispersion.
#
#   Longitudinal:  D_L = 0.011 · U²·W² / (H · u*)
#   Transverse:    D_T = β · H · u*       (β = 0.6 straight, ~1.0 meandering)
#   Shear velocity: u* ≈ √(g · H · S)     (S = energy slope)
#
# Meuse-like reference values (slow Belgian river, mild slope):
#   U  = 1.2  m/s    mean velocity
#   W  = 80   m      width
#   H  = 3.5  m      mean depth
#   S  = 3e-4        energy slope (typical lowland)
#   u* ≈ 0.10 m/s
#   D_L ≈ 0.011 · 1.44 · 6400 / (3.5 · 0.10) ≈ 290 m²/s
#   D_T ≈ 1.0  · 3.5  · 0.10                 ≈ 0.35 m²/s
#   Ratio D_L/D_T ≈ 830  (typical natural-channel value 100–1000)
#
# At these values a continuous source produces a long streamwise streak
# that hugs the bank it was released on, with full lateral mixing only
# reached after several km — matches dye-tracer field studies.

# River hydraulics (used by both DVsolver and the backtrack estimator)
RIVER_DEPTH_M          = 3.5     # mean depth (m)  — Meuse-typical
RIVER_SLOPE            = 3.0e-4  # energy slope (dimensionless)

# Fischer's *fully-mixed* values (reference only — used over km+ distances):
#   FISCHER_D_L_FULL = 290.0   m²/s
#   FISCHER_D_T_FULL = 0.35    m²/s
#
# But Fischer's D_L only applies AFTER the "initial period" — typically a
# distance of L_mix = 0.4·U·W²/(β·H·u*) ≈ 3.7 km for our Meuse parameters.
# Below L_mix the contaminant cloud is still in the shear-dispersion build-up
# regime; the effective D_L is much smaller (only turbulent + molecular
# mixing, not yet enhanced by velocity-profile shear).
#
# For visualisation on a short demo segment (~300 m), the appropriate
# *effective* coefficients (see Rutherford 1994 §6.4, "initial period"):
#   D_L_eff ≈ 1–30 m²/s   (turbulent diffusion, no shear contribution yet)
#   D_T_eff ≈ 0.1–0.5 m²/s
#
# These produce the visually-correct thin-streak plume that hugs the bank
# of release — the canonical "near-field" tracer pattern.
FISCHER_D_L_FULL       = 290.0   # m²/s — Fischer, fully-mixed (reference)
FISCHER_D_T_FULL       = 0.35    # m²/s — Fischer, fully-mixed (reference)

DEFAULT_D_L            = 12.0    # m²/s — effective near-field longitudinal
DEFAULT_D_T            = 2.5     # m²/s — meandering-river transverse (Fischer β≈7 H u*, upper end)
#
# At D_T = 1.2 m²/s, lateral spread at downstream end (300 m at 1.2 m/s
# traversal) is σ_T ≈ √(2·1.2·250) ≈ 24 m, so the plume's full 2σ fan
# reaches about half the river width — visible expanding cone, consistent
# with bend-enhanced lateral mixing observed on the Meuse.

# Continuous source strength.  Units: concentration · m²/s.
# The plume shape is invariant to this scaling; only sensor synthesis
# uses the absolute magnitude. Picked so the SIM sensor synthesizer
# reliably crosses the alarm thresholds when the buoy is in the plume.
DEFAULT_SOURCE_INTENSITY = 200.0

DEFAULT_N_PARTICLES    = 200

# Ignore very faint numerical plume tails when synthesizing SIM sensor values.
# This prevents the buoy from continuing to raise warning detections long after
# it has effectively left the visible/meaningful plume.
SIM_SENSOR_MIN_INTENSITY = 0.03

# --- Estimator (Bayesian profile-likelihood) ---
# Effective observation noise (units: same as the pollution score 0..1).
# Larger sigma => softer posterior (wider credible region).
# Calibrated so a typical buoy detection's residual is roughly sigma-sized.
ESTIMATOR_NOISE_SIGMA = 0.15
# Cap on iterations through the measurement log when extremely large.
ESTIMATOR_MAX_MEASUREMENTS = 2000

# --- Backtrack Defaults (kept for backwards compatibility — unused) ---
BACKTRACK_N_PARTICLES_PER_HIT = 400   # released per logged elevated reading
BACKTRACK_T_SECONDS           = 300   # seconds of backward integration
BACKTRACK_DT                  = 2.0   # integration step (s)
BACKTRACK_GRID_DS             = 5.0   # heatmap grid resolution (m)
BACKTRACK_SEVERITY_WEIGHT     = {"warning": 1.0, "critical": 3.0}
# Backward-particle dispersion. Use the same physically-motivated Fischer
# coefficients as the forward solver — the random walk reverses the
# deterministic flow but the stochastic spread remains physical.
BACKTRACK_D_L                 = DEFAULT_D_L
BACKTRACK_D_T                 = DEFAULT_D_T

# --------- Buoy DT simulation ---------
DEFAULT_D_T_BUOY            = 0.1     # transverse diffusion

# --- Contamination Alarm Rules (matches ThingsBoard rules) ---
# Each rule: (key, predicate, severity) — predicate is callable(value) -> bool
# Severities: "warning" | "critical"
ALARM_RULES = [
    # pH
    ("ph", lambda v: v < 6.0,                "critical"),
    ("ph", lambda v: v > 9.0,                "critical"),
    ("ph", lambda v: 6.0 <= v < 6.5,         "warning"),
    ("ph", lambda v: 8.5 < v <= 9.0,         "warning"),
    # EC (µS/cm)
    ("ec", lambda v: v > 1000.0,             "critical"),
    ("ec", lambda v: 700.0 < v <= 1000.0,    "warning"),
    # DO (mg/L)
    ("do", lambda v: v < 5.0,                "critical"),
    ("do", lambda v: 5.0 <= v < 7.0,         "warning"),
]

# --- Map defaults (center of map on load) ---
MAP_DEFAULT_LAT  = MEUSE_CENTER_LAT
MAP_DEFAULT_LON  = MEUSE_CENTER_LON
MAP_DEFAULT_ZOOM = 17
