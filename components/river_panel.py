# =============================================================================
# components/river_panel.py — River model visualization panel (Plotly)
# =============================================================================
#
# The river model lives in a curvilinear grid:
#   river.vis_x, river.vis_y  shape (N_stream, N_width) — actual XY coords
#   concentration_map          shape (N_stream, N_width) — values at those coords
##
# Two display modes (toggled by a radio button in the panel):
#   "scatter"  — faithful curvilinear rendering (vis_x / vis_y coords)
#   "logical"  — regular heatmap in logical (stream index, width index) space

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


def _carpet_field_traces(river, values, colorscale, zmin, zmax,
                         name: str = ""):
    """
    Render a 2-D scalar field on the river's curvilinear grid using a
    Carpet + Contourcarpet trace.

    Plotly's Carpet is designed exactly for this case: a parametric (a, b)
    grid mapped to physical (x, y) coordinates, with field values overlaid
    via filled contours.  Unlike scatter markers it tiles the grid without
    gaps or overlap artifacts, so cross-stream gradients render cleanly.

    Returns a list of two traces (the invisible carpet + the contour layer).
    """
    N, M = river.vis_x.shape
    a = np.arange(N)
    b = np.arange(M)

    carpet = go.Carpet(
        carpet="riverc",
        a=a, b=b,
        x=river.vis_x, y=river.vis_y,
        aaxis=dict(showgrid=False, showticklabels="none", showline=False,
                   minorgridcount=0, smoothing=0),
        baxis=dict(showgrid=False, showticklabels="none", showline=False,
                   minorgridcount=0, smoothing=0),
        opacity=0.0,
    )
    contour = go.Contourcarpet(
        carpet="riverc",
        a=a, b=b,
        z=values,
        colorscale=colorscale, zmin=zmin, zmax=zmax,
        contours=dict(coloring="fill", showlines=False, start=zmin, end=zmax,
                      size=max(1e-3, (zmax - zmin) / 30.0)),
        line=dict(width=0),
        colorbar=dict(
            title=dict(text=name, font=dict(color="#cdd9e5", size=10)),
            thickness=10, len=0.9,
            x=1.01, xanchor="left", y=0.5, yanchor="middle",
            tickfont=dict(color="#cdd9e5", size=9),
        ),
        showlegend=False,
    )
    return [carpet, contour]


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
                   marker_size: int = 4, name: str = "",
                   sqrt_scale: bool = False) -> go.Scattergl:
    """
    Replicate pcolormesh behaviour using WebGL scatter.

    vis_x, vis_y, values are all (N_stream, N_width) arrays.

    When sqrt_scale=True, the color mapping uses sqrt(value) — this makes
    long downstream tails (where concentration follows the 1/sqrt(x) decay
    law for a continuous point source) clearly visible without distorting
    the underlying physics. The colorbar tick labels are remapped so they
    still display the *true* concentration values.
    """
    x_flat = vis_x.flatten()
    y_flat = vis_y.flatten()
    v_flat = values.flatten()

    if sqrt_scale:
        color_values = np.sqrt(np.clip(v_flat, 0.0, None))
        cmin_eff = float(np.sqrt(max(zmin, 0.0)))
        cmax_eff = float(np.sqrt(max(zmax, 1e-9)))
        # Build colorbar ticks that read in the original linear units
        tick_lin = np.linspace(zmin, zmax, 5)
        tick_vals = np.sqrt(np.clip(tick_lin, 0.0, None))
        tick_text = [f"{v:.2g}" for v in tick_lin]
    else:
        color_values = v_flat
        cmin_eff, cmax_eff = zmin, zmax
        tick_vals = None
        tick_text = None

    colorbar = dict(
        title     = dict(text=name, font=dict(color="#cdd9e5", size=10)),
        thickness = 10,
        len       = 0.9,
        x         = 1.01,
        xanchor   = "left",
        y         = 0.5,
        yanchor   = "middle",
        tickfont  = dict(color="#cdd9e5", size=9),
    )
    if tick_vals is not None:
        colorbar["tickvals"] = tick_vals
        colorbar["ticktext"] = tick_text

    return go.Scattergl(
        x    = x_flat,
        y    = y_flat,
        mode = "markers",
        marker = dict(
            color      = color_values,
            colorscale = colorscale,
            cmin       = cmin_eff,
            cmax       = cmax_eff,
            size       = marker_size,
            symbol     = "square",
            colorbar   = colorbar,
        ),
        name      = name,
        hoverinfo = "skip",
        showlegend = False,
    )


def _base_layout(title: str) -> dict:
    return dict(
        template      = "plotly_dark",
        paper_bgcolor = "#0d1117",
        plot_bgcolor  = "#111820",
        # right margin makes room for the vertical colorbar; bottom margin for
        # the horizontal legend; top for title; left for the y-axis title.
        margin        = dict(l=48, r=140, t=32, b=56),  # extra right room for 2 colorbars
        title         = dict(text=title, font=dict(color="#cdd9e5", size=12,
                                                    family="monospace"),
                             x=0.02, xanchor="left"),
        xaxis = dict(title=dict(text="x (m)", standoff=4),
                     color="#8b949e",
                     showgrid=True, gridcolor="#1e2a35",
                     scaleanchor="y", scaleratio=1),
        yaxis = dict(title=dict(text="y (m)", standoff=4),
                     color="#8b949e",
                     showgrid=True, gridcolor="#1e2a35"),
        legend = dict(
            orientation="h",
            yanchor="top",   y=-0.12,
            xanchor="left",  x=0.0,
            font=dict(color="#cdd9e5", size=10),
            bgcolor="rgba(28,42,58,0.85)",
            bordercolor="#2d4863", borderwidth=1,
        ),
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
                style={
                    "display": "flex", "alignItems": "center", "gap": "12px",
                    "padding": "8px 12px",
                    "background": "#1c2a3a",
                    "border": "1px solid #2d4863",
                    "borderRadius": "4px",
                },
                children=[
                    html.Span("View:", style={"color": "#cdd9e5", "fontSize": "11px",
                                               "fontFamily": "monospace",
                                               "letterSpacing": "0.05em"}),
                    dcc.RadioItems(
                        id="river-view-mode",
                        options=[
                            {"label": " Curvilinear", "value": "scatter"},
                            {"label": " Logical",          "value": "logical"},
                        ],
                        value="scatter",
                        style={"color": "#ffffff", "fontSize": "11px",
                               "fontFamily": "monospace"},
                        labelStyle={"display": "inline-block", "marginRight": "12px",
                                    "color": "#ffffff"},
                        inputStyle={"marginRight": "4px"},
                    ),
                ]
            ),

            # Flow-field plot — fills the upper half of the available space.
            html.Div(
                id="river-model-block",
                style={"display": "flex", "flex": "1 1 50%",
                       "minHeight": "0", "gap": "8px"},
                children=[
                    dcc.Graph(
                        id     = "river-model-plot-0",
                        figure = _empty_fig("River model with buoy location"),
                        style  = {"flex": "1 1 100%", "height": "100%"},
                        config = {"displayModeBar": False, "responsive": True},
                    )
                ]
            ),


            # View mode toggle for contamination type
            html.Div(
                style={
                    "display": "flex", "alignItems": "center", "gap": "12px",
                    "padding": "8px 12px",
                    "background": "#1c2a3a",
                    "border": "1px solid #2d4863",
                    "borderRadius": "4px",
                },
                children=[
                    html.Span("View:", style={"color": "#cdd9e5", "fontSize": "11px",
                                               "fontFamily": "monospace",
                                               "letterSpacing": "0.05em"}),
                    dcc.RadioItems(
                        id="contamination-view-mode",
                        options=[
                            {"label": " Forward contamination flow", "value": "forward"},
                            {"label": " Probability map",    "value": "prob"},
                        ],
                        value="forward",
                        style={"color": "#ffffff", "fontSize": "11px",
                               "fontFamily": "monospace"},
                        labelStyle={"display": "inline-block", "marginRight": "12px",
                                    "color": "#ffffff"},
                        inputStyle={"marginRight": "4px"},
                    ),
                ]
            ),



            # Bottom plot — same flex / min-height as the top block so they
            # are visually aligned and never crowd each other.
            html.Div(
                style={"display": "flex", "flex": "1 1 50%",
                       "minHeight": "0", "gap": "8px"},
                children=[
                    dcc.Graph(
                        id     = "river-prob-plot",
                        figure = _empty_fig("Backtrack — Source Probability"),
                        style  = {"flex": "1 1 100%", "height": "100%"},
                        config = {"displayModeBar": False, "responsive": True},
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
                style  = {"flex": "1", "minWidth": 0},   # minWidth:0 lets flex items shrink below content size
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
            # n_vals = linspace(-W/2, +W/2), so j=0 is the RIGHT bank (n<0) and
            # j=N-1 is the LEFT bank (n>0).  Without flipping, Plotly maps j=0 to
            # the left of the x-axis — physically mirrored.  Flip the width axis
            # so "left bank → left of plot, right bank → right of plot".
            v_display  = r.vis_v[:,  ::-1]
            vn_display = r.vis_vn[:, ::-1]
            n_w = v_display.shape[1]

            # Streamwise velocity
            fig_v = go.Figure(data=go.Heatmap(
                z=v_display,
                x=np.arange(n_w),
                y=np.arange(v_display.shape[0]),
                colorscale="Blues",
                colorbar=dict(title="m/s", thickness=10),
                hovertemplate="Cross-stream: %{x}<br>Stream: %{y}<br>Speed: %{z:.2f} m/s<extra></extra>"
            ))
            fig_v.update_layout(
                **_base_layout("Streamwise Velocity (u)  —  left bank left, right bank right"),
                xaxis_title="Cross-stream  (0 = left bank,  N = right bank)",
                yaxis_title="Streamwise Index",
                autosize=True,
                uirevision="Don't change"
            )

            figures.append(fig_v)

            # Normal velocity — signed: positive = toward LEFT bank, negative = toward RIGHT bank.
            # Right bend -> outer bank is LEFT -> u_n > 0 on outer bank (red).
            # Left  bend -> outer bank is RIGHT -> u_n < 0 on outer bank (blue).
            vn_abs = float(np.abs(vn_display).max()) or 1.0
            fig_vn = go.Figure(data=go.Heatmap(
                z=vn_display,
                x=np.arange(n_w),
                y=np.arange(vn_display.shape[0]),
                colorscale="RdBu",
                zmid=0,
                zmin=-vn_abs,
                zmax= vn_abs,
                colorbar=dict(title="m/s", thickness=10),
                hovertemplate="Cross-stream: %{x}<br>Stream: %{y}<br>vn: %{z:.3f} m/s<extra></extra>"
            ))
            fig_vn.update_layout(
                **_base_layout("Normal Velocity (u_n)  —  red=toward left bank  /  blue=toward right bank"),
                xaxis_title="Cross-stream  (0 = left bank,  N = right bank)",
                yaxis_title="Streamwise Index",
                autosize=True,
                uirevision="Don't change"
            )

            figures.append(fig_vn)


        # Buoy + source + estimate (scatter mode only; logical view is grid-index space)
        if view_mode == "scatter":
            if buoy_dt_instance.local_x is not None:
                fig.add_trace(go.Scattergl(
                    x=[buoy_dt_instance.local_x], y=[buoy_dt_instance.local_y],
                    mode="markers",
                    marker=dict(color="#ffeb3b", size=12, symbol="diamond",
                                line=dict(color="white", width=1.5)),
                    name="Buoy",
                ))
            if sim_state.source_local is not None:
                sx, sy = sim_state.source_local
                fig.add_trace(go.Scattergl(
                    x=[sx], y=[sy], mode="markers",
                    marker=dict(color="#ff1744", size=14, symbol="star",
                                line=dict(color="white", width=1.5)),
                    name="True source",
                ))
            if sim_state.probability_map is not None:
                r = sim_state.river
                i, j = np.unravel_index(np.argmax(sim_state.probability_map),
                                        sim_state.probability_map.shape)
                fig.add_trace(go.Scattergl(
                    x=[float(r.vis_x[i, j])], y=[float(r.vis_y[i, j])],
                    mode="markers",
                    marker=dict(color="#00e5ff", size=14, symbol="circle-open",
                                line=dict(color="#00e5ff", width=3)),
                    name="Estimated source",
                ))
            if sim_state.detection_history:
                xs    = [d["x_local"]                          for d in sim_state.detection_history]
                ys    = [d["y_local"]                          for d in sim_state.detection_history]
                # Colour by ABSOLUTE concentration (not the c/cmax ratio).
                # The ratio rescales every tick as the field evolves which can
                # invert the apparent colour ordering between detection points.
                concs = [float(d.get("conc", d.get("intensity", 0.0)))
                         for d in sim_state.detection_history]
                # Stable colour scale: 0 .. (peak of forward plume at this moment).
                # Falls back to the recorded max so values are always normalised
                # against something visible.
                cmax_field = 0.01
                if sim_state.dv is not None:
                    try:
                        cmax_field = max(cmax_field, float(sim_state.dv.get_concentration_map().max()))
                    except Exception:
                        pass
                cmax_eff = max(cmax_field, max(concs) if concs else 0.01)
                fig.add_trace(go.Scattergl(
                    x=xs, y=ys, mode="markers",
                    marker=dict(
                        color=concs,
                        cmin=0.0,
                        cmax=cmax_eff,
                        colorscale="Reds",     # pale pink (low) -> deep red (high), unambiguous
                        size=5, symbol="circle",
                        line=dict(width=0),
                        colorbar=dict(
                            title=dict(text="Local<br>concentration",
                                       font=dict(color="#cdd9e5", size=10)),
                            thickness=10, len=0.4,
                            x=1.11, xanchor="left",
                            y=0.25, yanchor="middle",
                            tickfont=dict(color="#cdd9e5", size=9),
                        ),
                    ),
                    name="Detections",
                    hovertemplate="x: %{x:.0f} m<br>y: %{y:.0f} m<br>c: %{marker.color:.3f}<extra></extra>",
                ))

        return get_graph_children(figures)


    # ------------------------------------------------------------------

    @app.callback(
        Output("river-prob-plot", "figure"),
        Input("live-update-interval", "n_intervals"),
        Input("contamination-view-mode",      "value"),
    )
    def update_probability_map(_, view_mode):
        r = sim_state.river
        if r is None:
            return _empty_fig("Draw the river on the map to start")

        if view_mode == "prob":
            if sim_state.probability_map is None:
                return _empty_fig("No estimation yet — awaiting estimation")
            data = sim_state.probability_map
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
            sqrt_scale=True,    # boost long downstream tail (1/sqrt(x) decay)
        ))
        for t in _bank_traces(r):
            fig.add_trace(t)
        fig.update_layout(**_base_layout(title), uirevision="Don't change")
        return fig