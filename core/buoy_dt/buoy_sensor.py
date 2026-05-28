from dataclasses import dataclass, field
from typing import List, Optional, Tuple , Callable
import time
import json
import numpy as np
import config

@dataclass
class GPSData:
    lat: float
    lon: float
    vn: float        # speed north (m/s)
    vs: float        # speed east  (m/s)  — firmware field is `speed_s`


@dataclass
class IMUData:
    dt: float        # seconds since previous sample (derived, since firmware does not send it)
    ax: float = 0.0
    ay: float = 0.0
    az: float = 0.0
    gx: float = 0.0
    gy: float = 0.0
    gz: float = 0.0
    mx: float = 0.0
    my: float = 0.0
    mz: float = 0.0


@dataclass
class BuoySensorData:
    temperature: Optional[float] = None
    ph: Optional[float] = None
    ec: Optional[float] = None
    do: Optional[float] = None
    gps: Optional[GPSData] = None
    imu: Optional[List[IMUData]] = field(default_factory=list)
    counter: Optional[int] = None
    last_imu_ts: float = 0.0

    # --- Formatting Properties for the UI ---

    @property
    def formatted_temp(self) -> str:
        return f"{self.temperature:.2f}°C" if self.temperature is not None else "--"

    @property
    def formatted_lat(self) -> str:
        return f"{self.gps.lat:.6f}" if self.gps and self.gps.lat is not None else "--"

    @property
    def formatted_lon(self) -> str:
        return f"{self.gps.lon:.6f}" if self.gps and self.gps.lon is not None else "--"

    @property
    def formatted_ph(self) -> str:
        return f"{self.ph:.2f}" if self.ph is not None else "--"

    @property
    def formatted_ec(self) -> str:
        return f"{self.ec:.2f}" if self.ec is not None else "--"

    @property
    def formatted_do(self) -> str:
        return f"{self.do:.2f}" if self.do is not None else "--"


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_dict(v) -> Optional[dict]:
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


class BuoySensorReal:
    """
    Parses the flat telemetry dict produced by BuoyComm.get_latest() into a
    typed BuoySensorData snapshot.

    The Walter firmware publishes telemetry across several MQTT messages:
      Pub 1   : {temp, ph, ec, do, counter, gps:{lat,lon,speed_n,speed_s}, timestamp}
      Pub 1b  : flat {lat, lon, speed_n, speed_s}
      Pub 2   : {imu:{ax, ay, gz, mx, my, timestamp}}
      Pub 2b  : flat {ax, ay, gz, mx, my}

    ThingsBoard exposes each key with its most-recent value via values/timeseries,
    so we read whichever form is present (flat preferred, nested as fallback).
    """

    def __init__(self):
        self.data: BuoySensorData = BuoySensorData()

    def update_sensor(self, data: dict):
        if not data:
            return

        # --- Water quality ---
        temp = _to_float(data.get("temp"))
        if temp is not None:
            self.data.temperature = temp

        ph = _to_float(data.get("ph"))
        if ph is not None:
            self.data.ph = ph

        ec = _to_float(data.get("ec"))
        if ec is not None:
            self.data.ec = ec

        do = _to_float(data.get("do"))
        if do is not None:
            self.data.do = do

        counter = _to_float(data.get("counter"))
        if counter is not None:
            self.data.counter = int(counter)

        # --- GPS ---
        # Prefer flat fields (latest single-value snapshot); fall back to nested dict.
        lat = _to_float(data.get("lat"))
        lon = _to_float(data.get("lon"))
        sn  = _to_float(data.get("speed_n"))
        ss  = _to_float(data.get("speed_s"))

        gps_dict = _to_dict(data.get("gps"))
        if (lat is None or lon is None) and gps_dict is not None:
            g = gps_dict
            lat = _to_float(g.get("lat")) if lat is None else lat
            lon = _to_float(g.get("lon")) if lon is None else lon
            sn  = _to_float(g.get("speed_n")) if sn is None else sn
            ss  = _to_float(g.get("speed_s")) if ss is None else ss

        if lat is not None and lon is not None:
            self.data.gps = GPSData(
                lat=lat, lon=lon,
                vn=sn if sn is not None else 0.0,
                vs=ss if ss is not None else 0.0,
            )

        # --- IMU (firmware sends one sample per cycle, flat-keyed) ---
        ax = _to_float(data.get("ax"))
        ay = _to_float(data.get("ay"))
        gz = _to_float(data.get("gz"))
        mx = _to_float(data.get("mx"))
        my = _to_float(data.get("my"))

        # Nested fallback
        imu_dict = _to_dict(data.get("imu"))
        if all(v is None for v in (ax, ay, gz, mx, my)) and imu_dict is not None:
            imu_d = imu_dict
            ax = _to_float(imu_d.get("ax"))
            ay = _to_float(imu_d.get("ay"))
            gz = _to_float(imu_d.get("gz"))
            mx = _to_float(imu_d.get("mx"))
            my = _to_float(imu_d.get("my"))

        if any(v is not None for v in (ax, ay, gz, mx, my)):
            now = time.time()
            dt  = (now - self.data.last_imu_ts) if self.data.last_imu_ts > 0 else 0.0
            self.data.last_imu_ts = now
            sample = IMUData(
                dt=dt,
                ax=ax or 0.0, ay=ay or 0.0,
                gz=gz or 0.0,
                mx=mx or 0.0, my=my or 0.0,
            )
            # Append as a 1-element batch so downstream EKF code can iterate
            self.data.imu = [sample]


class BuoySensorSim:
    def __init__(self):
        self.data: BuoySensorData = BuoySensorData()
        self.sim_sensor_callback: Optional[Callable[[float, float], Tuple[float, float]]] = None 

    def update_sensor(self, local_x: float, local_y: float) -> BuoySensorData:
        """
        SIM mode: synthesize realistic pH/EC/DO sensor readings from the
        local pollution concentration.
        """
        # Safety check
        if self.sim_sensor_callback is None:
            return self.data
            
        # use the callback
        c, cmax = self.sim_sensor_callback(local_x, local_y)

        # Calculate intensity
        # Prevent division by zero if cmax happens to be 0
        safe_cmax = cmax if cmax > 0 else 1.0 
        raw_intensity = max(0.0, min(1.0, c / safe_cmax))
        
        if raw_intensity < getattr(config, "SIM_SENSOR_MIN_INTENSITY", 0.0):
            raw_intensity = 0.0
            
        # Non-linear sensor dose-response
        eff = float(raw_intensity ** 0.35) if raw_intensity > 0 else 0.0

        # Apply environmental baseline and sensor noise
        rng = np.random.default_rng()
        self.data.ph          = 7.5 + 2.5 * eff + rng.normal(0.0, 0.05)
        self.data.ec          = 400.0 + 900.0 * eff + rng.normal(0.0, 12.0)
        self.data.do          = 9.0 - 5.5 * eff + rng.normal(0.0, 0.10)
        self.data.temperature = 15.0 + 0.4 * eff + rng.normal(0.0, 0.20)
        
        # Stash for the buoy's own records
        self._last_concentration = c
        self._last_intensity = raw_intensity
        
        return self.data
    
