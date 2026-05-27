# =============================================================================
# components/control_panel.py — Left sidebar: controls + live sensor readout
# =============================================================================

from dash import html, dcc, Input, Output, State, no_update
import datetime
import numpy as np

from core.global_buoy_dt import BuoyDigitalTwin
from core.simulation import SimulationState
from core.global_buoy_dt import BuoyMode

from core.river_model.river_config import (
    RiverConfig, save_config, load_config, list_saved
)

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
            "background": "transparent",
            "border": f"1px solid {color}",
            "color": color,
            "fontFamily": "monospace",
            "fontSize": "11px",
            "letterSpacing": "0.08em",
            "padding": "6px 12px",
            "cursor": "pointer",
            "borderRadius": "3px",
            "width": "100%",
            "marginBottom": "6px",
            "transition": "all 0.15s",
        }
    )


# ------------------------------------------------------------------
# Layout
# ------------------------------------------------------------------

def layout():
    return html.Div(
        style={
            "width": "240px",
            "minWidth": "240px",
            "height": "100vh",
            "background": "#0d1117",
            "borderRight": "1px solid #1e2a35",
            "display": "flex",
            "flexDirection": "column",
            "padding": "16px",
            "overflowY": "auto",
            "boxSizing": "border-box",
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
                _readout("Temperature", "read-temperature", "°C"),
                _readout("Latitude", "read-latitude", "°", "#69f0ae"),
                _readout("Longitude", "read-longitude", "°", "#69f0ae"),
                _readout("Sensor pH", "read-sensor-ph", "", "#ff9800"),
                _readout("Sensor EC", "read-sensor-ec", "", "#ff9800"),
                _readout("Sensor DO", "read-sensor-do", "", "#ff9800"),
                _readout("Last update", "read-last-update", "", "#8b949e"),
            ]),

            # ---- Buoy position (sim) ----
            _section("Buoy Position (local)", [
                _readout("Stream X", "read-buoy-x", "m"),
                _readout("Cross Y", "read-buoy-y", "m"),
                _readout("Sim step", "read-sim-step", ""),
            ]),

            # ---- Mode controls ----
            _section("Simulation Mode", [
                dcc.RadioItems(
                    id="mode-selector",
                    options=[
                        {"label": " Simulated flow", "value": "simulated", "style": {"color": "#ffffff"}},
                        {"label": " Real GPS", "value": "real", "style": {"color": "#ffffff"}},
                    ],
                    value="simulated",
                    style={"color": "#FFFFFF", "fontSize": "12px",
                           "fontFamily": "monospace", "opacity": "1"},
                    labelStyle={"display": "block", "marginBottom": "4px"},
                ),
            ]),

            # ---- Sim controls ----
            _section("Controls", [
                _btn("▶  START", "btn-start", "#00e5ff"),
                _btn("⏸  PAUSE", "btn-pause", "#8b949e"),
                _btn("↺  RESET BUOY", "btn-reset", "#ff9800"),
                _btn("⚠  INJECT CONTAMINATION", "btn-inject", "#ff1744"),
                _btn("✕  CLEAR CONTAMINATION", "btn-clear-contam", "#8b949e"),
                _btn("⟳  RESET ALL", "btn-reset-all", "#ff1744"),
            ]),

            # ---- Contamination status ----
            _section("Contamination", [
                _btn("PLACE SOURCE", "btn-place-source", "#b388ff"),
                html.Div(id="contam-status-text",
                         style={"color": "#8b949e", "fontSize": "11px",
                                "fontFamily": "monospace"}),
            ]),

            # ---- Commands ----
            _section("Active Command", [
                _readout("Heading (sim)", "read-cmd-heading", "deg", "#ff9800"),
                _readout("Thrust",        "read-cmd-thrust",  "",    "#ff9800"),
                _readout("Reason",        "read-cmd-reason",  "",    "#8b949e"),
                ]),

            # ── Save / Load river ──────────────────────────────────────
            _section("River Profile", [
                html.Div(style={"display":"flex","gap":"5px","marginBottom":"6px"}, children=[
                    dcc.Input(id="river-name-input", type="text", placeholder="Profile name…",
                              style={"flex":"1","background":"#0d1520","border":"1px solid #1e2a35",
                                     "color":"#cdd9e5","fontFamily":"monospace","fontSize":"10px",
                                     "padding":"4px 6px","borderRadius":"2px"}),
                    html.Button("SAVE", id="btn-save-river",
                                style={"background":"transparent","border":"1px solid #1e4a35",
                                       "color":"#69f0ae","fontFamily":"monospace","fontSize":"9px",
                                       "padding":"4px 7px","cursor":"pointer","borderRadius":"2px"}),
                ]),
                dcc.Dropdown(
                    id="river-load-dropdown",
                    placeholder="Load saved profile…",
                    options=[],
                    style={"marginBottom":"6px"},
                    className="dt-dropdown",
                ),
                html.Button("⊕  LOAD SELECTED", id="btn-load-river",
                            style={"background":"transparent","border":"1px solid #1e3a55",
                                   "color":"#4fc3f7","fontFamily":"monospace","fontSize":"9px",
                                   "padding":"4px 8px","cursor":"pointer","borderRadius":"2px",
                                   "width":"100%","marginBottom":"4px","textAlign":"left"}),
            ]),

            # ── Notification area ──────────────────────────────────────
            html.Div(id="notification-bar", style={
                "marginTop":"auto","padding":"6px 8px","borderRadius":"2px",
                "fontFamily":"monospace","fontSize":"9px","display":"none",
            }),

        ]
    )


# ------------------------------------------------------------------
# Callbacks
# ------------------------------------------------------------------

def register_callbacks(app, sim_state : SimulationState, buoy_dt_instance : BuoyDigitalTwin):
    @app.callback(
        Output("read-temperature", "children"),
        Output("read-latitude", "children"),
        Output("read-longitude", "children"),
        Output("read-sensor-ph", "children"),
        Output("read-sensor-ec", "children"),
        Output("read-sensor-do", "children"),
        Output("read-last-update", "children"),
        Output("read-buoy-x", "children"),
        Output("read-buoy-y", "children"),
        Output("read-sim-step", "children"),
        Output("contam-status-text", "children"),
        Output("read-cmd-heading","children"),
        Output("read-cmd-thrust","children"),
        Output("read-cmd-reason","children"),
        Input("live-update-interval", "n_intervals"),
    )
    def update_readouts(_):

        sensor_data = buoy_dt_instance.sensor.data

        temp = sensor_data.formatted_temp
        lat = sensor_data.formatted_lat
        lon = sensor_data.formatted_lon

        ph = sensor_data.formatted_ph
        ec = sensor_data.formatted_ec
        do = sensor_data.formatted_do


        if buoy_dt_instance.comm_dt.last_update:
            dt = datetime.datetime.fromtimestamp(buoy_dt_instance.comm_dt.last_update)
            last = dt.strftime("%H:%M:%S")
        else:
            last = "never"

        bx = sim_state.buoy_dt.formatted_local_x     
        by = sim_state.buoy_dt.formatted_local_y      
        step = str(sim_state.sim_time)

        if sim_state.contamination_detected:
            cx, cy = sim_state.contamination_local
            contam = f"⚠ Detected on {sim_state.contamination_ts}\n" \
                     f"Local: ({cx:.1f}, {cy:.1f})\n" \
                     f"Backtrack: {'ready' if sim_state.backtrack_map is not None else 'running...'}"
        else:
            contam = "No contamination detected"

        cmd = sim_state._last_cmd
        cmd_heading = f"{np.degrees(cmd.heading_sim):.1f}" if cmd else "--"
        cmd_thrust  = f"{cmd.thrust:.2f}"      if cmd else "--"
        cmd_reason  = cmd.reason               if cmd else "--"

        
        return temp, lat, lon, ph, ec, do, last, bx, by, step, contam , cmd_heading, cmd_thrust , cmd_reason



    @app.callback(
        Output("btn-place-source", "style"),
        Input("btn-place-source", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_place_source(_):
        """Triggers the map into marker-placement mode for a new source."""
        sim_state.setup_step = 4
        return {"background": "transparent", "border": "1px solid #b388ff",
                "color": "#b388ff", "fontFamily": "monospace", "fontSize": "11px",
                "letterSpacing": "0.08em", "padding": "6px 12px", "cursor": "pointer",
                "borderRadius": "3px", "width": "100%", "marginBottom": "6px"}
    
    # ---- Start / Pause / Reset ----

    @app.callback(
        Output("btn-start", "style"),  # just to have an output
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
        Output("river-centerline-overlay", "positions", allow_duplicate=True),
        Input("btn-reset-all", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_reset_all(_):
        """Wipe everything — back to step 0, river/width/path/buoy start cleared."""
        sim_state.running = False
        sim_state.setup_step = 0
        sim_state.river_width = None
        sim_state.river = None
        sim_state.plume = None
        sim_state.dv = None
        sim_state.georef = sim_state.georef.__class__()  # fresh GeoReference
        sim_state.sim_time = 0
        sim_state.reset_contamination()

        buoy_dt_instance.hard_reset()

        return ({"background": "transparent", "border": "1px solid #ff1744",
                "color": "#ff1744", "fontFamily": "monospace", "fontSize": "11px",
                "letterSpacing": "0.08em", "padding": "6px 12px", "cursor": "pointer",
                "borderRadius": "3px", "width": "100%", "marginBottom": "6px"} , [])

    @app.callback(
        Output("mode-selector", "value"),
        Input("mode-selector", "value"),
    )
    def on_mode_change(mode):
        new_mode = BuoyMode(mode)
        sim_state.mode = new_mode
        sim_state.buoy_dt.set_mode(new_mode)   
        return mode


    # ── Save river ─────────────────────────────────────────────────────
    @app.callback(
        Output("notification-bar", "children", allow_duplicate=True),
        Output("notification-bar", "style",    allow_duplicate=True),
        Input("btn-save-river", "n_clicks"),
        State("river-name-input", "value"),
        prevent_initial_call=True,
    )
    def on_save_river(_, name):
        if not sim_state.georef._is_set or not sim_state.georef.gps_points:
            return ("No river drawn yet — draw the centreline first",
                    {"marginTop":"auto","padding":"6px 8px","borderRadius":"2px","fontFamily":"monospace",
                     "fontSize":"9px","display":"block","background":"#301010",
                     "border":"1px solid #ff174440","color":"#ff9800"})
        cfg = RiverConfig(
            name=name or "unnamed_river",
            width_m=sim_state.river_width or 80.0,
            gps_polyline=sim_state.georef.gps_points,
            source="drawn",
        )
        path = save_config(cfg)
        return (f"Saved: {cfg.name}",
                {"marginTop":"auto","padding":"6px 8px","borderRadius":"2px","fontFamily":"monospace",
                 "fontSize":"9px","display":"block","background":"#0d3020",
                 "border":"1px solid #69f0ae40","color":"#69f0ae"})
    

    # ── Populate load dropdown ─────────────────────────────────────────
    @app.callback(
        Output("river-load-dropdown", "options"),
        Input("live-update-interval", "n_intervals"),
    )
    def refresh_presets(_):
        saved = list_saved()
        return [{"label": f"{s['label']}  ({s['n_pts']} pts, {s['width_m']:.0f}m)",
                 "value": s["value"]} for s in saved]

    # ── Load selected river ────────────────────────────────────────────
    @app.callback(
        Output("notification-bar", "children", allow_duplicate=True),
        Output("notification-bar", "style",    allow_duplicate=True),
        Output("river-centerline-overlay", "positions", allow_duplicate=True),
        Input("btn-load-river", "n_clicks"),
        State("river-load-dropdown", "value"),
        prevent_initial_call=True,
    )
    def on_load_river(_, path):
        if not path:
            return "Select a profile from the dropdown first", no_update, no_update
        try:
            cfg = load_config(path)
            sim_state.river_width = cfg.width_m
            sim_state.georef.set_gps_polyline(cfg.gps_polyline)
            sim_state.build_river()
            sim_state.setup_step = 2
            
            overlay_positions = sim_state.get_river_overlay_gps()

            return (f"Loaded: {cfg.name}",
                    {"marginTop":"auto","padding":"6px 8px","borderRadius":"2px","fontFamily":"monospace",
                     "fontSize":"9px","display":"block","background":"#0d3020",
                     "border":"1px solid #69f0ae40","color":"#69f0ae"}, 
                     overlay_positions
                    )
        except Exception as e:
            return (f"Load error: {e}",
                    {"marginTop":"auto","padding":"6px 8px","borderRadius":"2px","fontFamily":"monospace",
                     "fontSize":"9px","display":"block","background":"#301010",
                     "border":"1px solid #ff174440","color":"#ff9800"},
                     no_update
                     )
