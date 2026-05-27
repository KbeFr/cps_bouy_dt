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

            # Create a Div entity for flow field so graph can be split up. 
            html.Div(
                id="river-model-block" ,
                style={"display": "flex", "flex": "1 1 40%", "gap": "8px"}, #check
                children=[
                            dcc.Graph(
                            id     = "river-model-plot-0",
                            figure = _empty_fig("River model with bouy location"),
                            style  = {"flex": "1 1 60%"},
                            config = {"displayModeBar": False},
                                    )]
            ),


            # View mode toggle for contamination type 
            html.Div(
                style={"display": "flex", "alignItems": "center", "gap": "12px",
                       "padding": "0 4px"},
                children=[
                    html.Span("View:", style={"color": "#8b949e", "fontSize": "11px",
                                               "fontFamily": "monospace"}),
                    dcc.RadioItems(
                        id="contamination-view-mode",
                        options=[
                            {"label": " Forward contamination flow", "value": "forward"},
                            {"label": " Backtrack contamination",    "value": "backtrack"},
                        ],
                        value="scatter",
                        style={"color": "#cdd9e5", "fontSize": "11px", "fontFamily": "monospace"},
                        labelStyle={"display": "inline-block", "marginRight": "12px"},
                        inputStyle={"marginRight": "4px"},
                    ),
                ]
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
            )
        ]
    )


# ------------------------------------------------------------------
# Callbacks
# ------------------------------------------------------------------

def get_graph_children(figures):
    children = []
    for i, figure in enumerate(figures):
        children.append(dcc.Graph(
                id     = f"river-model-plot-{i}",  
                figure = figure,
                style  = {"flex": "1"},            # 1 so they split the space evenly
                config = {"displayModeBar": False},
            ))
    return children


def register_callbacks(app, sim_state, buoy_dt_instance):

    @app.callback(
        Output(component_id='river-model-block', component_property='children'),
        Input("live-update-interval",  "n_intervals"),
        Input("river-view-mode",        "value"),
    )
    def update_river_model(_, view_mode):

        figures = []

        if sim_state.river is None:
            return get_graph_children([(_empty_fig("Draw the river on the map to start"))])

        r  = sim_state.river

        if view_mode == "scatter":
            # ---- Curvilinear scatter rendering (faithful to pcolormesh) ----
            fig = go.Figure()

            # Velocity background
            fig.add_trace(_scatter_field(
                r.vis_x, r.vis_y, r.vis_v,
                colorscale="Blues", zmin=0, zmax=float(r.vis_v.max() or 1),
                marker_size=4, name="Velocity (m/s)",
            ))

            # Banks
            for t in _bank_traces(r):
                fig.add_trace(t)

            fig.update_layout(**_base_layout("Flow Field — curvilinear"), uirevision="Don't change")

            figures.append(fig)

        else:
            # ---- Logical heatmap  ----
            # Here the map needs to  be visualized as 2 straight sections with the velocities displayed
            # So a left straigt section one with v and one with v_n 
            
            n_stream, n_width = r.vis_v.shape

            #Stramwise velocity 
            fig_v = go.Figure()

            fig_v = go.Figure(data=go.Heatmap(
                z=r.vis_v,
                x=np.arange(r.vis_v.shape[0]),
                y=np.arange(r.vis_v.shape[1]),
                colorscale="Blues",
                colorbar=dict(title="m/s", thickness=10),
                hovertemplate="Width: %{x}<br>Stream: %{y}<br>Speed: %{z:.2f} m/s<extra></extra>"
            ))
            fig_v.update_layout(
                **_base_layout("Streamwise Velocity (u)"),
                xaxis_title="Cross-stream Index",
                yaxis_title="Streamwise Index",
                height=500 ,
                width = 300,
                uirevision="Don't change"

            )

            figures.append(fig_v)

            #Normal direction velocity 
            fig_vn = go.Figure()

            fig_vn = go.Figure(data=go.Heatmap(
                z=r.vis_vn,
                x=np.arange(r.vis_vn.shape[0]),
                y=np.arange(r.vis_vn.shape[1]),
                colorscale="Blues",
                colorbar=dict(title="m/s", thickness=10),
                hovertemplate="Stream: %{x}<br>Width: %{y}<br>Speed: %{z:.2f} m/s<extra></extra>"
            ))
            fig_vn.update_layout(
                **_base_layout("Normal Velocity (u_n)"),
                xaxis_title="Cross-stream Index",
                yaxis_title="Streamwise Index",
                height=500, 
                width = 300,
                uirevision="Don't change"
            )

            figures.append(fig_vn)


        # Buoy marker (always in physical coords for scatter mode; skip in logical)
        if view_mode == "scatter":
            fig.add_trace(go.Scattergl(
                x=[buoy_dt_instance.local_x], y=[buoy_dt_instance.local_y],
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

        return get_graph_children(figures)


    # ------------------------------------------------------------------

    @app.callback(
        Output("river-backtrack-plot", "figure"),
        Input("live-update-interval", "n_intervals"),
        Input("contamination-view-mode",      "value"),
    )
    def update_backtrack(_, view_mode):
        r = sim_state.river
        if r is None:
            return _empty_fig("Draw the river on the map to start")

        if view_mode == "backtrack":
            if sim_state.backtrack_map is None:
                return _empty_fig("No backtrack yet — awaiting contamination detection")
            data = sim_state.backtrack_map
            title = "Backtrack — Source Probability"
        else:
            cmap = sim_state.get_concentration_map()
            if cmap is None:
                return _empty_fig("No contamination simulation yet")
            data = cmap
            title = "DVSolver — Forward Contamination"

        fig = go.Figure()
        fig.add_trace(_scatter_field(
            r.vis_x, r.vis_y, data,
            colorscale="Reds", zmin=0, zmax=float(data.max() or 1),
            marker_size=4, name="Concentration",
        ))
        for t in _bank_traces(r):
            fig.add_trace(t)
        fig.update_layout(**_base_layout(title), uirevision="Don't change")
        return fig
