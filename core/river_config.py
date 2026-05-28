# =============================================================================
# core/river_config.py — Save / Load 
# =============================================================================

import json
import os
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any


PRESETS_DIR = os.path.join(os.path.dirname(__file__), "..", "river_presets")


@dataclass
class RiverConfig:
    name: str
    width_m: float
    gps_polyline: list        # [(lat, lon), ...]
    source: str = "drawn"     # "drawn" | "osm" | "preset"
    measurement_log: List[Dict[str, Any]] = field(default_factory=list)

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
        measurement_log=d.get("measurement_log", [])
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


# ------------------------------------------------------------------
# Autosave  (single merge-on-write file used by the simulation)
# ------------------------------------------------------------------

AUTOSAVE_NAME = "autosave.json"
_AUTOSAVE_PATH = os.path.join(PRESETS_DIR, AUTOSAVE_NAME)


def load_autosave() -> tuple[list[tuple] | None, float | None]:
    """Return (centerline_pts, width_m) from the autosave file, or (None, None)."""
    try:
        cfg = load_config(_AUTOSAVE_PATH)
        pts   = cfg.gps_polyline if len(cfg.gps_polyline) >= 2 else None
        width = cfg.width_m      if cfg.width_m > 0            else None
        return pts, width
    except (FileNotFoundError, KeyError, Exception):
        return None, None


def save_autosave(
    points:  list[tuple] | None = None,
    width_m: float        | None = None,
    default_width: float = 80.0,
) -> None:
    """Merge-write the autosave file.

    Only the fields that are passed in are updated; the rest are kept from
    whatever was already on disk.  This mirrors the old _save_river_state
    logic so callers don't need to load-merge-save themselves.
    """
    existing_pts, existing_w = load_autosave()

    out_pts   = points  if points  is not None else (existing_pts or [])
    out_width = width_m if width_m is not None else (existing_w   or default_width)

    cfg = RiverConfig(
        name="Autosave",
        width_m=float(out_width),
        gps_polyline=out_pts,
        source="drawn",
    )
    save_config(cfg, filename=AUTOSAVE_NAME)
    print(
        f"[RiverConfig] Autosaved → {_AUTOSAVE_PATH} "
        f"(pts={len(out_pts)}, width={out_width:.1f}m)"
    )