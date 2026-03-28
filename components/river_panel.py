# =============================================================================
# components/river_panel.py — River model visualization panel (Plotly)
# =============================================================================
#
# The river model lives in a curvilinear grid:
#   river.vis_x, river.vis_y  shape (N_stream, N_width) — actual XY coords
#   concentration_map          shape (N_stream, N_width) — values at those coords
#
# matplotlib's pcolormesh handles irregular grids natively.
# Plotly does NOT — go.Heatmap requires a regular grid.
#
# Solution: flatten vis_x/vis_y/values into scatter points and colour them.
# go.Scattergl (WebGL) handles 100k+ points efficiently in the browser.
# Each point gets a marker sized to roughly fill the cell (no gaps visible).
#
# Two display modes (toggled by a radio button in the panel):
#   "scatter"  — faithful curvilinear rendering (vis_x / vis_y coords)
#   "logical"  — fast regular heatmap in logical (stream index, width index) space

import numpy as np
import plotly.graph_objects as go
from dash import html, dcc, Input, Output
import config


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _empty_fig(title: str = "Waiting for river model...") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        template      = "plotly_dark",
        paper_bgcolor = "#0d1117",
        plot_bgcolor  = "#0d1117",
        title         = dict(text=title, font=dict(color="#8b949e", size=12,
                                                    family="monospace")),
        margin = dict(l=10, r=10, t=36, b=10),
    )
    return fig


def _bank_traces(river) -> list:
    """Left and right bank lines as Scattergl traces."""
    return [
        go.Scattergl(
            x=river.vis_x[:, 0],   y=river.vis_y[:, 0],
            mode="lines", line=dict(color="#37474f", width=1.5),
            showlegend=False, hoverinfo="skip",
        ),
        go.Scattergl(
            x=river.vis_x[:, -1],  y=river.vis_y[:, -1],
            mode="lines", line=dict(color="#37474f", width=1.5),
            showlegend=False, hoverinfo="skip",
        ),
    ]


def _scatter_field(vis_x, vis_y, values, colorscale, zmin, zmax,
                   marker_size: int = 4, name: str = "") -> go.Scattergl:
    """
    Replicate pcolormesh behaviour using WebGL scatter.

    vis_x, vis_y, values are all (N_stream, N_width) arrays.
    Flattened into 1-D for Plotly.
    marker_size should be tuned so markers just touch (depends on zoom).
    """
    x_flat = vis_x.flatten()
    y_flat = vis_y.flatten()
    v_flat = values.flatten()

    return go.Scattergl(
        x    = x_flat,
        y    = y_flat,
        mode = "markers",
        marker = dict(
            color      = v_flat,
            colorscale = colorscale,
            cmin       = zmin,
            cmax       = zmax,
            size       = marker_size,
            symbol     = "square",
            colorbar   = dict(
                title    = dict(text=name, font=dict(color="#8b949e", size=10)),
                thickness = 12,
                len       = 0.6,
                tickfont  = dict(color="#8b949e", size=10),
                            ) 
        ),
        name      = name,
        hoverinfo = "skip",
    )


def _base_layout(title: str) -> dict:
    return dict(
        template      = "plotly_dark",
        paper_bgcolor = "#0d1117",
        plot_bgcolor  = "#111820",
        margin        = dict(l=10, r=10, t=36, b=10),
        title         = dict(text=title, font=dict(color="#cdd9e5", size=12,
                                                    family="monospace")),
        xaxis = dict(title="x (m)", color="#8b949e",
                     showgrid=True, gridcolor="#1e2a35",
                     scaleanchor="y", scaleratio=1),
        yaxis = dict(title="y (m)", color="#8b949e",
                     showgrid=True, gridcolor="#1e2a35"),
        legend = dict(font=dict(color="#8b949e", size=10)),
    )


# ------------------------------------------------------------------
# Layout
# ------------------------------------------------------------------

def layout():
    return html.Div(
        style={"height": "100%", "display": "flex", "flexDirection": "column", "gap": "8px"},
        children=[

            # View mode toggle
            html.Div(
                style={"display": "flex", "alignItems": "center", "gap": "12px",
                       "padding": "0 4px"},
                children=[
                    html.Span("View:", style={"color": "#8b949e", "fontSize": "11px",
                                               "fontFamily": "monospace"}),
                    dcc.RadioItems(
                        id="river-view-mode",
                        options=[
                            {"label": " Curvilinear (faithful)", "value": "scatter"},
                            {"label": " Logical (fast)",          "value": "logical"},
                        ],
                        value="scatter",
                        style={"color": "#cdd9e5", "fontSize": "11px", "fontFamily": "monospace"},
                        labelStyle={"display": "inline-block", "marginRight": "12px"},
                        inputStyle={"marginRight": "4px"},
                    ),
                ]
            ),

            # Concentration / flow field (main plot)
            dcc.Graph(
                id     = "river-concentration-plot",
                figure = _empty_fig("Concentration & Flow Field"),
                style  = {"flex": "1 1 60%"},
                config = {"displayModeBar": False},
            ),

            # Bottom row: backtrack #No particles anymore -> can be reimplemented easly
            html.Div(
                style={"display": "flex", "flex": "1 1 40%", "gap": "8px"},
                children=[
                    dcc.Graph(
                        id     = "river-backtrack-plot",
                        figure = _empty_fig("Backtrack — Source Probability"),
                        style  = {"flex": "1"},
                        config = {"displayModeBar": False},
                    ),
                ]
            ),
        ]
    )


# ------------------------------------------------------------------
# Callbacks
# ------------------------------------------------------------------

def register_callbacks(app, sim_state):

    @app.callback(
        Output("river-concentration-plot", "figure"),
        Input("live-update-interval",  "n_intervals"),
        Input("river-view-mode",        "value"),
    )
    def update_concentration(_, view_mode):
        if sim_state.river is None:
            return _empty_fig("Draw the river on the map to start")

        r   = sim_state.river
        con = sim_state.get_concentration_map()

        fig = go.Figure()

        if view_mode == "scatter":
            # ---- Curvilinear scatter rendering (faithful to pcolormesh) ----

            # Velocity background
            fig.add_trace(_scatter_field(
                r.vis_x, r.vis_y, r.vis_v,
                colorscale="Blues", zmin=0, zmax=float(r.vis_v.max() or 1),
                marker_size=4, name="Velocity (m/s)",
            ))
            '''
            # Concentration overlay (only where non-negligible)
            if con is not None:
                mask = con > 0.001
                if mask.any():
                    fig.add_trace(_scatter_field(
                        r.vis_x, r.vis_y, np.where(mask, con, np.nan),
                        colorscale="Oranges", zmin=0, zmax=0.5,
                        marker_size=4, name="Concentration",
                    ))
            '''
            # Banks
            for t in _bank_traces(r):
                fig.add_trace(t)

            fig.update_layout(**_base_layout("Concentration & Flow Field — curvilinear"), uirevision="Don't change")

        else:
            # ---- Logical heatmap (fast, regular grid, indices as axes) ----
            n_stream, n_width = r.vis_v.shape

            fig.add_trace(go.Heatmap(
                z          = r.vis_v.T,
                colorscale = "Blues",
                showscale  = False,
                name       = "Velocity",
            ))

            if con is not None:
                fig.add_trace(go.Heatmap(
                    z          = con.T,
                    colorscale = "Oranges",
                    zmin=0, zmax=0.5,
                    opacity    = 0.8,
                    colorbar   = dict(title="Conc.", thickness=12, len=0.6,
                                      tickfont=dict(color="#8b949e", size=10)),
                    name       = "Concentration",
                ))

            fig.update_layout(
                template      = "plotly_dark",
                paper_bgcolor = "#0d1117",
                plot_bgcolor  = "#111820",
                margin        = dict(l=10, r=10, t=36, b=10),
                title         = dict(
                    text="Concentration & Flow Field — logical grid",
                    font=dict(color="#cdd9e5", size=12, family="monospace"),
                ),
                xaxis=dict(title="Stream index", color="#8b949e",
                           showgrid=True, gridcolor="#1e2a35"),
                yaxis=dict(title="Width index",  color="#8b949e",
                           showgrid=True, gridcolor="#1e2a35"),
                uirevision="Don't change"
            )

        # Buoy marker (always in physical coords for scatter mode; skip in logical)
        if view_mode == "scatter":
            fig.add_trace(go.Scattergl(
                x=[sim_state.buoy_local_x], y=[sim_state.buoy_local_y],
                mode="markers",
                marker=dict(color="#ffeb3b", size=12, symbol="diamond",
                            line=dict(color="white", width=1.5)),
                name="Buoy",
            ))
            if sim_state.contamination_detected:
                cx, cy = sim_state.contamination_local
                fig.add_trace(go.Scattergl(
                    x=[cx], y=[cy], mode="markers",
                    marker=dict(color="#ff1744", size=14, symbol="x",
                                line=dict(color="white", width=2)),
                    name="Detection point",
                ))

        return fig


    # ------------------------------------------------------------------

    @app.callback(
        Output("river-backtrack-plot", "figure"),
        Input("live-update-interval", "n_intervals"),
        Input("river-view-mode",      "value"),
    )
    def update_backtrack(_, view_mode):
        if sim_state.river is None or sim_state.backtrack_map is None:
            return _empty_fig("No backtrack yet — awaiting detection")

        r  = sim_state.river
        bt = sim_state.backtrack_map

        fig = go.Figure()

        if view_mode == "scatter":
            fig.add_trace(_scatter_field(
                r.vis_x, r.vis_y, bt,
                colorscale="Reds", zmin=0, zmax=float(bt.max() or 1),
                marker_size=4, name="Source probability",
            ))
            for t in _bank_traces(r):
                fig.add_trace(t)
            fig.update_layout(**_base_layout("Backtrack — Source Probability"), uirevision="Don't change")

        else:
            fig.add_trace(go.Heatmap(
                z=bt.T, colorscale="Reds",
                zmin=0, zmax=float(bt.max() or 1),
                colorbar=dict(title="Prob.", thickness=12, len=0.6,
                              tickfont=dict(color="#8b949e", size=10)),
            ))
            fig.update_layout(
                template="plotly_dark", paper_bgcolor="#0d1117",
                plot_bgcolor="#111820", margin=dict(l=10, r=10, t=36, b=10),
                title=dict(text="Backtrack — logical grid",
                           font=dict(color="#cdd9e5", size=12, family="monospace")),
                xaxis=dict(title="Stream index", color="#8b949e",
                           showgrid=True, gridcolor="#1e2a35"),
                yaxis=dict(title="Width index", color="#8b949e",
                           showgrid=True, gridcolor="#1e2a35"),
            )

        return fig