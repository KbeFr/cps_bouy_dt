# =============================================================================
# components/map_panel.py — Satellite map + OSM river picker + Persistent Data
# =============================================================================

import requests
import dash_leaflet as dl
from dash import html, dcc, Input, Output, State, no_update, ALL, ctx
import config

# ---------------------------------------------------------------------------
# Map Layers & Icons
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Overpass Fetching Logic
# ---------------------------------------------------------------------------
_OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

def _fetch_waterways(south, west, north, east) -> list:
    """Returns list of dicts: {name, width, coords: [[lon,lat],...]}"""
    query = f"""
    [out:json][timeout:25];
    way["waterway"="river"]["name"]({south},{west},{north},{east});
    out geom;
    """
    for mirror in _OVERPASS_MIRRORS:
        try:
            resp = requests.post(mirror, data={"data": query}, timeout=25, headers={"User-Agent": "BuoyDT/1.0"})
            resp.raise_for_status()
            rivers = []
            for el in resp.json().get("elements", []):
                geom = el.get("geometry", [])
                if len(geom) < 2:
                    continue
                tags = el.get("tags", {})
                try:
                    width = float(tags.get("width", tags.get("est_width", 100)))
                except (ValueError, TypeError):
                    width = 100.0
                rivers.append({
                    "name":   tags.get("name", "unnamed"),
                    "width":  width,
                    "coords": [[g["lon"], g["lat"]] for g in geom],
                })
            return rivers
        except Exception as e:
            print(f"[MapPanel] Mirror failed ({mirror}): {e}")
    return []

def _trim_coords(coords, start_pct, end_pct):
    n  = len(coords)
    i0 = max(0, int(n * start_pct / 100))
    i1 = min(n - 1, int(n * end_pct / 100))
    if i1 <= i0:
        i1 = i0 + 1
    return coords[i0: i1 + 1]

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
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

                    dl.FeatureGroup(id="draw-feature-group", children=[
                        dl.EditControl(
                            id="draw-control",
                            draw={"polyline": True, "marker": False, "polygon": False,
                                  "circle": False, "circlemarker": False, "rectangle": False},
                            edit={"edit": False, "remove": True},
                        )
                    ]),

                    # Dynamic OSM River Polylines
                    dl.LayerGroup(id="river-lines-group"),
                    dl.Polyline(id="segment-preview", positions=[], color="#69f0ae", weight=4, opacity=0.9, dashArray="6 3"),

                    # Main Simulation Overlays
                    dl.Polyline(id="river-centerline-overlay", positions=[], color="#00e5ff", weight=3, opacity=0.85, dashArray="6 4"),
                    dl.Polyline(id="buoy-track", positions=[], color="#ffeb3b", weight=2, opacity=0.7),

                    dl.Marker(id="buoy-marker", position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON], icon=yellowIcon, children=[dl.Tooltip("Buoy")]),
                    dl.Marker(id="buoy-start-marker", position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON], icon=greenIcon, children=[dl.Tooltip("Buoy start")]),
                    dl.Marker(id="source-marker", position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON], icon=redIcon, children=[dl.Tooltip("Source")]),
                    
                    dl.CircleMarker(
                        id="estimated-source-marker", center=[0, 0], radius=10, color="#00e5ff",
                        weight=3, opacity=1.0, fill=False, fillOpacity=0.0, children=[dl.Tooltip("Estimated source")]
                    ),
                    
                    dl.Marker(id="detection-marker", position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON], children=[dl.Tooltip("First detection")], opacity=0.0),
                ],
            ),

            # Mode Toggle (OSM vs Manual Draw)
            html.Div(style={
                "position": "absolute", "top": "12px", "left": "50%",
                "transform": "translateX(-50%)", "zIndex": 9999,
                "display": "flex", "gap": "6px", "pointerEvents": "all",
            }, children=[
                html.Button(" MANUAL", id="btn-mode-draw", style=_toggle_btn_style("#00e5ff", active=True)),
                html.Button(" OSM RIVERS", id="btn-mode-rivers", style=_toggle_btn_style("#4fc3f7", active=False)),
            ]),

            # OSM River Picker Panel
            html.Div(id="river-picker-panel", style=_panel_style(visible=False), children=[
                html.Div(id="river-picker-status", style={"color": "#4fc3f7", "fontSize": "11px", "marginBottom": "10px", "letterSpacing": "0.04em"}),
                html.Div(style={"display": "flex", "alignItems": "center", "gap": "8px", "marginBottom": "10px"}, children=[
                    html.Span("Selected:", style={"color": "#3d5166", "fontSize": "10px"}),
                    html.Span(id="selected-river-name", style={"color": "#00e5ff", "fontSize": "12px", "fontWeight": "600"}),
                    html.Span(id="selected-river-pts", style={"color": "#3d5166", "fontSize": "10px", "marginLeft": "auto"}),
                ]),
                html.Div(style={"marginBottom": "6px"}, children=[
                    html.Div(style={"display": "flex", "justifyContent": "space-between", "marginBottom": "2px"}, children=[
                        html.Span("Start", style={"color": "#566879", "fontSize": "10px"}),
                        html.Span(id="slider-start-val", style={"color": "#8b949e", "fontSize": "10px"}),
                    ]),
                    dcc.Slider(id="segment-start-slider", min=0, max=100, step=1, value=0, marks={0: "0%", 50: "50%", 100: "100%"}, tooltip={"always_visible": False}),
                ]),
                html.Div(style={"marginBottom": "12px"}, children=[
                    html.Div(style={"display": "flex", "justifyContent": "space-between", "marginBottom": "2px"}, children=[
                        html.Span("End", style={"color": "#566879", "fontSize": "10px"}),
                        html.Span(id="slider-end-val", style={"color": "#8b949e", "fontSize": "10px"}),
                    ]),
                    dcc.Slider(id="segment-end-slider", min=0, max=100, step=1, value=100, marks={0: "0%", 50: "50%", 100: "100%"}, tooltip={"always_visible": False}),
                ]),
                html.Div(style={"display": "flex", "gap": "8px"}, children=[
                    html.Button("USE THIS SEGMENT", id="btn-confirm-segment", style={
                        "background": "#003820", "border": "1px solid #69f0ae", "color": "#69f0ae",
                        "fontFamily": "monospace", "fontSize": "10px", "padding": "6px 14px",
                        "cursor": "pointer", "borderRadius": "2px", "letterSpacing": "0.06em", "flex": "1",
                    }),
                    html.Button("✕", id="btn-cancel-segment", style={
                        "background": "transparent", "border": "1px solid #3d5166", "color": "#566879",
                        "fontFamily": "monospace", "fontSize": "10px", "padding": "6px 10px",
                        "cursor": "pointer", "borderRadius": "2px",
                    }),
                ]),
            ]),

            # Placement / Workflow Hint
            html.Div(id="placement-hint-banner", style={
                "position": "absolute", "bottom": "20px", "left": "50%",
                "transform": "translateX(-50%)", "zIndex": 1000,
                "pointerEvents": "none", "whiteSpace": "nowrap",
            }, children=[html.Span(id="placement-hint-text", style=_hint_style("#69f0ae"))]),

            # Contamination Alert Badge
            html.Div(id="contam-alert-badge", style={
                "position": "absolute", "top": "50px", "left": "50%",
                "transform": "translateX(-50%)", "zIndex": 1000, "display": "none", "pointerEvents": "none",
                # Added constraints to force a clean wrap before it hits screen edges
                "width": "max-content", "maxWidth": "80vw" 
            }, children=[
                # Changed from html.Span to html.Div to act as a proper block container
                html.Div(id="contam-alert-text", children="⚠ CONTAMINATION DETECTED", style={
                    "background": "#ff1744", "color": "white", "padding": "8px 16px",
                    "borderRadius": "4px", "fontFamily": "monospace", "fontWeight": "700",
                    "fontSize": "13px", "letterSpacing": "0.08em",
                    # Added typography rules for clean multi-line wrapping
                    "lineHeight": "1.5", "textAlign": "center", "wordWrap": "break-word",
                    "boxShadow": "0 4px 12px rgba(0,0,0,0.4)" # Optional: added shadow for contrast
                })
            ]),

            # Stores
            dcc.Store(id="draw-trigger-store", data=0),
            dcc.Store(id="clear-trigger-store", data=0),
            dcc.Store(id="store-map-mode", data="draw"),
            dcc.Store(id="store-river-list", data=[]),
            dcc.Store(id="store-selected-river", data=None),
        ]
    )

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
def register_callbacks(app, sim_state, buoy_dt_instance):

    # 1. Manual Polyline Drawn -> Ingest to Sim State
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

    # 2. Drive EditControl Programmatically
    @app.callback(
        Output("draw-control", "drawToolbar"),
        Input("draw-trigger-store", "data"),
        prevent_initial_call=True,
    )
    def trigger_draw(n):
        if not n: return no_update
        return {"mode": "polyline", "n_clicks": n}

    @app.callback(
        Output("draw-control", "editToolbar"),
        Input("clear-trigger-store", "data"),
        prevent_initial_call=True,
    )
    def trigger_clear(n):
        if not n: return no_update
        return {"mode": "remove", "action": "clear all", "n_clicks": n}

    # 3. Map Click -> Sim State Point Placement
    @app.callback(
        Output("satellite-map", "n_clicks"),
        Input("satellite-map", "clickData"),
        prevent_initial_call=True,
    )
    def on_map_click(click_data):
        if not click_data: return no_update
        latlng = click_data.get("latlng") if isinstance(click_data, dict) else None
        if not latlng: return no_update
        sim_state.handle_map_click(latlng["lat"], latlng["lng"])
        return no_update

    # 4. OSM / Mode Toggle Layer
    @app.callback(
        Output("store-map-mode", "data"),
        Output("store-river-list", "data"),
        Output("river-lines-group", "children"),
        Output("btn-mode-draw", "style"),
        Output("btn-mode-rivers", "style"),
        Output("river-picker-panel", "style"),
        Output("river-picker-status", "children"),
        Input("btn-mode-draw", "n_clicks"),
        Input("btn-mode-rivers", "n_clicks"),
        State("satellite-map", "bounds"),
        prevent_initial_call=True,
    )
    def toggle_mode(n_draw, n_rivers, bounds):
        if not ctx.triggered or not ctx.triggered[0]["value"]:
            return (no_update,) * 7

        if ctx.triggered_id == "btn-mode-draw":
            return ("draw", [], [], _toggle_btn_style("#00e5ff", active=True), _toggle_btn_style("#4fc3f7", active=False), _panel_style(visible=False), "")

        if not bounds:
            return ("rivers", [], [], _toggle_btn_style("#00e5ff", active=False), _toggle_btn_style("#4fc3f7", active=True), _panel_style(visible=True), "Pan/zoom the map then click OSM RIVERS again to load")

        s, w = bounds[0]
        n, e = bounds[1]
        rivers = _fetch_waterways(s, w, n, e)

        if not rivers:
            return ("rivers", [], [], _toggle_btn_style("#00e5ff", active=False), _toggle_btn_style("#4fc3f7", active=True), _panel_style(visible=True), "No major rivers found in view — try zooming out")

        polylines = []
        for i, rv in enumerate(rivers):
            positions = [[c[1], c[0]] for c in rv["coords"]]
            polylines.append(dl.Polyline(id={"type": "river-line", "index": i}, positions=positions, color="#4fc3f7", weight=4, opacity=0.7, children=[dl.Tooltip(rv["name"])]))

        unique_names = list(dict.fromkeys(rv["name"] for rv in rivers))
        preview = ", ".join(unique_names[:4]) + ("…" if len(unique_names) > 4 else "")
        return ("rivers", rivers, polylines, _toggle_btn_style("#00e5ff", active=False), _toggle_btn_style("#4fc3f7", active=True), _panel_style(visible=True), f"Found {len(rivers)} segment(s): {preview}")

    # 5. OSM Picker Interactions
    @app.callback(
        Output("store-selected-river", "data"),
        Output("selected-river-name", "children"),
        Output("selected-river-pts", "children"),
        Output("segment-start-slider", "value"),
        Output("segment-end-slider", "value"),
        Output("river-lines-group", "children", allow_duplicate=True),
        Input({"type": "river-line", "index": ALL}, "n_clicks"),
        State("store-river-list", "data"),
        prevent_initial_call=True,
    )
    def on_river_click(n_clicks_list, rivers):
        if not any(n_clicks_list) or not rivers: return (no_update,) * 6
        clicked_idx = next((i for i, n in enumerate(n_clicks_list) if n), None)
        if clicked_idx is None or clicked_idx >= len(rivers): return (no_update,) * 6

        rv = rivers[clicked_idx]
        polylines = []
        for i, r in enumerate(rivers):
            positions = [[c[1], c[0]] for c in r["coords"]]
            selected  = (i == clicked_idx)
            polylines.append(dl.Polyline(
                id={"type": "river-line", "index": i}, positions=positions,
                color="#00e5ff" if selected else "#4fc3f7", weight=6 if selected else 4,
                opacity=1.0 if selected else 0.45, children=[dl.Tooltip(r["name"])]
            ))
        return (rv, rv["name"], f"{len(rv['coords'])} pts", 0, 100, polylines)

    @app.callback(
        Output("segment-preview", "positions"),
        Output("slider-start-val", "children"),
        Output("slider-end-val", "children"),
        Input("segment-start-slider", "value"),
        Input("segment-end-slider", "value"),
        State("store-selected-river", "data"),
        prevent_initial_call=True,
    )
    def update_preview(start_pct, end_pct, river):
        if not river: return [], f"{start_pct}%", f"{end_pct}%"
        trimmed = _trim_coords(river["coords"], start_pct, end_pct)
        return [[c[1], c[0]] for c in trimmed], f"{start_pct}%", f"{end_pct}%"

    @app.callback(
        Output("store-map-mode", "data", allow_duplicate=True),
        Output("btn-mode-draw", "style", allow_duplicate=True),
        Output("btn-mode-rivers", "style", allow_duplicate=True),
        Output("river-picker-panel", "style", allow_duplicate=True),
        Output("segment-preview", "positions", allow_duplicate=True),
        Output("river-lines-group", "children", allow_duplicate=True),
        Input("btn-confirm-segment", "n_clicks"),
        State("store-selected-river", "data"),
        State("segment-start-slider", "value"),
        State("segment-end-slider", "value"),
        prevent_initial_call=True,
    )
    def confirm_segment(_, river, start_pct, end_pct):
        if not river: return (no_update,) * 6
        trimmed = _trim_coords(river["coords"], start_pct, end_pct)
        if len(trimmed) < 2: return (no_update,) * 6

        # Pipe OSM data directly into the new architecture
        gps_pts = [(c[1], c[0]) for c in trimmed]
        sim_state.set_drawn_centerline(gps_pts)
        
        # If your new sim_state handles width explicitly, set it here
        if hasattr(sim_state, "river_width"):
            sim_state.river_width = float(river.get("width", 100))
        
        sim_state.set_placement_mode(sim_state.PLACE_NONE)
        sim_state.set_toast("OSM River segment loaded.", "#69f0ae", duration_s=5.0)
        
        return ("draw", _toggle_btn_style("#00e5ff", active=True), _toggle_btn_style("#4fc3f7", active=False), _panel_style(visible=False), [], [])

    @app.callback(
        Output("river-picker-panel", "style", allow_duplicate=True),
        Output("store-map-mode", "data", allow_duplicate=True),
        Output("btn-mode-draw", "style", allow_duplicate=True),
        Output("btn-mode-rivers", "style", allow_duplicate=True),
        Output("segment-preview", "positions", allow_duplicate=True),
        Output("river-lines-group", "children", allow_duplicate=True),
        Input("btn-cancel-segment", "n_clicks"),
        prevent_initial_call=True,
    )
    def cancel_segment(_):
        return (_panel_style(visible=False), "draw", _toggle_btn_style("#00e5ff", active=True), _toggle_btn_style("#4fc3f7", active=False), [], [])


    # 6. Live Map Update Tick
    @app.callback(
        Output("river-centerline-overlay", "positions"),
        Output("buoy-marker", "position"),
        Output("buoy-start-marker", "position", allow_duplicate=True),
        Output("source-marker", "position", allow_duplicate=True),
        Output("buoy-track", "positions"),
        Output("contam-alert-badge", "style"),
        Output("contam-alert-text", "children"),
        Output("detection-marker", "position"),
        Output("detection-marker", "opacity"),
        Output("estimated-source-marker", "center"),
        Output("estimated-source-marker", "opacity"),
        Output("placement-hint-text", "children"),
        Output("placement-hint-text", "style"),
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

        src_pos = list(sim_state.source_gps) if sim_state.source_gps else [config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON]
        track = [list(p) for p in buoy_dt_instance.buoy_history_gps if p[0] is not None and p[1] is not None]

        sev = sim_state.contamination_severity
        badge_style = {
            "position": "absolute", "top": "50px", "left": "50%",
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
            est_pos, est_opacity = list(est), 1.0
        else:
            est_pos, est_opacity = [0.0, 0.0], 1.0

        toast_text, toast_color = sim_state.get_toast()
        if toast_text is not None:
            hint, color = toast_text, toast_color
        else:
            hint = sim_state.placement_hint
            color = "#ff9800" if sim_state.placement_mode else "#69f0ae"

        return (overlay, buoy_pos, start_pos, src_pos, track, badge_style, alert_text, det_pos, det_opacity, est_pos, est_opacity, hint, _hint_style(color))


# ---------------------------------------------------------------------------
# Style Helpers
# ---------------------------------------------------------------------------
def _toggle_btn_style(color: str, active: bool) -> dict:
    return {
        "background": f"{color}25" if active else "rgba(8,13,20,0.88)",
        "border": f"1.5px solid {color}" if active else "1px solid #2a3a4a",
        "color": color if active else "#566879",
        "fontFamily": "monospace", "fontSize": "11px",
        "fontWeight": "600" if active else "400",
        "letterSpacing": "0.09em", "padding": "7px 16px",
        "cursor": "pointer", "borderRadius": "3px",
        "boxShadow": "0 2px 8px rgba(0,0,0,0.5)", "transition": "all 0.15s",
    }

def _panel_style(visible: bool) -> dict:
    return {
        "position": "absolute", "bottom": "60px", "left": "50%",
        "transform": "translateX(-50%)", "zIndex": 1000,
        "background": "rgba(8,13,20,0.95)", "border": "1px solid #1e3a55",
        "borderRadius": "4px", "padding": "12px 16px",
        "minWidth": "380px", "maxWidth": "480px",
        "fontFamily": "monospace", "display": "block" if visible else "none",
    }

def _hint_style(color: str) -> dict:
    return {
        "background": "rgba(13,17,23,0.88)", "color": color,
        "padding": "7px 18px", "borderRadius": "4px",
        "fontFamily": "monospace", "fontSize": "12px",
        "letterSpacing": "0.06em", "border": f"1px solid {color}",
    }