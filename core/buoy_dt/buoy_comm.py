# =============================================================================
# core/buoy_comm.py — ThingsBoard data connection for the Buoy Digital Twin
# =============================================================================

import requests
import time
import threading
from typing import Callable
import config
from dataclasses import dataclass



class BuoyComm:
    """
    Handles all communication with ThingsBoard for the buoy device.

    Features:
      - JWT token management (auto-refresh)
      - Latest telemetry polling
      - Historical telemetry fetch
      - RPC command dispatch (for future motor control)
      - Background polling thread with callback
    """

    TOKEN_REFRESH_INTERVAL = 3600   # seconds — TB JWT expires after ~1h

    def __init__(self):
        self.host        = config.TB_HOST
        self.device_id   = config.TB_DEVICE_ID
        self._token      = None
        self._token_ts   = 0.0
        self._headers    = {}

        # Latest data cache
        self.latest: dict = {}          # {"temperature": 29.6, "latitude": ..., ...}
        self.last_update: float = 0.0   # epoch timestamp of last successful poll

        # Background polling
        self._poll_thread: threading.Thread | None = None
        self._polling = False
        self._callback: Callable | None = None   # called whenever new data arrives

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------


    def login(self) -> bool:
        """Fetch a JWT token from ThingsBoard. Returns True on success."""
        try:
            resp = requests.post(
                f"{self.host}/api/auth/login",
                json={"username": config.TB_EMAIL, "password": config.TB_PASSWORD},
                timeout=10,
            )
            if resp.status_code == 200:
                self._token    = resp.json()["token"]
                self._token_ts = time.time()
                self._headers  = {"X-Authorization": f"Bearer {self._token}"}
                print("[BuoyDT] Logged in to ThingsBoard")
                return True
            else:
                print(f"[BuoyDT] Login failed: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            print(f"[BuoyDT] Login error: {e}")
            return False

    def _ensure_token(self):
        if self._token is None or (time.time() - self._token_ts) > self.TOKEN_REFRESH_INTERVAL:
            self.login()

    # ------------------------------------------------------------------
    # Telemetry — latest values
    # ------------------------------------------------------------------

    def get_latest(self, keys: list[str] | None = None) -> dict:
        """
        Fetch the latest telemetry values for the device.
        Returns a flat dict: {"temperature": 29.6, "latitude": 51.22, ...}
        """
        self._ensure_token()
        keys = keys or config.TB_TELEMETRY_KEYS
        try:
            resp = requests.get(
                f"{self.host}/api/plugins/telemetry/DEVICE/{self.device_id}/values/timeseries",
                headers=self._headers,
                params={"keys": ",".join(keys)},
                timeout=10,
            )
            if resp.status_code == 200:
                raw = resp.json()
                # Flatten: {"temperature": [{"ts":..., "value":"29.6"}]} -> {"temperature": 29.6}
                flat = {}
                for k, v_list in raw.items():
                    if v_list:
                        try:
                            flat[k] = float(v_list[0]["value"])
                        except (ValueError, TypeError):
                            flat[k] = v_list[0]["value"]
                self.latest = flat
                self.last_update = time.time()
                return flat
            else:
                print(f"[BuoyDT] Telemetry fetch failed: {resp.status_code}")
                return self.latest   # return cached
        except Exception as e:
            print(f"[BuoyDT] Telemetry error: {e}")
            return self.latest

    # ------------------------------------------------------------------
    # Telemetry — historical range
    # ------------------------------------------------------------------

    def get_history(
        self,
        keys: list[str],
        start_ts: int | None = None,
        end_ts: int | None = None,
        duration_hours: float = 1.0,
        limit: int = 5000,
    ) -> dict:
        """
        Fetch historical telemetry as a dict of lists.

        Returns:
            {
              "temperature": [{"ts": 1234567890000, "value": 29.6}, ...],
              "latitude":    [...],
              ...
            }
        """
        self._ensure_token()
        now_ms   = int(time.time() * 1000)
        end_ts   = end_ts   or now_ms
        start_ts = start_ts or (end_ts - int(duration_hours * 3600 * 1000))

        try:
            resp = requests.get(
                f"{self.host}/api/plugins/telemetry/DEVICE/{self.device_id}/values/timeseries",
                headers=self._headers,
                params={
                    "keys":    ",".join(keys),
                    "startTs": start_ts,
                    "endTs":   end_ts,
                    "limit":   limit,
                    "orderBy": "ASC",
                },
                timeout=15,
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                print(f"[BuoyDT] History fetch failed: {resp.status_code} {resp.text}")
                return {}
        except Exception as e:
            print(f"[BuoyDT] History error: {e}")
            return {}

    # ------------------------------------------------------------------
    # RPC — send commands (motor control, future use)
    # ------------------------------------------------------------------

    # Send rpc asynchronous do it doesn't interrupt the program flow
    def send_rpc_async(self, method: str, params: dict):
        def _task():
            self.send_rpc(method, params)

        threading.Thread(target=_task, daemon=True).start()

    def send_rpc(self, method: str, params: dict, timeout_ms: int = 5000) -> dict | None:
        """
        Send a two-way RPC command to the device and return its response.

        Example:
            buoy.send_rpc("setMotor", {"direction": "left", "speed": 50})
        """
        self._ensure_token()
        try:
            resp = requests.post(
                f"{self.host}/api/plugins/rpc/twoway/{self.device_id}",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"method": method, "params": params, "timeout": timeout_ms},
                timeout=timeout_ms / 1000 + 5,
            )
            if resp.status_code == 200:
                return resp.json()
            else:
                print(f"[BuoyDT] RPC failed: {resp.status_code} {resp.text}")
                return None
        except Exception as e:
            print(f"[BuoyDT] RPC error: {e}")
            return None

    # ------------------------------------------------------------------
    # Background polling
    # ------------------------------------------------------------------

    def start_polling(self, callback: Callable, interval_s: float = config.POLL_INTERVAL_S):
        """
        Start a background thread that polls ThingsBoard every `interval_s` seconds.
        Calls `callback(data: dict)` with the latest flat telemetry dict.
        """
        if self._polling:
            return
        self._callback = callback
        self._polling  = True

        def _loop():
            while self._polling:
                data = self.get_latest()
                if data and self._callback:
                    self._callback(data)
                time.sleep(interval_s)

        self._poll_thread = threading.Thread(target=_loop, daemon=True)
        self._poll_thread.start()
        print(f"[BuoyDT] Polling started (every {interval_s}s)")

    def stop_polling(self):
        self._polling = False
        print("[BuoyDT] Polling stopped")
