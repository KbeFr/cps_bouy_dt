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
POLL_INTERVAL_S = 10

# --- Abstract Meuse segment (preloaded river — no map drawing required) ---
MEUSE_CENTER_LAT = 50.6450
MEUSE_CENTER_LON = 5.5750
MEUSE_SEGMENT_LENGTH_M = 300.0      # total stream length (m)
MEUSE_WIDTH_M          = 80.0       # river width (m)

# Smooth abstract topology: short straight → gentle bend → short straight.
MEUSE_TOPOLOGY = [
    (0, 80),                 # 80 m straight
    (1, (180.0, 25.0)),      # gentle right-ish bend, r=180m, 25°
    (0, 80),                 # 80 m straight
    (1, (180.0, -25.0)),     # gentle counter-bend
    (0, 80),                 # 80 m straight
]

# --- River Model Defaults ---
DEFAULT_BEND_RADIUS = 60
DEFAULT_MERGE_WINDOW_M = 200    # m — consecutive bends within this distance are merged
DEFAULT_MIN_ANGLE = 0 #at what angle is the drawn line intersection considered a bend

DEFAULT_WIDTH          = MEUSE_WIDTH_M
DEFAULT_N_LENGTH       = 500     # grid spacing along stream
DEFAULT_N_WIDTH        = 30      # grid points across width
DEFAULT_U_AVG          = 1.2     # m/s base velocity (Meuse-typical low flow)
DEFAULT_ALPHA_SEC      = 50       # secondary flow factor

# --- Pollution Transport (physically calibrated) ---
# Coefficients derived from Fischer et al. (1979) "Mixing in Inland and
# Coastal Waters"
DEFAULT_D_L            = 12.0    # m²/s — effective near-field longitudinal
DEFAULT_D_T            = 2.5     # m²/s — meandering-river transverse 

#Dv source intensity
DEFAULT_SOURCE_INTENSITY = 200.0

# Ignore very faint numerical plume tails when synthesizing SIM sensor values.
SIM_SENSOR_MIN_INTENSITY = 0.03

# --- Estimator (Bayesian profile-likelihood) ---
# Effective observation noise (units: same as the pollution score 0..1).
# Larger sigma => softer posterior (wider credible region).
# Calibrated so a typical buoy detection's residual is roughly sigma-sized.
ESTIMATOR_NOISE_SIGMA = 0.15
# Cap on iterations through the measurement log when extremely large.
ESTIMATOR_MAX_MEASUREMENTS = 2000


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
