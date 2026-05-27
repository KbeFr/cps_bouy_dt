# =============================================================================
# components/map_panel.py — Satellite map + OSM river picker
# =============================================================================

import requests
import dash_leaflet as dl
from dash import html, dcc, Input, Output, State, no_update, ALL, ctx
import config

# ---------------------------------------------------------------------------
ESRI_SATELLITE = dl.TileLayer(
    url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution="Esri World Imagery", maxZoom=19,
)
ESRI_LABELS = dl.TileLayer(
    url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    attribution="Esri Labels", opacity=0.7,
)

_STEP_COLORS = {0: "#ff9800", 1: "#00e5ff", 2: "#69f0ae", 3: "#69f0ae", 4: "#b388ff"} 

_OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

redIcon = dict(
    iconUrl="https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-red.png",
    shadowUrl="https://cdnjs.cloudflare.com/ajax/libs/leaflet/0.7.7/images/marker-shadow.png",
    iconSize=[25, 41], iconAnchor=[12, 41], popupAnchor=[1, -34], shadowSize=[41, 41],
)

# ---------------------------------------------------------------------------
# Overpass Fetching
# ---------------------------------------------------------------------------

def _fetch_waterways(south, west, north, east) -> list:
    """
    Returns list of dicts: {name, width, coords: [[lon,lat],...]}
    """
    query = f"""
    [out:json][timeout:25];
    way["waterway"="river"]["name"]({south},{west},{north},{east});
    out geom;
    """
    for mirror in _OVERPASS_MIRRORS:
        try:
            resp = requests.post(mirror, data={"data": query}, timeout=25,
                                 headers={"User-Agent": "BuoyDT/1.0"})
            resp.raise_for_status()
            rivers = []
            for el in resp.json().get("elements", []):
                geom = el.get("geometry", [])
                # Lowered the filter from 8 to 2 so we don't accidentally drop valid data
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
                            draw={"polyline": True, "marker": True, "polygon": False,
                                  "circle": False, "circlemarker": False, "rectangle": False},
                            edit={"edit": False, "remove": True},
                        )
                    ]),

                    # Dynamic River Polylines
                    dl.LayerGroup(id="river-lines-group"),

                    dl.Polyline(id="segment-preview", positions=[],
                                color="#69f0ae", weight=4, opacity=0.9, dashArray="6 3"),

                    dl.Polyline(id="river-centerline-overlay", positions=[],
                                color="#00e5ff", weight=2, opacity=0.75, dashArray="6 4"),

                    dl.Polyline(id="buoy-track", positions=[],
                                color="#ffeb3b", weight=2, opacity=0.7),

                    dl.Marker(id="buoy-marker",
                              position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON],
                              children=[dl.Tooltip("Buoy"),
                                        dl.Popup(id="buoy-popup", children="Buoy position")]),

                    dl.Marker(id="contamination-marker",
                              position=[config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON],
                              icon=redIcon,
                              children=[dl.Tooltip("Contamination"),
                                        dl.Popup(id="cont-popup", children="Contamination")]),
                ],
            ),

            # Mode toggle
            html.Div(style={
                "position":  "absolute", "top": "12px", "left": "50%",
                "transform": "translateX(-50%)", "zIndex": 9999,
                "display":   "flex", "gap": "6px", "pointerEvents": "all",
            }, children=[
                html.Button(" DRAW",    id="btn-mode-draw", style=_toggle_btn_style("#00e5ff", active=True)),
                html.Button(" RIVERS", id="btn-mode-rivers", style=_toggle_btn_style("#4fc3f7", active=False)),
            ]),

            # River picker panel
            html.Div(id="river-picker-panel", style=_panel_style(visible=False), children=[
                html.Div(id="river-picker-status",
                         style={"color": "#4fc3f7", "fontSize": "11px",
                                "marginBottom": "10px", "letterSpacing": "0.04em"}),

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

            html.Div(id="workflow-hint-banner", style={
                "position": "absolute", "bottom": "14px", "left": "50%",
                "transform": "translateX(-50%)", "zIndex": 998,
                "pointerEvents": "none", "whiteSpace": "nowrap",
            }, children=[
                html.Span(id="workflow-hint-text", style={
                    "background": "rgba(13,17,23,0.88)", "color": "#ff9800", "padding": "7px 18px",
                    "borderRadius": "4px", "fontFamily": "monospace", "fontSize": "12px",
                    "letterSpacing": "0.06em", "border": "1px solid #ff9800",
                    "whiteSpace": "nowrap", "display": "inline-block",
                })
            ]),

            html.Div(id="contam-alert-badge", style={
                "position": "absolute", "top": "50px", "left": "50%",
                "transform": "translateX(-50%)", "zIndex": 1000,
                "display": "none", "pointerEvents": "none",
            }, children=[html.Span("⚠ CONTAMINATION DETECTED — BACKTRACKING", style={
                "background": "#ff1744", "color": "white", "padding": "6px 16px",
                "borderRadius": "4px", "fontFamily": "monospace", "fontWeight": "700",
                "fontSize": "13px", "letterSpacing": "0.08em",
            })]),

            # Stores
            dcc.Store(id="store-map-mode",       data="draw"),
            dcc.Store(id="store-river-list",     data=[]),
            dcc.Store(id="store-selected-river", data=None),
        ]
    )


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def register_callbacks(app, sim_state, buoy_dt_instance):

    @app.callback(
        Output("store-map-mode",      "data"),
        Output("store-river-list",    "data"),
        Output("river-lines-group",   "children"),
        Output("btn-mode-draw",       "style"),
        Output("btn-mode-rivers",     "style"),
        Output("river-picker-panel",  "style"),
        Output("river-picker-status", "children"),
        Input("btn-mode-draw",        "n_clicks"),
        Input("btn-mode-rivers",      "n_clicks"),
        State("satellite-map",        "bounds"),
        prevent_initial_call=True,
    )
    def toggle_mode(n_draw, n_rivers, bounds):
        if not ctx.triggered or not ctx.triggered[0]["value"]:
            return (no_update,) * 7

        if ctx.triggered_id == "btn-mode-draw":
            return ("draw", [], [], _toggle_btn_style("#00e5ff", active=True),
                    _toggle_btn_style("#4fc3f7", active=False), _panel_style(visible=False), "")

        if not bounds:
            return ("rivers", [], [], _toggle_btn_style("#00e5ff", active=False),
                    _toggle_btn_style("#4fc3f7", active=True), _panel_style(visible=True),
                    "Pan/zoom the map then click RIVERS again to load")

        s, w = bounds[0]
        n, e = bounds[1]
        rivers = _fetch_waterways(s, w, n, e)

        if not rivers:
            return ("rivers", [], [], _toggle_btn_style("#00e5ff", active=False),
                    _toggle_btn_style("#4fc3f7", active=True), _panel_style(visible=True),
                    "No major rivers found in view — try zooming out or check network")

        polylines = []
        for i, rv in enumerate(rivers):
            positions = [[c[1], c[0]] for c in rv["coords"]]
            polylines.append(dl.Polyline(
                id={"type": "river-line", "index": i},
                positions=positions, color="#4fc3f7", weight=4, opacity=0.7,
                children=[dl.Tooltip(rv["name"])],
            ))

        unique_names = list(dict.fromkeys(rv["name"] for rv in rivers))
        preview = ", ".join(unique_names[:4]) + ("…" if len(unique_names) > 4 else "")
        status  = f"Found {len(rivers)} segment(s): {preview} — click a river to select"

        return ("rivers", rivers, polylines, _toggle_btn_style("#00e5ff", active=False),
                _toggle_btn_style("#4fc3f7", active=True), _panel_style(visible=True), status)


    @app.callback(
        Output("river-centerline-overlay","positions", allow_duplicate=True),
        Input("draw-control",  "geojson"),
        State("store-map-mode","data"),
        prevent_initial_call=True,
    )
    def on_drawing_complete(geojson, mode):
        if mode == "rivers":
            return no_update
        if not geojson or not geojson.get("features"):
            return no_update

        latest = geojson["features"][-1]
        geom   = latest["geometry"]

        if sim_state.setup_step == 0:
            if geom["type"] == "LineString":
                sim_state.set_gps_width([(c[1], c[0]) for c in geom["coordinates"]])

        elif sim_state.setup_step == 1:
            if geom["type"] == "LineString":
                coords = [(c[1], c[0]) for c in geom["coordinates"]]
                if len(coords) >= 2:
                    sim_state.set_gps_polyline(coords)

        elif sim_state.setup_step == 2:
            if geom["type"] == "Point":
                lon, lat = geom["coordinates"]
                sim_state.set_buoy_start_gps(lat, lon)

        elif sim_state.setup_step == 4:
            if geom["type"] == "Point":
                lon, lat = geom["coordinates"]
                sim_state.set_contamination_source_gps(lat, lon)

        return sim_state.get_river_overlay_gps()
    

    @app.callback(
        Output("buoy-marker",         "position"),
        Output("buoy-track",          "positions"),
        Output("buoy-popup",          "children"),
        Output("contamination-marker","position"),
        Output("contam-alert-badge",  "style"),
        Input("live-update-interval", "n_intervals"),
    )
    def update_buoy_on_map(_):
        lat = buoy_dt_instance.lat or config.MAP_DEFAULT_LAT
        lon = buoy_dt_instance.lon or config.MAP_DEFAULT_LON
        lx  = buoy_dt_instance.local_x
        popup = (f"Lat: {lat:.6f}  Lon: {lon:.6f}\nStream x: {lx:.1f} m  Step: {sim_state.sim_time}" if lx is not None
                 else f"Lat: {lat:.6f}  Lon: {lon:.6f}\nStep: {sim_state.sim_time}")

        badge_style = {
            "position": "absolute", "top": "50px", "left": "50%",
            "transform": "translateX(-50%)", "zIndex": 1000,
            "display": "block" if sim_state.contamination_detected else "none",
            "pointerEvents": "none",
        }
        cont = list(sim_state.contamination_gps) if sim_state.contamination_gps else [config.MAP_DEFAULT_LAT, config.MAP_DEFAULT_LON]

        return ([lat, lon], list(buoy_dt_instance.buoy_history_gps), popup, cont, badge_style)


    @app.callback(
        Output("workflow-hint-text","children", allow_duplicate=True),
        Output("workflow-hint-text","style",    allow_duplicate=True),
        Input("live-update-interval","n_intervals"),
        prevent_initial_call="initial_duplicate",
    )
    def refresh_hint(_):
        color = _STEP_COLORS.get(sim_state.setup_step, "#8b949e")
        return sim_state.step_hint, _hint_style(color)


    @app.callback(
        Output("store-selected-river",   "data"),
        Output("selected-river-name",    "children"),
        Output("selected-river-pts",     "children"),
        Output("segment-start-slider",   "value"),
        Output("segment-end-slider",     "value"),
        Output("river-lines-group",      "children", allow_duplicate=True),
        Input({"type": "river-line", "index": ALL}, "n_clicks"),
        State("store-river-list",        "data"),
        prevent_initial_call=True,
    )
    def on_river_click(n_clicks_list, rivers):
        if not any(n_clicks_list) or not rivers:
            return (no_update,) * 6

        clicked_idx = next((i for i, n in enumerate(n_clicks_list) if n), None)
        if clicked_idx is None or clicked_idx >= len(rivers):
            return (no_update,) * 6

        rv = rivers[clicked_idx]
        polylines = []
        for i, r in enumerate(rivers):
            positions = [[c[1], c[0]] for c in r["coords"]]
            selected  = (i == clicked_idx)
            polylines.append(dl.Polyline(
                id={"type": "river-line", "index": i},
                positions=positions,
                color="#00e5ff" if selected else "#4fc3f7",
                weight=6 if selected else 4,
                opacity=1.0 if selected else 0.45,
                children=[dl.Tooltip(r["name"])],
            ))

        return (rv, rv["name"], f"{len(rv['coords'])} pts", 0, 100, polylines)


    @app.callback(
        Output("segment-preview",    "positions"),
        Output("slider-start-val",   "children"),
        Output("slider-end-val",     "children"),
        Input("segment-start-slider","value"),
        Input("segment-end-slider",  "value"),
        State("store-selected-river","data"),
        prevent_initial_call=True,
    )
    def update_preview(start_pct, end_pct, river):
        if not river:
            return [], f"{start_pct}%", f"{end_pct}%"
        trimmed   = _trim_coords(river["coords"], start_pct, end_pct)
        positions = [[c[1], c[0]] for c in trimmed]
        return positions, f"{start_pct}%", f"{end_pct}%"


    @app.callback(
        Output("river-centerline-overlay",  "positions"),
        Output("store-map-mode",            "data",     allow_duplicate=True),
        Output("btn-mode-draw",             "style",    allow_duplicate=True),
        Output("btn-mode-rivers",           "style",    allow_duplicate=True),
        Output("river-picker-panel",        "style",    allow_duplicate=True),
        Output("segment-preview",           "positions",allow_duplicate=True),
        Output("river-lines-group",         "children", allow_duplicate=True),
        Input("btn-confirm-segment",        "n_clicks"),
        State("store-selected-river",       "data"),
        State("segment-start-slider",       "value"),
        State("segment-end-slider",         "value"),
        prevent_initial_call=True,
    )
    def confirm_segment(_, river, start_pct, end_pct):
        if not river:
            return (no_update,) * 7

        trimmed = _trim_coords(river["coords"], start_pct, end_pct)
        if len(trimmed) < 2:
            return (no_update,) * 7

        gps_pts = [(c[1], c[0]) for c in trimmed]
        sim_state.river_width = float(river.get("width", 100))
        sim_state.georef.set_gps_polyline(gps_pts)
        sim_state.build_river()
        sim_state.setup_step = 2
        
        for _ in range(100):
            sim_state.dv.update()

        overlay = sim_state.get_river_overlay_gps()
        
        return (overlay,
                "draw", _toggle_btn_style("#00e5ff", active=True), _toggle_btn_style("#4fc3f7", active=False),
                _panel_style(visible=False), [], [])

    @app.callback(
        Output("river-picker-panel",  "style",    allow_duplicate=True),
        Output("store-map-mode",      "data",     allow_duplicate=True),
        Output("btn-mode-draw",       "style",    allow_duplicate=True),
        Output("btn-mode-rivers",     "style",    allow_duplicate=True),
        Output("segment-preview",     "positions",allow_duplicate=True),
        Output("river-lines-group",   "children", allow_duplicate=True),
        Input("btn-cancel-segment",   "n_clicks"),
        prevent_initial_call=True,
    )
    def cancel_segment(_):
        return (_panel_style(visible=False), "draw", _toggle_btn_style("#00e5ff", active=True),
                _toggle_btn_style("#4fc3f7", active=False), [], [])



# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------

def _toggle_btn_style(color: str, active: bool) -> dict:
    return {
        "background":    f"{color}25" if active else "rgba(8,13,20,0.88)",
        "border":        f"1.5px solid {color}" if active else "1px solid #2a3a4a",
        "color":         color if active else "#566879",
        "fontFamily":    "monospace", "fontSize": "11px",
        "fontWeight":    "600" if active else "400",
        "letterSpacing": "0.09em", "padding": "7px 16px",
        "cursor":        "pointer", "borderRadius":  "3px",
        "boxShadow":     "0 2px 8px rgba(0,0,0,0.5)", "transition": "all 0.15s",
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