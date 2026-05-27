# =============================================================================
# config.py — Central configuration 
# =============================================================================

# --- ThingsBoard ---
TB_HOST = "https://eu.thingsboard.cloud"
TB_EMAIL = "hendrick.vandnbosse@gmail.com"
TB_PASSWORD = "cpscps"
TB_DEVICE_ID = "cf38e950-160f-11f1-9f60-d181c3f7d20f"  


# Telemetry keys to fetch from ThingsBoard
TB_TELEMETRY_KEYS = ["temp", "gps", "imu", "counter" , "ph", "ec", "do"]

# How often (seconds) to poll ThingsBoard for live data
POLL_INTERVAL_S = 60

# --- River Model Defaults ---

DEFAULT_MERGE_WINDOW_M = 500.0  # metres — consecutive bends within this distance are merged
DEFAULT_MIN_ANGLE = 0 #at what angle is the drawn line intersection considered a bend

DEFAULT_WIDTH          = 80      # metres
DEFAULT_N_LENGTH       = 500     # grid spacing along stream
DEFAULT_N_WIDTH        = 50      # grid points across width
DEFAULT_U_AVG          = 1.5     # m/s base velocity
DEFAULT_ALPHA_SEC      = 100      # secondary flow factor

# --- Forward DV Defaults ---
DEFAULT_DV_STEP_FORWARD  = 25   
DEFAULT_DIFFUSIVITY    = 10
DEFAULT_SOURCE_COORDS  = [5, 0]
DEFAULT_SOURCE_INTENSITY = 100

# --- Backtrack Simulation Defaults --- # should depend on cont level measured ig
DEFAULT_DV_STEP_ADJOINT  = 40 
DEFAULT_DIFFUSIVITY_BACK    = 20
DEFAULT_SOURCE_INTENSITY_BACK = 100

SOURCE_INTENSITY_BACK_WARNING = 50
SOURCE_INTENSITY_BACK_CRITICAL = 100



# --------- Buoy DT simulation ---------
DEFAULT_D_T_BUOY            = 0.07     # transverse diffusion




# --- Contamination Detection ---
CONTAMINATION_THRESHOLD = 1  

# --- Map defaults (center of map on load) ---

#Its antwerp ATM but needs to be changed to valid meuse location

MAP_DEFAULT_LAT  = 51.22       
MAP_DEFAULT_LON  = 4.40
MAP_DEFAULT_ZOOM = 14
