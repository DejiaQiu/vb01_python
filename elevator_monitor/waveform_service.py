from __future__ import annotations

import base64
import html
import math
from pathlib import Path
from typing import Any

from report.fault_algorithms._base import build_feature_pack, load_rows, parse_float, parse_int


def _extract_series(rows: list[dict[str, Any]], names: tuple[str, ...]) -> list[float]:
    out: list[float] = []
    for row in rows:
        value = None
        for name in names:
            value = parse_float(row.get(name))
            if value is not None:
                break
        out.append(float(value if value is not None else 0.0))
    return out


def _extract_ts_ms(rows: list[dict[str, Any]]) -> list[int]:
    ts: list[int] = []
    for idx, row in enumerate(rows):
        value = parse_int(row.get("ts_ms"))
        if value is None:
            value = parse_int(row.get("data_ts_ms"))
        if value is None:
            value = idx
        ts.append(int(value))
    return ts


def _pick_effective_rows(rows: list[dict[str, Any]], min_real_rows: int = 8) -> tuple[list[dict[str, Any]], bool]:
    if not rows:
        return rows, False
    real_rows: list[dict[str, Any]] = []
    has_flag = False
    for row in rows:
        flag = parse_int(row.get("is_new_frame"))
        if flag is None:
            continue
        has_flag = True
        if flag == 1:
            real_rows.append(row)
    if has_flag and len(real_rows) >= min_real_rows:
        return real_rows, True
    return rows, False


def _downsample(xs: list[float], ys: list[float], max_points: int) -> tuple[list[float], list[float]]:
    if len(xs) <= max_points:
        return xs, ys
    idxs = [round(i * (len(xs) - 1) / max(1, max_points - 1)) for i in range(max_points)]
    return [xs[i] for i in idxs], [ys[i] for i in idxs]


def _polyline_points(xs: list[float], ys: list[float], *, width: int, height: int, pad_x: int = 44, pad_y: int = 22) -> str:
    if not xs or not ys:
        return ""
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    if math.isclose(max_x, min_x):
        max_x = min_x + 1.0
    if math.isclose(max_y, min_y):
        max_y = min_y + 1.0
    inner_w = max(1, width - 2 * pad_x)
    inner_h = max(1, height - 2 * pad_y)
    pts: list[str] = []
    for x, y in zip(xs, ys):
        px = pad_x + inner_w * (x - min_x) / (max_x - min_x)
        py = pad_y + inner_h * (1.0 - (y - min_y) / (max_y - min_y))
        pts.append(f"{px:.2f},{py:.2f}")
    return " ".join(pts)


def _build_svg(
    title: str,
    series: list[dict[str, Any]],
    *,
    width: int = 920,
    height: int = 320,
) -> str:
    bg = "#ffffff"
    axis = "#D0D7DE"
    text = "#24292F"
    legend_bg = "#F6F8FA"
    inner_w = width - 88
    inner_h = height - 44
    grid_lines = []
    for i in range(5):
        y = 22 + inner_h * i / 4
        grid_lines.append(f'<line x1="44" y1="{y:.2f}" x2="{width - 44}" y2="{y:.2f}" stroke="{axis}" stroke-width="1" />')
    legend_items = []
    for idx, item in enumerate(series):
        legend_y = 34 + idx * 18
        legend_items.append(
            f'<rect x="{width - 190}" y="{legend_y - 9}" width="12" height="3" fill="{item["color"]}" />'
            f'<text x="{width - 172}" y="{legend_y}" font-size="12" fill="{text}">{html.escape(str(item["label"]))}</text>'
        )
    paths = []
    for item in series:
        points = _polyline_points(item["xs"], item["ys"], width=width, height=height)
        if not points:
            continue
        paths.append(
            f'<polyline fill="none" stroke="{item["color"]}" stroke-width="2" points="{points}" />'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="{bg}" rx="12" />'
        f'<text x="24" y="28" font-size="16" font-weight="600" fill="{text}">{html.escape(title)}</text>'
        f'<rect x="{width - 206}" y="18" width="170" height="{max(28, len(series) * 18 + 14)}" rx="10" fill="{legend_bg}" stroke="{axis}" />'
        f'{"".join(grid_lines)}'
        f'<line x1="44" y1="{height - 22}" x2="{width - 44}" y2="{height - 22}" stroke="{text}" stroke-width="1.2" />'
        f'<line x1="44" y1="22" x2="44" y2="{height - 22}" stroke="{text}" stroke-width="1.2" />'
        f'{"".join(paths)}'
        f'{"".join(legend_items)}'
        "</svg>"
    )


def _data_uri(svg: str) -> str:
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _plot_block(title: str, series: list[dict[str, Any]], *, width: int, height: int) -> dict[str, Any]:
    svg = _build_svg(title, series, width=width, height=height)
    uri = _data_uri(svg)
    return {
        "title": title,
        "svg": svg,
        "data_uri": uri,
        "markdown": f"![{title}]({uri})",
    }


def load_waveform_rows(rows: list[dict[str, Any]], csv_text: str, csv_path: str) -> tuple[list[dict[str, Any]], str]:
    if rows:
        normalized = [{str(k): "" if v is None else str(v) for k, v in row.items()} for row in rows if isinstance(row, dict)]
        return normalized, "inline_rows"
    if csv_text.strip():
        import csv
        import io

        parsed = [dict(row) for row in csv.DictReader(io.StringIO(csv_text.strip()))]
        return parsed, "inline_csv_text"
    if csv_path.strip():
        path = Path(csv_path).expanduser().resolve()
        return load_rows(path), str(path)
    raise ValueError("provide rows, csv_text, or csv_path")


def build_waveform_payload(
    rows: list[dict[str, Any]],
    *,
    source: str = "",
    width: int = 920,
    height: int = 320,
    max_points: int = 240,
) -> dict[str, Any]:
    effective_rows, used_new_only = _pick_effective_rows(rows)
    features = build_feature_pack(rows)
    ts_ms = _extract_ts_ms(effective_rows)
    if ts_ms:
        t0 = ts_ms[0]
        xs = [(ts - t0) / 1000.0 for ts in ts_ms]
    else:
        xs = [float(i) for i in range(len(effective_rows))]

    ax = _extract_series(effective_rows, ("Ax", "AX"))
    ay = _extract_series(effective_rows, ("Ay", "AY"))
    az = _extract_series(effective_rows, ("Az", "AZ"))
    gx = _extract_series(effective_rows, ("Gx", "GX"))
    gy = _extract_series(effective_rows, ("Gy", "GY"))
    gz = _extract_series(effective_rows, ("Gz", "GZ"))
    amag = [math.sqrt(ax[i] * ax[i] + ay[i] * ay[i] + az[i] * az[i]) for i in range(len(ax))]

    x_acc, ax = _downsample(xs, ax, max_points)
    _, ay = _downsample(xs, ay, max_points)
    _, az = _downsample(xs, az, max_points)
    x_gyr, gx = _downsample(xs, gx, max_points)
    _, gy = _downsample(xs, gy, max_points)
    _, gz = _downsample(xs, gz, max_points)
    x_mag, amag = _downsample(xs, amag, max_points)

    acceleration = _plot_block(
        "Acceleration Waveform",
        [
            {"label": "Ax", "color": "#1F77B4", "xs": x_acc, "ys": ax},
            {"label": "Ay", "color": "#FF7F0E", "xs": x_acc, "ys": ay},
            {"label": "Az", "color": "#2CA02C", "xs": x_acc, "ys": az},
        ],
        width=width,
        height=height,
    )
    gyroscope = _plot_block(
        "Gyroscope Waveform",
        [
            {"label": "Gx", "color": "#9467BD", "xs": x_gyr, "ys": gx},
            {"label": "Gy", "color": "#D62728", "xs": x_gyr, "ys": gy},
            {"label": "Gz", "color": "#8C564B", "xs": x_gyr, "ys": gz},
        ],
        width=width,
        height=height,
    )
    magnitude = _plot_block(
        "Acceleration Magnitude",
        [
            {"label": "A_mag", "color": "#17BECF", "xs": x_mag, "ys": amag},
        ],
        width=width,
        height=height,
    )

    markdown = "\n".join(
        [
            "## 波形图",
            "",
            acceleration["markdown"],
            "",
            gyroscope["markdown"],
            "",
            magnitude["markdown"],
        ]
    )

    return {
        "source": source,
        "summary": {
            "n_raw": int(features.get("n_raw", 0)),
            "n_effective": int(features.get("n", 0)),
            "fs_hz": round(float(features.get("fs_hz", 0.0)), 4),
            "duration_s": round(float(features.get("duration_s", 0.0)), 4),
            "used_new_only": bool(features.get("used_new_only", False)),
            "effective_rows_from_new_frame": bool(used_new_only),
        },
        "plots": {
            "acceleration": acceleration,
            "gyroscope": gyroscope,
            "acceleration_magnitude": magnitude,
        },
        "markdown": markdown,
    }
