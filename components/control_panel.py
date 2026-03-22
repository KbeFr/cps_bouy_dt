# =============================================================================
# components/control_panel.py — Left sidebar: controls + live sensor readout
# =============================================================================

import dash_bootstrap_components as dbc
from dash import html, dcc, Input, Output, State
import config


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _readout(label: str, value_id: str, unit: str = "", color: str = "#00e5ff"):
    """Single data readout row."""
    return html.Div(
        style={"display": "flex", "justifyContent": "space-between",
               "alignItems": "baseline", "padding": "4px 0",
               "borderBottom": "1px solid #1e2a35"},
        children=[
            html.Span(label, style={"color": "#8b949e", "fontSize": "11px",
                                     "fontFamily": "monospace", "letterSpacing": "0.05em"}),
            html.Span([
                html.Span(id=value_id, style={"color": color, "fontSize": "15px",
                                               "fontFamily": "monospace", "fontWeight": "700"}),
                html.Span(f" {unit}", style={"color": "#8b949e", "fontSize": "10px"}),
            ]),
        ]
    )


def _section(title: str, children: list):
    """Titled section card."""
    return html.Div(
        style={"marginBottom": "16px"},
        children=[
            html.Div(title, style={
                "color": "#8b949e", "fontSize": "10px", "fontFamily": "monospace",
                "letterSpacing": "0.15em", "textTransform": "uppercase",
                "borderBottom": "1px solid #1e2a35", "paddingBottom": "4px",
                "marginBottom": "8px",
            }),
            *children,
        ]
    )


def _btn(label: str, btn_id: str, color: str = "#00e5ff"):
    return html.Button(
        label,
        id=btn_id,
        style={
            "background":    "transparent",
            "border":        f"1px solid {color}",
            "color":         color,
            "fontFamily":    "monospace",
            "fontSize":      "11px",
            "letterSpacing": "0.08em",
            "padding":       "6px 12px",
            "cursor":        "pointer",
            "borderRadius":  "3px",
            "width":         "100%",
            "marginBottom":  "6px",
            "transition":    "all 0.15s",
        }
    )


# ------------------------------------------------------------------
# Layout
# ------------------------------------------------------------------

def layout():
    return html.Div(
        style={
            "width":          "240px",
            "minWidth":       "240px",
            "height":         "100vh",
            "background":     "#0d1117",
            "borderRight":    "1px solid #1e2a35",
            "display":        "flex",
            "flexDirection":  "column",
            "padding":        "16px",
            "overflowY":      "auto",
            "boxSizing":      "border-box",
        },
        children=[

            # ---- Logo / title ----
            html.Div(
                style={"marginBottom": "24px"},
                children=[
                    html.Div("BUOY", style={"color": "#00e5ff", "fontSize": "22px",
                                             "fontFamily": "monospace", "fontWeight": "700",
                                             "letterSpacing": "0.3em"}),
                    html.Div("DIGITAL TWIN", style={"color": "#8b949e", "fontSize": "10px",
                                                      "fontFamily": "monospace",
                                                      "letterSpacing": "0.2em"}),
                ]
            ),

            # ---- Live sensor data ----
            _section("Live Sensor Data", [
                _readout("Temperature",  "read-temperature",  "°C"),
                _readout("Latitude",     "read-latitude",     "°",  "#69f0ae"),
                _readout("Longitude",    "read-longitude",    "°",  "#69f0ae"),
                _readout("Sensor pH",      "read-sensor-ph",      "",   "#ff9800"),
                _readout("Sensor EC",      "read-sensor-ec",      "",   "#ff9800"),
                _readout("Sensor DO",      "read-sensor-do",      "",   "#ff9800"),
                _readout("Last update",  "read-last-update",  "",   "#8b949e"),
            ]),

            # ---- Buoy position (sim) ----
            _section("Buoy Position (local)", [
                _readout("Stream X",  "read-buoy-x",  "m"),
                _readout("Cross Y",   "read-buoy-y",  "m"),
                _readout("Sim step",  "read-sim-step", ""),
            ]),

            # ---- Mode controls ----
            _section("Simulation Mode", [
                dcc.RadioItems(
                    id="mode-selector",
                    options=[
                        {"label": " Simulated flow", "value": "simulated" , "style":{"color": "#4898e7"}},
                        {"label": " Real GPS",        "value": "real" ,"style":{"color": "#4898e7"}},
                    ],
                    value="simulated",
                    style={"color": "#f9fafb", "fontSize": "12px",
                           "fontFamily": "monospace"},
                    labelStyle={"display": "block", "marginBottom": "4px"},
                ),
            ]),

            # ---- Sim controls ----
            _section("Controls", [
                _btn("▶  START",    "btn-start",    "#00e5ff"),
                _btn("⏸  PAUSE",    "btn-pause",    "#8b949e"),
                _btn("↺  RESET BUOY",    "btn-reset",    "#ff9800"),
                _btn("⚠  INJECT CONTAMINATION", "btn-inject", "#ff1744"),
                _btn("✕  CLEAR CONTAMINATION",  "btn-clear-contam", "#8b949e"),
                _btn("⟳  RESET ALL", "btn-reset-all", "#ff1744"),
            ]),

            # ---- Contamination status ----
            _section("Contamination", [
                html.Div(id="contam-status-text",
                         style={"color": "#8b949e", "fontSize": "11px",
                                "fontFamily": "monospace"}),
            ]),

            # ---- Instructions ----
            html.Div(
                style={"marginTop": "auto", "paddingTop": "16px",
                       "borderTop": "1px solid #1e2a35"},
                children=[
                    html.Div("HOW TO START", style={"color": "#8b949e", "fontSize": "9px",
                                                      "fontFamily": "monospace",
                                                      "letterSpacing": "0.15em",
                                                      "marginBottom": "6px"}),
                    html.Ol([
                        html.Li("Draw river on satellite map"),
                        html.Li("Click START"),
                        html.Li("Inject contamination to test backtracking"),
                    ], style={"color": "#8b949e", "fontSize": "10px",
                              "fontFamily": "monospace", "paddingLeft": "16px",
                              "lineHeight": "1.8"}),
                ]
            ),
        ]
    )


# ------------------------------------------------------------------
# Callbacks
# ------------------------------------------------------------------

def register_callbacks(app, sim_state, buoy_dt_instance):
    @app.callback(
        Output("read-temperature",  "children"),
        Output("read-latitude",     "children"),
        Output("read-longitude",    "children"),
        Output("read-sensor-ph",      "children"),
        Output("read-sensor-ec",      "children"),
        Output("read-sensor-do",      "children"),
        Output("read-last-update",  "children"),
        Output("read-buoy-x",       "children"),
        Output("read-buoy-y",       "children"),
        Output("read-sim-step",     "children"),
        Output("contam-status-text","children"),
        Input("live-update-interval", "n_intervals"),
    )
    def update_readouts(_):
        import time, datetime
        s = sim_state.sensor

        temp = f"{s.get('temperature', '--'):.2f}" if s.get('temperature') is not None else "--"
        lat  = f"{s.get('latitude',    '--'):.6f}" if s.get('latitude')    is not None else "--"
        lon  = f"{s.get('longitude',   '--'):.6f}" if s.get('longitude')   is not None else "--"


        # UPDATE this (ph,ec,do) to right topics name for the sensor readings 
        ph  = f"{s.get('ph',   '--'):.6f}" if s.get('ph')   is not None else "--"
        ec  = f"{s.get('ec',   '--'):.6f}" if s.get('ec')   is not None else "--"
        do  = f"{s.get('do',   '--'):.6f}" if s.get('do')   is not None else "--"



        if buoy_dt_instance.last_update:
            dt   = datetime.datetime.fromtimestamp(buoy_dt_instance.last_update)
            last = dt.strftime("%H:%M:%S")
        else:
            last = "never"

        bx   = f"{sim_state.buoy_local_x:.1f}"
        by   = f"{sim_state.buoy_local_y:.1f}"
        step = str(sim_state.sim_time)

        if sim_state.contamination_detected:
            cx, cy = sim_state.contamination_local
            contam = f"⚠ Detected at step {sim_state.sim_time}\n" \
                     f"Local: ({cx:.1f}, {cy:.1f})\n" \
                     f"Backtrack: {'ready' if sim_state.backtrack_map is not None else 'running...'}"
        else:
            contam = "No contamination detected"

        return temp, lat, lon, ph,ec,do, last, bx, by, step, contam


    # ---- Start / Pause / Reset ----

    @app.callback(
        Output("btn-start", "style"),   # just to have an output
        Input("btn-start", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_start(_):
        sim_state.running = True
        return {"background": "transparent", "border": "1px solid #00e5ff",
                "color": "#00e5ff", "fontFamily": "monospace", "fontSize": "11px",
                "letterSpacing": "0.08em", "padding": "6px 12px", "cursor": "pointer",
                "borderRadius": "3px", "width": "100%", "marginBottom": "6px"}

    @app.callback(
        Output("btn-pause", "style"),
        Input("btn-pause", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_pause(_):
        sim_state.running = False
        return {"background": "transparent", "border": "1px solid #8b949e",
                "color": "#8b949e", "fontFamily": "monospace", "fontSize": "11px",
                "letterSpacing": "0.08em", "padding": "6px 12px", "cursor": "pointer",
                "borderRadius": "3px", "width": "100%", "marginBottom": "6px"}

    @app.callback(
        Output("btn-reset", "style"),
        Input("btn-reset", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_reset(_):
        sim_state.reset_buoy()
        sim_state.reset_contamination()
        return {"background": "transparent", "border": "1px solid #ff9800",
                "color": "#ff9800", "fontFamily": "monospace", "fontSize": "11px",
                "letterSpacing": "0.08em", "padding": "6px 12px", "cursor": "pointer",
                "borderRadius": "3px", "width": "100%", "marginBottom": "6px"}

    @app.callback(
        Output("btn-inject", "style"),
        Input("btn-inject", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_inject(_):
        """Manually inject contamination at the buoy's current position."""
        sim_state._check_contamination(True)
        return {"background": "transparent", "border": "1px solid #ff1744",
                "color": "#ff1744", "fontFamily": "monospace", "fontSize": "11px",
                "letterSpacing": "0.08em", "padding": "6px 12px", "cursor": "pointer",
                "borderRadius": "3px", "width": "100%", "marginBottom": "6px"}

    @app.callback(
        Output("btn-clear-contam", "style"),
        Input("btn-clear-contam", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_clear_contam(_):
        sim_state.reset_contamination()
        return {"background": "transparent", "border": "1px solid #8b949e",
                "color": "#8b949e", "fontFamily": "monospace", "fontSize": "11px",
                "letterSpacing": "0.08em", "padding": "6px 12px", "cursor": "pointer",
                "borderRadius": "3px", "width": "100%", "marginBottom": "6px"}

    @app.callback(
        Output("btn-reset-all", "style"),
        Input("btn-reset-all", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_reset_all(_):
        """Wipe everything — back to step 0, river/width/path/buoy start cleared."""
        sim_state.running          = False
        sim_state.setup_step       = 0
        sim_state.river_width      = None
        sim_state.river            = None
        sim_state.plume            = None
        sim_state.dv               = None
        sim_state.georef           = sim_state.georef.__class__()   # fresh GeoReference
        sim_state.buoy_local_x     = 0.0
        sim_state.buoy_local_y     = 0.0
        sim_state.buoy_start_local = (0.0, 0.0)
        sim_state.buoy_gps_lat     = config.MAP_DEFAULT_LAT
        sim_state.buoy_gps_lon     = config.MAP_DEFAULT_LON
        sim_state.buoy_history_gps = []
        sim_state.sim_time         = 0
        sim_state.reset_contamination()
        return {"background": "transparent", "border": "1px solid #ff1744",
                "color": "#ff1744", "fontFamily": "monospace", "fontSize": "11px",
                "letterSpacing": "0.08em", "padding": "6px 12px", "cursor": "pointer",
                "borderRadius": "3px", "width": "100%", "marginBottom": "6px"}

    @app.callback(
        Output("mode-selector", "value"),
        Input("mode-selector", "value"),
    )
    def on_mode_change(mode):
        sim_state.mode = mode
        return mode