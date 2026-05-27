# =============================================================================
# core/river_config.py — Save / Load 
# =============================================================================

import json
import os
from dataclasses import dataclass, asdict

PRESETS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "river_presets")


@dataclass
class RiverConfig:
    name: str
    width_m: float
    gps_polyline: list        # [(lat, lon), ...]
    source: str = "drawn"     # "drawn" | "osm" | "preset"


# ------------------------------------------------------------------
# Local save / load
# ------------------------------------------------------------------

def _ensure_dir():
    os.makedirs(PRESETS_DIR, exist_ok=True)


def save_config(cfg: RiverConfig, filename: str | None = None) -> str:
    _ensure_dir()
    safe = cfg.name.replace(" ", "_").lower()
    fname = filename or f"{safe}.json"
    path = os.path.join(PRESETS_DIR, fname)
    with open(path, "w") as f:
        json.dump(asdict(cfg), f, indent=2)
    print(f"[RiverConfig] Saved → {path}")
    return path


def load_config(path: str) -> RiverConfig:
    with open(path) as f:
        d = json.load(f)
    cfg = RiverConfig(
        name=d["name"],
        width_m=float(d["width_m"]),
        gps_polyline=[tuple(p) for p in d["gps_polyline"]],
        source=d.get("source", "drawn"),
    )
    print(f"[RiverConfig] Loaded '{cfg.name}' — {len(cfg.gps_polyline)} pts, width={cfg.width_m:.0f}m")
    return cfg


def list_saved() -> list[dict]:
    """Return list of {name, path, n_pts, width_m} for all saved presets."""
    _ensure_dir()
    results = []
    for fn in sorted(os.listdir(PRESETS_DIR)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(PRESETS_DIR, fn)
        try:
            with open(path) as f:
                d = json.load(f)
            results.append({
                "label": d.get("name", fn),
                "value": path,
                "n_pts": len(d.get("gps_polyline", [])),
                "width_m": d.get("width_m", 0),
                "source": d.get("source", "drawn"),
            })
        except Exception:
            pass
    return results

