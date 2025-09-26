# /// script
# dependencies = [
#   "bbos",
#   "bokeh"
# ]
# [tool.uv.sources]
# bbos = { path = "/home/bracketbot/BracketBotOS", editable = true }
# ///
# main.py
from bbos import Reader
from bokeh.layouts import column
from bokeh.models import ColumnDataSource, DataRange1d
from bokeh.models.tools import HoverTool
from bokeh.palettes import Category10, Category20, Viridis256
from bokeh.plotting import figure
from bokeh.server.server import Server
import numpy as np
import time

PORT = 5008
ROLLOVER = 3000
DT_MS = 1  # update period (ms)

def _sample_colors(n):
    """Return n visually distinct hex colors."""
    if n <= 10:
        return Category10[10][:n]
    if n <= 20:
        return Category20[20][:n]
    # Evenly sample Viridis256
    if n <= 1:
        return [Viridis256[128]]
    idxs = [round(i * (len(Viridis256) - 1) / (n - 1)) for i in range(n)]
    return [Viridis256[i] for i in idxs]

def _series_from_dtype(dt):
    series = []
    for name in dt.names:
        if name == "timestamp":
            continue
        field = dt[name]
        shape = getattr(field, "shape", ())
        if shape == () or shape == (1,):
            def _scalar(field_name=name):
                return lambda data: float(data[field_name])
            series.append((name, _scalar()))
        else:
            n = int(np.prod(shape))
            for idx in range(n):
                def _vec_extractor(field_name=name, element=idx):
                    return lambda data: float(np.ravel(data[field_name])[element])
                series.append((f"{name}[{idx}]", _vec_extractor()))
    return series


def _build_plot(reader, title):
    d0 = reader.data
    dt = d0.dtype
    series = _series_from_dtype(dt)

    colors = _sample_colors(len(series)) if series else []
    color_map = {label: colors[i] for i, (label, _) in enumerate(series)}

    ctx = {
        "reader": reader,
        "series": series,
        "sources": {},
        "has_ts": ("timestamp" in dt.names),
        "ts0": int(d0["timestamp"]) if "timestamp" in dt.names else None,
        "t0_wall": np.datetime64("now", "ms"),
    }

    plot = figure(title=title,
                  x_axis_label="Δt (s)",
                  y_axis_label="value",
                  tools="pan,wheel_zoom,box_zoom,reset",
                  sizing_mode="stretch_both",
                  output_backend="webgl")
    
    plot.y_range = DataRange1d(only_visible=True, range_padding=0.05)

    hover = HoverTool(
        tooltips=[
            ("t (s)", "@dt_s{0.000}"),
            ("value", "@val{0.000}"),
        ],
        mode="vline"
    )
    plot.add_tools(hover)

    for label, _ in series:
        src = ColumnDataSource(data=dict(dt_s=[], val=[]))
        ctx["sources"][label] = src
        plot.circle("dt_s", "val", source=src, size=3, alpha=0.9,
                    color=color_map[label], legend_label=label)

    plot.legend.click_policy = "hide"
    plot.legend.location = "top_left"
    ctx["figure"] = plot
    return ctx


def _stream(ctx):
    reader = ctx["reader"]
    if reader.ready():
        data = reader.data
        if ctx["has_ts"]:
            dt_s = (int(data["timestamp"]) - ctx["ts0"]) / 1e9
        else:
            now_ms = np.datetime64("now", "ms")
            dt_s = float((now_ms - ctx["t0_wall"]) / np.timedelta64(1, "ms")) / 1000.0
        for label, extract in ctx["series"]:
            ctx["sources"][label].stream({"dt_s": [dt_s], "val": [extract(data)]}, rollover=ROLLOVER)


def make_document(doc):
    r_orientation = Reader("imu.orientation")
    r_drive = Reader("drive.state")
    readcnt = 0
    while readcnt < 2:
        if r_orientation.ready():
            readcnt += 1
        if r_drive.ready():
            readcnt += 1

    ctx_orientation = _build_plot(r_orientation, "imu.orientation")
    ctx_drive = _build_plot(r_drive, "drive.state")

    doc.add_root(column(ctx_orientation["figure"],
                        ctx_drive["figure"],
                        sizing_mode="stretch_both"))

    def tick():
        _stream(ctx_orientation)
        _stream(ctx_drive)

    doc.add_periodic_callback(tick, DT_MS)

if __name__ == "__main__":
    server = Server({"/": make_document}, port=PORT,
                    allow_websocket_origin=[f"localhost:{PORT}",f"127.0.0.1:{PORT}"])
    server.start()
    print(f"Bokeh app at http://localhost:{PORT}/")
    server.io_loop.add_callback(server.show, "/")
    server.io_loop.start()
