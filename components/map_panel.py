# =============================================================================
# components/map_panel.py — Satellite map with persistent river drawing
# =============================================================================
#
# The latest drawn river centerline is saved and reused after restarting.
# The map also places the CONTAMINATION SOURCE and BUOY START markers.

import dash_leaflet as dl
from dash import html, dcc, Input, Output, State, no_update
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


def _icon(color: str):
    return dict(
        iconUrl=f"https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-{color}.png",
        shadowUrl="https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png",
        iconSize=[25, 41], iconAnchor=[12, 41],
        popupAnchor=[1, -34], shadowSize=[41, 41],
    )


redIcon    = _icon("red")
greenIcon  = _icon("green")
yellowIcon = _icon("yellow")
blueIcon   = _icon("blue")


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

                    # Polyline-drawing tool for replacing the saved river.
                    dl.FeatureGroup(
                        id="draw-feature-group",
                        children=[
                            dl.EditControl(
                                id="draw-control",
                                draw={
                                    "polyline":     True,
                                    "marker":       False,
                                    "polygon":      False,
                                    "circle":       False,
                                    "circlemarker": False,
                                    "rectangle":    False,
                                },
                                edit={"edit": False, "remove": True},
                            )
                        ]
                    ),

                    # River centerline (preloaded — drawn on app start)
                    dl.Polyline(
                        id="river-centerline-overlay",
                        positions=[],
                        color="#00e5ff", weight=3, opacity=0.85, dashArray="6 4",
                    ),

                    # Buoy GPS track
                    dl.Polyline(
                        id="buoy-track",
                        positions=[],
                        color="#ffeb3b", weight=2, opacity=0.7,
                    ),

                    # Live buoy marker
                    dl.Marker(
                        id="buoy-marker",
                        position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON],
                        icon=yellowIcon,
                        children=[dl.Tooltip("Buoy")],
                    ),

                    # Buoy START marker (reference)
                    dl.Marker(
                        id="buoy-start-marker",
                        position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON],
                        icon=greenIcon,
                        children=[dl.Tooltip("Buoy start")],
                    ),

                    # Contamination SOURCE marker (true source, sim only)
                    dl.Marker(
                        id="source-marker",
                        position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON],
                        icon=redIcon,
                        children=[dl.Tooltip("Source")],
                    ),

                    # Estimated source — cyan open circle (matches river-model panel)
                    # Always rendered (some dash-leaflet versions don't mount the
                    # SVG when initial opacity=0). When there is no estimate yet
                    # we park it off-screen via the update callback.
                    dl.CircleMarker(
                        id="estimated-source-marker",
                        center=[0, 0],            # parked off-screen until estimator fires
                        radius=10,
                        color="#00e5ff",
                        weight=3,
                        opacity=1.0,
                        fill=False,               # open circle (no fill)
                        fillOpacity=0.0,
                        children=[dl.Tooltip("Estimated source (backtrack)")],
                    ),

                    # Detection-point marker
                    dl.Marker(
                        id="detection-marker",
                        position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON],
                        children=[dl.Tooltip("First detection")],
                        opacity=0.0,
                    ),
                ],
            ),

            # ---- Placement hint banner ----
            html.Div(
                id="placement-hint-banner",
                style={
                    "position": "absolute", "bottom": "20px",
                    "left": "50%", "transform": "translateX(-50%)",
                    "zIndex": 1000, "pointerEvents": "none",
                    "whiteSpace": "nowrap",
                },
                children=[
                    html.Span(
                        id="placement-hint-text",
                        style=_hint_style("#69f0ae"),
                    )
                ]
            ),

            # ---- Hidden counters used to drive EditControl programmatically ----
            dcc.Store(id="draw-trigger-store", data=0),
            dcc.Store(id="clear-trigger-store", data=0),

            # ---- Contamination alert badge ----
            html.Div(
                id="contam-alert-badge",
                style={
                    "position": "absolute", "top": "12px",
                    "left": "50%", "transform": "translateX(-50%)",
                    "zIndex": 1000, "display": "none",
                    "pointerEvents": "none",
                },
                children=[
                    html.Span(
                        id="contam-alert-text",
                        children="⚠ CONTAMINATION DETECTED",
                        style={
                            "background":    "#ff1744", "color": "white",
                            "padding":       "6px 16px", "borderRadius": "4px",
                            "fontFamily":    "monospace", "fontWeight": "700",
                            "fontSize":      "13px", "letterSpacing": "0.08em",
                        }
                    )
                ]
            ),
        ]
    )


def register_callbacks(app, sim_state, buoy_dt_instance):

    # ------------------------------------------------------------------
    # Polyline drawn -> rebuild and persist the river.
    # ------------------------------------------------------------------
    @app.callback(
        Output("clear-trigger-store", "data"),
        Input("draw-control", "geojson"),
        State("clear-trigger-store", "data"),
        prevent_initial_call=True,
    )
    def on_polyline_drawn(geojson, clear_counter):
        if not geojson or not geojson.get("features"):
            return no_update
        latest = geojson["features"][-1]
        geom = latest.get("geometry") or {}
        if geom.get("type") != "LineString":
            return no_update
        coords = [(c[1], c[0]) for c in geom["coordinates"]]

        mode = sim_state.placement_mode
        next_clear = (clear_counter or 0) + 1

        if mode == sim_state.PLACE_RIVER:
            if len(coords) < 2:
                sim_state.set_toast("Need >=2 points along the river", "#ff9800")
            else:
                sim_state.set_drawn_centerline(coords)
                sim_state.set_placement_mode(sim_state.PLACE_NONE)
                sim_state.set_toast("River updated and saved.", "#69f0ae", duration_s=5.0)
            return next_clear

        if mode == sim_state.PLACE_WIDTH:
            if len(coords) < 2:
                sim_state.set_toast("Need a 2-point line across the river", "#ff9800")
            else:
                sim_state.set_drawn_width(coords)
                sim_state.set_placement_mode(sim_state.PLACE_NONE)
            return next_clear

        sim_state.set_toast("Arm DRAW RIVER or DRAW WIDTH first.", "#ff9800")
        return next_clear

    # ------------------------------------------------------------------
    # Drive EditControl programmatically:
    #   - bump draw-trigger-store  -> activate polyline draw tool
    #   - bump clear-trigger-store -> clear shapes after we ingest them
    # ------------------------------------------------------------------
    @app.callback(
        Output("draw-control", "drawToolbar"),
        Input("draw-trigger-store", "data"),
        prevent_initial_call=True,
    )
    def trigger_draw(n):
        if not n:
            return no_update
        return {"mode": "polyline", "n_clicks": n}

    @app.callback(
        Output("draw-control", "editToolbar"),
        Input("clear-trigger-store", "data"),
        prevent_initial_call=True,
    )
    def trigger_clear(n):
        if not n:
            return no_update
        return {"mode": "remove", "action": "clear all", "n_clicks": n}

    # ------------------------------------------------------------------
    # Map click → place source / buoy depending on armed mode
    # The visible markers refresh on the next live-update tick (~1s).
    # ------------------------------------------------------------------
    @app.callback(
        Output("satellite-map", "n_clicks"),   # dummy output; we mutate sim_state in place
        Input("satellite-map", "clickData"),
        prevent_initial_call=True,
    )
    def on_map_click(click_data):
        if not click_data:
            return no_update
        latlng = click_data.get("latlng") if isinstance(click_data, dict) else None
        if not latlng:
            return no_update
        lat, lon = latlng["lat"], latlng["lng"]
        sim_state.handle_map_click(lat, lon)
        # update_map will refresh the markers + hint on the next live-update tick
        return no_update

    # ------------------------------------------------------------------
    # Live marker + track + alert badge + centerline overlay + hint
    # ------------------------------------------------------------------
    @app.callback(
        Output("river-centerline-overlay", "positions"),
        Output("buoy-marker",              "position"),
        Output("buoy-start-marker",        "position", allow_duplicate=True),
        Output("source-marker",            "position", allow_duplicate=True),
        Output("buoy-track",               "positions"),
        Output("contam-alert-badge",       "style"),
        Output("contam-alert-text",        "children"),
        Output("detection-marker",         "position"),
        Output("detection-marker",         "opacity"),
        Output("estimated-source-marker",  "center"),
        Output("estimated-source-marker",  "opacity"),
        Output("placement-hint-text",      "children"),
        Output("placement-hint-text",      "style"),
        Input("live-update-interval", "n_intervals"),
        prevent_initial_call="initial_duplicate",
    )
    def update_map(_):
        overlay = sim_state.get_river_overlay_gps()

        lat = buoy_dt_instance.lat if buoy_dt_instance.lat is not None else config.MAP_DEFAULT_LAT
        lon = buoy_dt_instance.lon if buoy_dt_instance.lon is not None else config.MAP_DEFAULT_LON
        buoy_pos = [lat, lon]

        if buoy_dt_instance.start_local is not None and sim_state.georef.is_set:
            sx, sy = buoy_dt_instance.start_local
            start_lat, start_lon = sim_state.georef.sim_cartesian_to_gps(sx, sy)
            start_pos = [start_lat, start_lon]
        else:
            start_pos = [config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON]

        src_pos = list(sim_state.source_gps) if sim_state.source_gps else \
                  [config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON]

        track = [list(p) for p in buoy_dt_instance.buoy_history_gps
                 if p[0] is not None and p[1] is not None]

        sev = sim_state.contamination_severity
        badge_style = {
            "position": "absolute", "top": "12px", "left": "50%",
            "transform": "translateX(-50%)", "zIndex": 1000,
            "display": "block" if sim_state.contamination_detected else "none",
            "pointerEvents": "none",
        }
        if sev == "critical":
            alert_text = "🚨 CRITICAL — " + ", ".join(sim_state.contamination_rules_hit[:3])
        elif sev == "warning":
            alert_text = "⚠ WARNING — " + ", ".join(sim_state.contamination_rules_hit[:3])
        else:
            alert_text = "Contamination detected"

        if sim_state.contamination_detected:
            det_pos = list(sim_state.contamination_gps)
            det_opacity = 1.0
        else:
            det_pos = [config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON]
            det_opacity = 0.0

        est = sim_state.get_estimated_source_gps()
        if est is not None:
            est_pos = list(est)
            est_opacity = 1.0
        else:
            # Park the circle far off-screen so it isn't visible; keep opacity=1
            # because dash-leaflet may not re-mount the SVG when toggling opacity.
            est_pos = [0.0, 0.0]
            est_opacity = 1.0

        toast_text, toast_color = sim_state.get_toast()
        if toast_text is not None:
            hint, color = toast_text, toast_color
        else:
            hint = sim_state.placement_hint
            color = "#ff9800" if sim_state.placement_mode else "#69f0ae"

        return (overlay, buoy_pos, start_pos, src_pos, track,
                badge_style, alert_text,
                det_pos, det_opacity,
                est_pos, est_opacity,
                hint, _hint_style(color))


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
