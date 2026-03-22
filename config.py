# =============================================================================
# config.py — Central configuration for the Buoy Digital Twin
# =============================================================================

# --- ThingsBoard ---
TB_HOST = "https://eu.thingsboard.cloud"
TB_EMAIL = "hendrick.vandnbosse@gmail.com"
TB_PASSWORD = "cpscps"
TB_DEVICE_ID = "cf38e950-160f-11f1-9f60-d181c3f7d20f"  


# Telemetry keys to fetch from ThingsBoard
TB_TELEMETRY_KEYS = ["temperature", "latitude", "longitude", "counter"]

# How often (seconds) to poll ThingsBoard for live data
POLL_INTERVAL_S = 60

# --- River Model Defaults ---
# These match what you already have in riverbend_Further.py
DEFAULT_TOPOLOGY = [
    (0,  250),
    (1, (80,  100)),
    (0,  120),
    (1, (80, -70)),
    (0,  200),
]

DEFAULT_MERGE_WINDOW_M = 600.0  # metres — consecutive bends within this distance are merged
DEFAULT_MIN_ANGLE = 3 #at what angle is the drawn line intersection considered a bend

DEFAULT_WIDTH          = 80      # metres
DEFAULT_DS_L           = 5     # grid spacing along stream
DEFAULT_N_WIDTH        = 25      # grid points across width
DEFAULT_U_AVG          = 1.5     # m/s base velocity
DEFAULT_ALPHA_SEC      = 10      # secondary flow factor

# --- Pollution Simulation Defaults ---
DEFAULT_D_L            = 5.0     # longitudinal diffusion
DEFAULT_D_T            = 1.0     # transverse diffusion
DEFAULT_DIFFUSIVITY    = 0.5
DEFAULT_SOURCE_COORDS  = [5, 0]
DEFAULT_SOURCE_INTENSITY = 10
DEFAULT_N_PARTICLES    = 200

# --- Contamination Detection ---
CONTAMINATION_THRESHOLD = 1  

# --- Map defaults (center of map on load) ---

#Its antwerp ATM but needs to be changed to valid meuse location

MAP_DEFAULT_LAT  = 51.22       
MAP_DEFAULT_LON  = 4.40
MAP_DEFAULT_ZOOM = 14
