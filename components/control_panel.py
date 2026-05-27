# =============================================================================
# components/control_panel.py — Left sidebar: controls + live sensor readout
# =============================================================================

from dash import html, dcc, Input, Output, State, no_update
import config

from core.global_buoy_dt import BuoyDigitalTwin, BuoyMode
from core.simulation import SimulationState

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
def _readout(label: str, value_id: str, unit: str = "", color: str = "#00e5ff"):
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
        label, id=btn_id, n_clicks=0,
        style=_btn_style(color, armed=False),
    )


# Lighter blue-tinted box for highlighted controls (mode toggle, slider, etc.)
PANEL_BOX_STYLE = {
    "background":   "#1c2a3a",
    "border":       "1px solid #2d4863",
    "borderRadius": "4px",
    "padding":      "10px 12px",
    "marginTop":    "4px",
}


def _btn_style(color: str, armed: bool = False) -> dict:
    return {
        "background": color if armed else "transparent",
        "border": f"1px solid {color}",
        "color": "#0d1117" if armed else color,
        "fontFamily": "monospace", "fontSize": "11px",
        "letterSpacing": "0.08em", "padding": "6px 12px",
        "cursor": "pointer", "borderRadius": "3px",
        "width": "100%", "marginBottom": "6px",
        "transition": "all 0.15s",
        "fontWeight": "700" if armed else "400",
    }


# ------------------------------------------------------------------
# Layout
# ------------------------------------------------------------------
def layout():
    return html.Div(
        style={
            "width": "260px", "minWidth": "260px",
            "height": "100vh", "background": "#0d1117",
            "borderRight": "1px solid #1e2a35",
            "display": "flex", "flexDirection": "column",
            "padding": "16px", "overflowY": "auto",
            "boxSizing": "border-box",
        },
        children=[

            html.Div(
                style={"marginBottom": "20px"},
                children=[
                    html.Div("BUOY", style={"color": "#00e5ff", "fontSize": "22px",
                                            "fontFamily": "monospace", "fontWeight": "700",
                                            "letterSpacing": "0.3em"}),
                    html.Div("DIGITAL TWIN — MEUSE", style={"color": "#8b949e",
                                                            "fontSize": "10px",
                                                            "fontFamily": "monospace",
                                                            "letterSpacing": "0.15em"}),
                ]
            ),

            # ---- Live sensor data ----
            _section("Live Sensor Data", [
                _readout("Temperature", "read-temperature", "°C"),
                _readout("Latitude", "read-latitude", "°", "#69f0ae"),
                _readout("Longitude", "read-longitude", "°", "#69f0ae"),
                _readout("pH", "read-sensor-ph", "", "#ff9800"),
                _readout("EC", "read-sensor-ec", "µS/cm", "#ff9800"),
                _readout("DO", "read-sensor-do", "mg/L", "#ff9800"),
                _readout("Last update", "read-last-update", "", "#8b949e"),
            ]),

            # ---- Buoy position (sim) ----
            _section("Buoy Position (local)", [
                _readout("Stream X", "read-buoy-x", "m"),
                _readout("Cross Y", "read-buoy-y", "m"),
                _readout("Sim time", "read-sim-step", "s"),
            ]),

            # ---- Mode controls ----
            _section("Mode", [
                html.Div(
                    style=PANEL_BOX_STYLE,
                    children=[
                        dcc.RadioItems(
                            id="mode-selector",
                            options=[
                                {"label": " Simulated flow",   "value": "simulated"},
                                {"label": " Real GPS (live)", "value": "real"},
                            ],
                            value="simulated",
                            style={"color": "#ffffff", "fontSize": "12px",
                                   "fontFamily": "monospace"},
                            labelStyle={"display": "block", "marginBottom": "6px",
                                        "color": "#ffffff"},
                            inputStyle={"marginRight": "6px"},
                        ),
                    ],
                ),
            ]),

            # ---- Place source / buoy / river ----
            _section("Map Placement", [
                _btn("🌊 Draw RIVER centerline", "btn-place-river", "#00e5ff"),
                _btn("📏 Draw river WIDTH",      "btn-place-width", "#00e5ff"),
                _btn("📍 Place SOURCE on map",   "btn-place-source", "#ff1744"),
                _btn("📍 Place BUOY START on map", "btn-place-buoy", "#69f0ae"),
                _btn("↺ Reset river (use default)", "btn-clear-river", "#8b949e"),
                html.Div(id="river-info",
                         style={"color": "#8b949e", "fontSize": "10px",
                                "fontFamily": "monospace", "marginTop": "4px"}),
            ]),

            # ---- Run controls ----
            _section("Run", [
                _btn("▶  START", "btn-start", "#00e5ff"),
                _btn("⏸  PAUSE", "btn-pause", "#8b949e"),
                _btn("↺  RESET BUOY", "btn-reset", "#ff9800"),
                html.Div(
                    style={**PANEL_BOX_STYLE, "marginTop": "10px"},
                    children=[
                        html.Div(
                            id="speed-label",
                            children="Speed: 1.0x  (SIM only)",
                            style={"color": "#ffffff", "fontSize": "11px",
                                   "fontFamily": "monospace",
                                   "letterSpacing": "0.05em", "marginBottom": "6px"},
                        ),
                        dcc.Slider(
                            id="speed-slider",
                            min=1, max=20, step=0.5, value=1,
                            marks={1: {"label": "1x", "style": {"color": "#cdd9e5"}},
                                   5: {"label": "5x", "style": {"color": "#cdd9e5"}},
                                   10: {"label": "10x", "style": {"color": "#cdd9e5"}},
                                   20: {"label": "20x", "style": {"color": "#cdd9e5"}}},
                            tooltip={"placement": "bottom", "always_visible": False},
                        ),
                    ],
                ),
            ]),

            # ---- Backtrack ----
            _section("Source Estimation", [
                _btn("🔍 ESTIMATE SOURCE", "btn-estimate", "#00e5ff"),
                _btn("🗑 CLEAR LOG", "btn-clear-log", "#8b949e"),
                _btn("✕ CLEAR DETECTION", "btn-clear-contam", "#8b949e"),
                html.Div(id="log-status",
                         style={"color": "#8b949e", "fontSize": "10px",
                                "fontFamily": "monospace", "marginTop": "4px"}),
            ]),

            # ---- Contamination status ----
            _section("Contamination", [
                html.Div(id="contam-status-text",
                         style={"color": "#8b949e", "fontSize": "11px",
                                "fontFamily": "monospace",
                                "whiteSpace": "pre-wrap"}),
            ]),

            html.Div(
                style={"marginTop": "auto", "paddingTop": "16px",
                       "borderTop": "1px solid #1e2a35"},
                children=[
                    html.Div("WORKFLOW", style={"color": "#8b949e", "fontSize": "9px",
                                                "fontFamily": "monospace",
                                                "letterSpacing": "0.15em",
                                                "marginBottom": "6px"}),
                    html.Ol([
                        html.Li("Place SOURCE (sim) and BUOY START on map"),
                        html.Li("Press START"),
                        html.Li("After the buoy drifts through plume → press ESTIMATE SOURCE"),
                        html.Li("Run again from a new buoy start to sharpen the estimate"),
                    ], style={"color": "#8b949e", "fontSize": "10px",
                              "fontFamily": "monospace", "paddingLeft": "16px",
                              "lineHeight": "1.6"}),
                ]
            ),
        ]
    )


# ------------------------------------------------------------------
# Callbacks
# ------------------------------------------------------------------
def register_callbacks(app, sim_state: SimulationState, buoy_dt_instance: BuoyDigitalTwin):

    # ---- Live readouts ----
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
        Output("log-status", "children"),
        Input("live-update-interval", "n_intervals"),
    )
    def update_readouts(_):
        import datetime
        s = buoy_dt_instance.sensor.data

        temp = s.formatted_temp
        lat  = s.formatted_lat
        lon  = s.formatted_lon
        ph   = s.formatted_ph
        ec   = s.formatted_ec
        do_  = s.formatted_do

        if buoy_dt_instance.comm_dt.last_update:
            last = datetime.datetime.fromtimestamp(buoy_dt_instance.comm_dt.last_update).strftime("%H:%M:%S")
        else:
            last = "never"

        bx   = buoy_dt_instance.formatted_local_x
        by   = buoy_dt_instance.formatted_local_y
        step = f"{sim_state.sim_time:.0f}"

        if sim_state.contamination_detected:
            sev = sim_state.contamination_severity or "?"
            rules = "\n  • " + "\n  • ".join(sim_state.contamination_rules_hit[:4])
            contam = f"Severity: {sev.upper()}\nRules hit:{rules}"
        else:
            contam = "No contamination detected"

        log = f"log: {len(sim_state.measurement_log)} samples"
        if sim_state.backtrack_map is not None:
            log += " | backtrack: ready"

        return (temp, lat, lon, ph, ec, do_, last,
                bx, by, step, contam, log)

    # ---- Mode selector — actually swap the model ----
    @app.callback(
        Output("mode-selector", "value"),
        Input("mode-selector", "value"),
        prevent_initial_call=True,
    )
    def on_mode_change(mode):
        try:
            new_mode = BuoyMode(mode)
        except ValueError:
            new_mode = BuoyMode.SIM
        sim_state.mode = new_mode
        try:
            buoy_dt_instance.set_mode(new_mode)
        except Exception as e:
            print(f"[control_panel] set_mode error: {e}")
        return mode

    # ---- Placement arm buttons ----
    @app.callback(
        Output("btn-place-source",   "style"),
        Output("btn-place-buoy",     "style"),
        Output("btn-place-river",    "style"),
        Output("btn-place-width",    "style"),
        Output("draw-trigger-store", "data"),
        Input("btn-place-source",    "n_clicks"),
        Input("btn-place-buoy",      "n_clicks"),
        Input("btn-place-river",     "n_clicks"),
        Input("btn-place-width",     "n_clicks"),
        Input("live-update-interval", "n_intervals"),
        State("draw-trigger-store",  "data"),
        prevent_initial_call=True,
    )
    def on_place_click(_src, _buoy, _river, _width, _tick, draw_counter):
        from dash import ctx
        trig = ctx.triggered_id
        draw_bump = no_update

        def _toggle(mode):
            was = (sim_state.placement_mode == mode)
            sim_state.set_placement_mode(None if was else mode)
            return not was   # True if newly armed

        if trig == "btn-place-source":
            _toggle(sim_state.PLACE_SOURCE)
        elif trig == "btn-place-buoy":
            _toggle(sim_state.PLACE_BUOY)
        elif trig == "btn-place-river":
            if _toggle(sim_state.PLACE_RIVER):
                draw_bump = (draw_counter or 0) + 1
                sim_state.set_toast(
                    "Click points along the river (upstream -> downstream), double-click to finish.",
                    "#00e5ff", duration_s=10.0,
                )
        elif trig == "btn-place-width":
            if _toggle(sim_state.PLACE_WIDTH):
                draw_bump = (draw_counter or 0) + 1
                sim_state.set_toast(
                    "Draw a 2-point line ACROSS the river, double-click to finish.",
                    "#00e5ff", duration_s=10.0,
                )

        src_armed   = (sim_state.placement_mode == sim_state.PLACE_SOURCE)
        buoy_armed  = (sim_state.placement_mode == sim_state.PLACE_BUOY)
        river_armed = (sim_state.placement_mode == sim_state.PLACE_RIVER)
        width_armed = (sim_state.placement_mode == sim_state.PLACE_WIDTH)
        return (_btn_style("#ff1744", src_armed),
                _btn_style("#69f0ae", buoy_armed),
                _btn_style("#00e5ff", river_armed),
                _btn_style("#00e5ff", width_armed),
                draw_bump)

    # ---- River info readout (centerline points + width) ----
    @app.callback(
        Output("river-info", "children"),
        Input("live-update-interval", "n_intervals"),
    )
    def update_river_info(_):
        pts = len(sim_state.river.xc) if sim_state.river is not None else 0
        return f"river: {pts} pts | width: {sim_state.river_width:.1f} m"

    @app.callback(
        Output("btn-clear-river", "n_clicks"),
        Input("btn-clear-river", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_clear_river(n):
        sim_state.clear_drawn_centerline()
        return n

    # ---- Speed slider ----
    @app.callback(
        Output("speed-label", "children"),
        Input("speed-slider", "value"),
    )
    def on_speed_change(value):
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 1.0
        sim_state.speed_multiplier = v
        suffix = "" if sim_state.mode == BuoyMode.SIM else "  (REAL: locked to 1x)"
        return f"Speed: {v:.1f}x  (SIM only){suffix}"

    # ---- Run / pause / reset ----
    @app.callback(
        Output("btn-start", "n_clicks"),
        Input("btn-start", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_start(n):
        sim_state.running = True
        sim_state._last_tick_t = 0.0   # restart real-time clock
        return n

    @app.callback(
        Output("btn-pause", "n_clicks"),
        Input("btn-pause", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_pause(n):
        sim_state.running = False
        return n

    @app.callback(
        Output("btn-reset", "n_clicks"),
        Input("btn-reset", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_reset(n):
        sim_state.reset_buoy()
        return n

    # ---- Source estimation ----
    @app.callback(
        Output("btn-estimate", "n_clicks"),
        Input("btn-estimate", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_estimate(n):
        sim_state.estimate_source()
        return n

    @app.callback(
        Output("btn-clear-log", "n_clicks"),
        Input("btn-clear-log", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_clear_log(n):
        sim_state.clear_log()
        return n

    @app.callback(
        Output("btn-clear-contam", "n_clicks"),
        Input("btn-clear-contam", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_clear_contam(n):
        sim_state.reset_contamination()
        return n
