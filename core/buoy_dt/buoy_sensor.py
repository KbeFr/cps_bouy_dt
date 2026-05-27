from dataclasses import dataclass
from typing import List, Optional


@dataclass
class GPSData:
    lat: float
    lon: float
    vn: float
    ve: float


@dataclass
class IMUData:
    dt: float
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float
    mx: float
    my: float
    mz: float


@dataclass
class BuoySensorData:
    temperature: Optional[float] = None
    counter: Optional[int] = None
    ph: Optional[float] = None
    ec: Optional[float] = None
    do: Optional[float] = None
    gps: Optional[GPSData] = None
    imu: Optional[List[IMUData]] = None

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


class BuoySensor:
    def __init__(self):
        # --- Buoy sensor data state ---
        self.data: BuoySensorData = BuoySensorData()

    def update_sensor(self, data: dict):
        """
        Parses incoming raw dictionary data from ThingsBoard polling
        and safely updates the internal dataclass.
        """

        # --- Temperature ---
        if "temp" in data:
            self.data.temperature = data.get("temp")

        # --- Counter ---
        if "counter" in data:
            self.data.counter = int(data.get("counter"))

        # --- Water Quality Sensors ---
        if "ph" in data:
            self.data.ph = data.get("ph")

        if "ec" in data:
            self.data.ec = data.get("ec")

        if "do" in data:
            self.data.do = data.get("do")

        # --- GPS ---
        if "gps" in data and isinstance(data["gps"], dict):
            gps_data = data.get("gps", {})
            self.data.gps = GPSData(
                lat=float(gps_data.get("lat", 0.0)),
                lon= float(gps_data.get("lon", 0.0)),
                vn= float(gps_data.get("speed_n", 0.0)),
                ve= float(gps_data.get("speed_e", 0.0))
            )

        # --- IMU Batch ---
        if "imu" in data and isinstance(data["imu"], list):
            imu_batch_raw = data.get("imu", [])
            parsed_imu_batch = []

            for imu_raw in imu_batch_raw:
                # Catch instances where an individual reading might be malformed
                if not isinstance(imu_raw, dict):
                    continue

                parsed_imu_batch.append(
                    IMUData(
                        dt= float(imu_raw.get("dt", 0.0)),
                        ax= float(imu_raw.get("ax", 0.0)),
                        ay= float(imu_raw.get("ay", 0.0)),
                        az= float(imu_raw.get("az", 0.0)),
                        gx= float(imu_raw.get("gx", 0.0)),
                        gy= float(imu_raw.get("gy", 0.0)),
                        gz= float(imu_raw.get("gz", 0.0)),
                        mx= float(imu_raw.get("mx", 0.0)),
                        my= float(imu_raw.get("my", 0.0)),
                        mz= float(imu_raw.get("mz", 0.0))
                    )
                )

            # Only overwrite the existing IMU data if the new batch actually contains data
            if parsed_imu_batch:
                self.data.imu = parsed_imu_batch
