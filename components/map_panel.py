# =============================================================================
# components/map_panel.py — Satellite map panel (dash-leaflet)
# =============================================================================
#
# Three-step setup workflow driven by sim_state.setup_step:
#
#   Step 0  User draws a 2-point POLYLINE across the river → measures width
#   Step 1  User draws a multi-point POLYLINE along the centreline → builds model
#   Step 2  User places a MARKER → sets buoy start position
#   Step 3  Ready — simulation can be started
#
# After setup, the map shows:
#   - River centreline model overlay (dashed cyan)
#   - Buoy GPS track (yellow trail)
#   - Live buoy marker (yellow diamond icon)
#   - Contamination alert badge

import dash_leaflet as dl
from dash import html, Input, Output
import config


ESRI_SATELLITE = dl.TileLayer(
    url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution="Esri World Imagery",
    maxZoom=19,
)
ESRI_LABELS = dl.TileLayer(
    url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    attribution="Esri Labels",
    opacity=0.7,
)

# Colour coding per step
_STEP_COLORS = {0: "#ff9800", 1: "#00e5ff", 2: "#69f0ae", 3: "#69f0ae"}



redIcon = dict(
    iconUrl= 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png',
    shadowUrl= 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png',
    iconSize= [25, 41],
    iconAnchor= [12, 41],
    popupAnchor= [1, -34],
    shadowSize= [41, 41],
    )

def layout():
    return html.Div(
        id="map-panel",
        style={"height": "100%", "width": "100%", "position": "relative"},
        children=[
            dl.Map(
                id="satellite-map",
                center=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON],
                zoom=config.MAP_DEFAULT_ZOOM,
                style={"height": "100%", "width": "100%"},
                children=[
                    ESRI_SATELLITE,
                    ESRI_LABELS,

                    # Draw toolbar
                    dl.FeatureGroup(
                        id="draw-feature-group",
                        children=[
                            dl.EditControl(
                                id="draw-control",
                                draw={
                                    "polyline":    True,
                                    "marker":      True,
                                    "polygon":     False,
                                    "circle":      False,
                                    "circlemarker":False,
                                    "rectangle":   False,
                                },
                                edit={"edit": False, "remove": True},
                            )
                        ]
                    ),

                    # River model centreline overlay (dashed)
                    dl.Polyline(
                        id="river-centerline-overlay",
                        positions=[],
                        color="#00e5ff",
                        weight=2,
                        opacity=0.75,
                        dashArray="6 4",
                    ),

                    # Buoy GPS track
                    dl.Polyline(
                        id="buoy-track",
                        positions=[],
                        color="#ffeb3b",
                        weight=2,
                        opacity=0.7,
                    ),

                    # Live buoy marker — position updated every interval
                    dl.Marker(
                        id="buoy-marker",
                        position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON],
                        children=[
                            dl.Tooltip("Buoy"),
                            dl.Popup(id="buoy-popup", children="Buoy position"),
                        ],
                    ),

                    # Contamination marker — position updated every interval
                    dl.Marker(
                        id="contamination-marker",
                        position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON],
                        icon=redIcon,
                        children=[
                            dl.Tooltip("Contamination"),
                            dl.Popup(id="cont-popup", children="Contamination position"),
                        ],
                    ),

                ],
            ),

            # ---- Workflow hint banner ----
            html.Div(
                id="workflow-hint-banner",
                style={
                    "position":      "absolute",
                    "bottom":        "20px",
                    "left":          "50%",
                    "transform":     "translateX(-50%)",
                    "zIndex":        1000,
                    "pointerEvents": "none",
                    "whiteSpace":    "nowrap",   # prevent wrapping
                },
                children=[
                    html.Span(
                        id="workflow-hint-text",
                        style={
                            "background":    "rgba(13,17,23,0.88)",
                            "color":         "#ff9800",
                            "padding":       "7px 18px",
                            "borderRadius":  "4px",
                            "fontFamily":    "monospace",
                            "fontSize":      "12px",
                            "letterSpacing": "0.06em",
                            "border":        "1px solid #ff9800",
                            "whiteSpace":    "nowrap",
                            "display":       "inline-block",
                        }
                    )
                ]
            ),

            # ---- Contamination alert badge ----
            html.Div(
                id="contam-alert-badge",
                style={
                    "position":      "absolute",
                    "top":           "12px",
                    "left":          "50%",
                    "transform":     "translateX(-50%)",
                    "zIndex":        1000,
                    "display":       "none",
                    "pointerEvents": "none",
                },
                children=[
                    html.Span(
                        "⚠ CONTAMINATION DETECTED — BACKTRACKING",
                        style={
                            "background":    "#ff1744",
                            "color":         "white",
                            "padding":       "6px 16px",
                            "borderRadius":  "4px",
                            "fontFamily":    "monospace",
                            "fontWeight":    "700",
                            "fontSize":      "13px",
                            "letterSpacing": "0.08em",
                        }
                    )
                ]
            ),
        ]
    )


def register_callbacks(app, sim_state , buoy_dt_instance ):

    # ------------------------------------------------------------------
    # Main drawing callback — dispatches to the correct setup step
    # ------------------------------------------------------------------
    @app.callback(
        Output("river-centerline-overlay", "positions"),
        Output("workflow-hint-text",        "children"),
        Output("workflow-hint-text",        "style"),
        Input("draw-control", "geojson"),
        prevent_initial_call=True,
    )
    def on_drawing_complete(geojson):
        """
        Dispatch each completed drawing to the appropriate setup step.

        Step 0 — expects a polyline (width measurement)
        Step 1 — expects a polyline (river centreline)
        Step 2 — expects a marker  (buoy start position)
        """
        hint, color = sim_state.step_hint, _STEP_COLORS.get(sim_state.setup_step, "#8b949e")

        if not geojson or not geojson.get("features"):
            return _no_change(hint, color)

        # Find the most recently added feature (last in list)
        latest = geojson["features"][-1]
        geom   = latest["geometry"]

        # ---------- Step 0: width line ----------
        if sim_state.setup_step == 0:
            if geom["type"] == "LineString":
                coords = [(c[1], c[0]) for c in geom["coordinates"]]
                sim_state.set_gps_width(coords)
                hint  = sim_state.step_hint
                color = _STEP_COLORS[sim_state.setup_step]
            else:
                hint = "⚠ Please draw a LINE across the river for width measurement"

        # ---------- Step 1: river centreline ----------
        elif sim_state.setup_step == 1:
            if geom["type"] == "LineString":
                coords = [(c[1], c[0]) for c in geom["coordinates"]]
                if len(coords) < 2:
                    hint = "⚠ Draw at least 2 points along the river"
                else:
                    sim_state.set_gps_polyline(coords)
                    hint  = sim_state.step_hint
                    color = _STEP_COLORS[sim_state.setup_step]
            else:
                hint = "⚠ Please draw a POLYLINE along the river centreline"

        # ---------- Step 2: buoy start marker ----------
        elif sim_state.setup_step == 2:
            if geom["type"] == "Point":
                lon, lat = geom["coordinates"]
                sim_state.set_buoy_start_gps(lat, lon)
                hint  = sim_state.step_hint
                color = _STEP_COLORS[sim_state.setup_step]
            else:
                hint = "⚠ Please place a MARKER for the buoy starting position"

        overlay = sim_state.get_river_overlay_gps()
        style   = _hint_style(color)
        return overlay, hint, style

    # ------------------------------------------------------------------
    # Buoy marker + track + contamination badge — live update
    # ------------------------------------------------------------------
    @app.callback(
        Output("buoy-marker",        "position"),
        Output("buoy-track",         "positions"),
        Output("buoy-popup",         "children"),
        Output("contamination-marker" ,  "position"), 
        Output("contam-alert-badge", "style"),
        Input("live-update-interval", "n_intervals"),
    )
    def update_buoy_on_map(_):
        lat = buoy_dt_instance.lat or config.MAP_DEFAULT_LAT
        lon = buoy_dt_instance.lon or config.MAP_DEFAULT_LON
        pos = [lat, lon]

        track = list(buoy_dt_instance.buoy_history_gps)   # moved to buoy_dt

        popup_text = (
            f"Lat: {lat:.6f}\n"
            f"Lon: {lon:.6f}\n"
            f"Stream x: {buoy_dt_instance.local_x:.1f} m\n" if buoy_dt_instance.local_x else
            f"Stream x: --\n"
            f"Step: {sim_state.sim_time}"
        )

        badge_style = {
            "position": "absolute", "top": "12px", "left": "50%",
            "transform": "translateX(-50%)", "zIndex": 1000,
            "display": "block" if sim_state.contamination_detected else "none",
            "pointerEvents": "none",
        }

        cont_gps = sim_state.contamination_gps
        pos_cont = list(cont_gps) if cont_gps else [config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON]

        return pos, track, popup_text, pos_cont, badge_style

    # ------------------------------------------------------------------
    # Workflow hint on initial load
    # ------------------------------------------------------------------
    @app.callback(
        Output("workflow-hint-text", "children", allow_duplicate=True),
        Output("workflow-hint-text", "style",    allow_duplicate=True),
        Input("live-update-interval", "n_intervals"),
        prevent_initial_call="initial_duplicate",
    )
    def refresh_hint(_):
        color = _STEP_COLORS.get(sim_state.setup_step, "#8b949e")
        return sim_state.step_hint, _hint_style(color)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _hint_style(color: str) -> dict:
    return {
        "background":    "rgba(13,17,23,0.88)",
        "color":         color,
        "padding":       "7px 18px",
        "borderRadius":  "4px",
        "fontFamily":    "monospace",
        "fontSize":      "12px",
        "letterSpacing": "0.06em",
        "border":        f"1px solid {color}",
    }

def _no_change(hint, color):
    return [], hint, _hint_style(color)