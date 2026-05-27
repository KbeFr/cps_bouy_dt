# =============================================================================
# app.py — Buoy Digital Twin — Main Dash Application
# =============================================================================
#

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import dash
from dash import html, dcc, Input, Output

# --- Core singletons (created once, shared across callbacks) ---
from core.simulation import SimulationState
from core.global_buoy_dt import BuoyDigitalTwin , BuoyMode


buoy_dt = BuoyDigitalTwin()
sim_state = SimulationState(buoy_dt)
sim_state.start_sim_thread()

# --- Import panels (after singletons so callbacks can close over them) ---
from components import control_panel, map_panel, river_panel


# =============================================================================
# App init
# =============================================================================

app = dash.Dash(
    __name__,
    suppress_callback_exceptions=True,
    title="Buoy Digital Twin",
    external_stylesheets=[],   # no Bootstrap — custom only
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
server = app.server   # expose for gunicorn / deployment


# =============================================================================
# Layout
# =============================================================================

app.layout = html.Div(
    style={
        "display":        "flex",
        "flexDirection":  "row",
        "height":         "100vh",
        "width":          "100vw",
        "overflow":       "hidden",
        "background":     "#0d1117",
        "fontFamily":     "monospace",
    },
    children=[

        # ---- Left sidebar ----
        control_panel.layout(),

        # ---- Centre column (satellite map) ----
        html.Div(
            style={"flex": "1 1 0", "height": "100vh", "position": "relative",
                   "borderRight": "1px solid #1e2a35"},
            children=[
                # Header
                html.Div(
                    style={"height": "36px", "background": "#111820",
                           "borderBottom": "1px solid #1e2a35",
                           "display": "flex", "alignItems": "center",
                           "padding": "0 12px", "gap": "8px"},
                    children=[
                        html.Span("◉", style={"color": "#00e5ff", "fontSize": "10px"}),
                        html.Span("SATELLITE VIEW", style={"color": "#8b949e",
                                                            "fontSize": "10px",
                                                            "letterSpacing": "0.15em"}),
                        html.Span(
                            "",
                            style={"color": "#3d5166", "fontSize": "10px",
                                   "marginLeft": "auto"},
                        ),
                    ]
                ),
                # Map (fills remaining height)
                html.Div(
                    style={"height": "calc(100vh - 36px)"},
                    children=[map_panel.layout()]
                ),
            ]
        ),

        # ----  Right column (river model) ----
        html.Div(
            style={"flex": "1 1 0", "height": "100vh", "display": "flex",
                   "flexDirection": "column"},
            children=[
                # Header
                html.Div(
                    style={"height": "36px", "background": "#111820",
                           "borderBottom": "1px solid #1e2a35",
                           "display": "flex", "alignItems": "center",
                           "padding": "0 12px", "gap": "8px"},
                    children=[
                        html.Span("◈", style={"color": "#69f0ae", "fontSize": "10px"}),
                        html.Span("RIVER MODEL", style={"color": "#8b949e",
                                                         "fontSize": "10px",
                                                         "letterSpacing": "0.15em"}),
                        html.Span(
                            id="sim-step-header",
                            style={"color": "#3d5166", "fontSize": "10px",
                                   "marginLeft": "auto", "fontFamily": "monospace"},
                        ),
                    ]
                ),
                # Plots
                html.Div(
                    style={"flex": "1", "padding": "8px", "overflow": "hidden"},
                    children=[river_panel.layout()]
                ),
            ]
        ),

        # ---- Global interval — drives all live updates ----
        dcc.Interval(
            id="live-update-interval",
            interval=1000,    # ms — 1 second update rate
            n_intervals=0,
        ),

        # ---- Simulation step interval — drives physics ----
        dcc.Interval(
            id="sim-step-interval",
            interval=500,     # ms — physics ticks every 0.5s
            n_intervals=0,
        ),
    ]
)


# =============================================================================
# Top-level callbacks
# =============================================================================

@app.callback(
    Output("sim-step-header", "children"),
    Input("sim-step-interval", "n_intervals"),
)
def tick_simulation(_):
    mode = "SIM" if sim_state.mode == BuoyMode.SIM else "REAL"
    return f"t={sim_state.sim_time}  |  mode={mode}  |  {'▶ RUNNING' if sim_state.running else '⏸ PAUSED'}"


# =============================================================================
# Register panel callbacks
# =============================================================================

control_panel.register_callbacks(app, sim_state, buoy_dt)
map_panel.register_callbacks(app, sim_state,buoy_dt)
river_panel.register_callbacks(app, sim_state, buoy_dt)


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8050, threaded=True)
