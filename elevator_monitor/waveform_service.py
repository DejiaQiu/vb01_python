from __future__ import annotations

import base64
import html
import json
import math
from pathlib import Path
from typing import Any

from report.fault_algorithms._base import (
    _scan_spectrum,
    axis_mapping_signature,
    build_feature_pack,
    load_rows,
    normalize_axis_mapping,
    parse_float,
    parse_int,
)


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


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _diag_dict(payload: dict[str, Any] | None, key: str) -> dict[str, Any]:
    diag = payload if isinstance(payload, dict) else {}
    latest_result = diag.get("latest_result")
    if isinstance(latest_result, dict):
        nested = latest_result.get(key)
        if isinstance(nested, dict):
            return nested
    direct = diag.get(key)
    return direct if isinstance(direct, dict) else {}


def _diag_list(payload: dict[str, Any] | None, key: str) -> list[Any]:
    diag = payload if isinstance(payload, dict) else {}
    latest_result = diag.get("latest_result")
    if isinstance(latest_result, dict):
        nested = latest_result.get(key)
        if isinstance(nested, list):
            return nested
    direct = diag.get(key)
    return direct if isinstance(direct, list) else []


def _axis_mapping_from_signature(signature: str) -> dict[str, str] | None:
    text = str(signature or "").strip()
    if not text:
        return None
    parts: dict[str, str] = {}
    for item in text.split("|"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts[str(key).strip()] = str(value).strip()
    candidate = {
        "vertical": parts.get("vertical", ""),
        "lateral_x": parts.get("lateral_x", ""),
        "lateral_y": parts.get("lateral_y", ""),
    }
    values = list(candidate.values())
    if any(value not in {"Ax", "Ay", "Az"} for value in values):
        return None
    if len(set(values)) != 3:
        return None
    return candidate


def _axis_mapping_for_waveforms(diagnosis_result: dict[str, Any] | None) -> dict[str, str]:
    summary = _diag_dict(diagnosis_result, "summary")
    signature = str(summary.get("axis_mapping_signature", "")).strip()
    return normalize_axis_mapping(_axis_mapping_from_signature(signature))


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


def _series_values(values: list[float]) -> list[float]:
    return [round(float(item), 6) for item in values]


def _echarts_option(title: str, series: list[dict[str, Any]]) -> dict[str, Any]:
    x_data = [round(float(item), 3) for item in (series[0]["xs"] if series else [])]
    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "axis"},
        "legend": {"top": 28, "data": [str(item["label"]) for item in series]},
        "grid": {"left": 48, "right": 24, "top": 72, "bottom": 36},
        "xAxis": {
            "type": "category",
            "name": "时间 (秒)",
            "boundaryGap": False,
            "data": x_data,
        },
        "yAxis": {
            "type": "value",
            "scale": True,
        },
        "series": [
            {
                "name": str(item["label"]),
                "type": "line",
                "showSymbol": False,
                "smooth": False,
                "lineStyle": {"width": 2, "color": str(item["color"])},
                "itemStyle": {"color": str(item["color"])},
                "data": _series_values(item["ys"]),
            }
            for item in series
        ],
    }


def _echarts_block(title: str, series: list[dict[str, Any]]) -> dict[str, Any]:
    option = _echarts_option(title, series)
    option_json = json.dumps(option, ensure_ascii=False)
    return {
        "option": option,
        "option_json": option_json,
        "markdown": f"```echarts\n{option_json}\n```",
    }


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    if not headers or not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        padded = list(row[: len(headers)]) + [""] * max(0, len(headers) - len(row))
        cells = [str(item).replace("\n", "<br>").replace("|", "/") for item in padded[: len(headers)]]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _sampling_condition_text(condition: str) -> str:
    key = str(condition or "").strip().lower()
    labels = {
        "sampling_ok": "采样条件满足",
        "low_sampling_rate": "采样率过低，频域特征稳定性不足",
        "too_few_samples": "有效样本点过少",
        "insufficient_samples": "有效样本点过少",
        "short_duration": "窗口时长不足",
        "short_window": "窗口时长不足",
        "invalid_timing": "时间戳异常",
        "no_effective_samples": "没有有效采样点",
        "no_rows": "没有可用数据",
    }
    return labels.get(key, key or "未知")


def _screening_status_text(status: str) -> str:
    key = str(status or "").strip().lower()
    labels = {
        "candidate_faults": "高度怀疑，需要尽快复核",
        "watch_only": "看到了异常变化，建议继续观察或复测",
        "normal": "当前未见明确异常",
        "low_quality": "数据不足，先不要下结论",
        "unknown": "暂未形成明确结论",
    }
    return labels.get(key, key or "暂未形成明确结论")


def _axis_description(mapping: dict[str, str], mode: str) -> tuple[str, str]:
    vertical = mapping["vertical"]
    lateral_x = mapping["lateral_x"]
    lateral_y = mapping["lateral_y"]
    short = f"当前按 `{vertical}` 作为竖向，`{lateral_x}/{lateral_y}` 作为横向"
    long = (
        f"{short}。图里的 `Ax/Ay/Az` 仍是原始轴名，"
        f"横向/竖向特征与低频频域图都按这套映射计算。"
    )
    if str(mode or "").strip() == "explicit":
        long += " 这次使用了显式轴向映射配置。"
    else:
        long += " 这次使用的是默认轴向映射。"
    return short, long


def _normalized_spectrum_values(bins: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    if not bins:
        return [], []
    peak = max((float(power) for _, power in bins), default=0.0)
    scale = peak if peak > 1e-9 else 1.0
    xs = [float(freq) for freq, _ in bins]
    ys = [float(power) / scale for _, power in bins]
    return xs, ys


def _build_low_frequency_spectrum(
    rows: list[dict[str, Any]],
    *,
    mapping: dict[str, str],
    fs_hz: float,
    width: int,
    height: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    accel_by_axis = {
        "Ax": _extract_series(rows, ("Ax", "AX")),
        "Ay": _extract_series(rows, ("Ay", "AY")),
        "Az": _extract_series(rows, ("Az", "AZ")),
    }
    lat_x = accel_by_axis[mapping["lateral_x"]]
    lat_y = accel_by_axis[mapping["lateral_y"]]
    vertical = accel_by_axis[mapping["vertical"]]
    lat_x_mean = sum(lat_x) / len(lat_x) if lat_x else 0.0
    lat_y_mean = sum(lat_y) / len(lat_y) if lat_y else 0.0
    vertical_mean = sum(vertical) / len(vertical) if vertical else 0.0
    lateral_signal = [
        math.sqrt((lat_x[idx] - lat_x_mean) * (lat_x[idx] - lat_x_mean) + (lat_y[idx] - lat_y_mean) * (lat_y[idx] - lat_y_mean))
        for idx in range(min(len(lat_x), len(lat_y)))
    ]
    vertical_signal = [value - vertical_mean for value in vertical]
    lateral_bins = _scan_spectrum(lateral_signal, fs_hz=fs_hz, freq_min_hz=0.3, freq_max_hz=4.0, step_hz=0.1)
    vertical_bins = _scan_spectrum(vertical_signal, fs_hz=fs_hz, freq_min_hz=0.3, freq_max_hz=4.0, step_hz=0.1)
    x_lat, y_lat = _normalized_spectrum_values(lateral_bins)
    x_vert, y_vert = _normalized_spectrum_values(vertical_bins)
    plot = _plot_block(
        "横向/竖向低频能量对比",
        [
            {"label": "横向摆动", "color": "#E4572E", "xs": x_lat, "ys": y_lat},
            {"label": "竖向传递", "color": "#4C78A8", "xs": x_vert, "ys": y_vert},
        ],
        width=width,
        height=height,
    )
    chart = _echarts_block(
        "横向/竖向低频能量对比",
        [
            {"label": "横向摆动", "color": "#E4572E", "xs": x_lat, "ys": y_lat},
            {"label": "竖向传递", "color": "#4C78A8", "xs": x_vert, "ys": y_vert},
        ],
    )
    if isinstance(chart.get("option"), dict):
        chart["option"]["xAxis"]["name"] = "频率 (Hz)"
        chart["option"]["yAxis"]["name"] = "相对能量"
        chart["option"]["title"]["text"] = "横向/竖向低频能量对比"
        chart["option"]["legend"]["data"] = ["横向摆动", "竖向传递"]
        for series, label in zip(chart["option"]["series"], ["横向摆动", "竖向传递"]):
            series["name"] = label
        chart["option_json"] = json.dumps(chart["option"], ensure_ascii=False)
        chart["markdown"] = f"```echarts\n{chart['option_json']}\n```"
    plot["title"] = "横向/竖向低频能量对比"
    plot["markdown"] = f"![横向/竖向低频能量对比]({plot['data_uri']})"
    return plot, chart


def _build_insight_markdown(
    *,
    features: dict[str, Any],
    diagnosis_result: dict[str, Any] | None,
    mapping: dict[str, str],
    used_new_only: bool,
) -> str:
    diag = diagnosis_result if isinstance(diagnosis_result, dict) else {}
    summary = _diag_dict(diag, "summary")
    screening = _diag_dict(diag, "screening")
    system_abnormality = _diag_dict(diag, "system_abnormality")

    sampling_ok = bool(features.get("sampling_ok", features.get("sampling_ok_40hz", False)))
    sampling_condition = str(features.get("sampling_condition", "unknown"))
    axis_mode = str(summary.get("axis_mapping_mode", features.get("axis_mapping_mode", "default")))
    axis_signature = str(summary.get("axis_mapping_signature", features.get("axis_mapping_signature", axis_mapping_signature(None))))
    axis_short, axis_long = _axis_description(mapping, axis_mode)
    screening_status = str(screening.get("status") or diag.get("status") or "unknown").strip() or "unknown"
    data_quality = "这段数据适合进入主链路判断" if sampling_ok else f"这段数据只能保守解释，原因：{_sampling_condition_text(sampling_condition)}"
    row_origin = "已优先使用真实采样点绘图" if used_new_only else "没有足够多的真实采样标记，本次直接使用原始记录绘图"
    quality_rows = [
        ["这段数据的采样频率", f"{float(features.get('fs_hz', 0.0)):.2f} Hz"],
        ["这段数据的时长", f"{float(features.get('duration_s', 0.0)):.2f} 秒"],
        ["可用数据量", f"{int(features.get('n', 0))} 个有效点 / {int(features.get('n_raw', 0))} 个原始点"],
        ["当前是否适合判断", data_quality],
        ["这张图使用的数据", row_origin],
        ["系统识别到的数据条件", _sampling_condition_text(str(features.get("sampling_condition", "unknown")))],
    ]

    lateral_ratio = float(features.get("lateral_ratio", 0.0))
    lat_dom_freq_hz = float(features.get("lat_dom_freq_hz", 0.0))
    lat_low_band_ratio = float(features.get("lat_low_band_ratio", 0.0))
    anomaly_rows = [
        ["横向摆动强度", f"{lateral_ratio:.4f}", "越高，说明左右方向的晃动相对更明显"],
        ["主摆动节奏", f"{lat_dom_freq_hz:.4f} Hz", "越低，越像慢速摆动而不是高频抖动"],
        ["低频摆动占比", f"{lat_low_band_ratio:.4f}", "越高，说明能量更集中在慢摆区间"],
        ["相对基线异常分", f"{float(system_abnormality.get('score', 0.0)):.1f}", "这是统一异常门给出的结果，只回答“像不像健康状态”"],
        ["异常命中特征数", f"{int(system_abnormality.get('shared_hits', 0))}/{int(system_abnormality.get('shared_feature_total', 0))}，强命中 {int(system_abnormality.get('shared_strong_hits', 0))}", "命中越多，说明和健康基线偏离越明显"],
        ["系统当前判断", _screening_status_text(screening_status), "最终仍以统一决策层为准"],
    ]
    feature_labels = {
        "a_rms_ac": "加速度 RMS",
        "a_p2p": "加速度峰峰值",
        "g_std": "角速度波动",
        "a_peak_std": "局部峰值离散度",
        "a_pca_primary_ratio": "主方向能量占比",
        "a_band_log_ratio_0_5_over_5_20": "低/高频能量比",
        "lateral_ratio": "横向占比",
        "lat_dom_freq_hz": "横向主频",
        "lat_low_band_ratio": "横向低频占比",
        "z_peak_ratio": "竖向谱峰集中度",
    }
    top_deviation_rows = [
        [
            feature_labels.get(str(item.get("key", "")), str(item.get("key", ""))),
            f"{float(item.get('value', 0.0)):.4f}",
            f"{float(item.get('median', 0.0)):.4f}",
            f"{float(item.get('z', 0.0)):.2f}",
            f"{float(item.get('score', 0.0)):.1f}",
        ]
        for item in system_abnormality.get("top_deviations", [])
        if isinstance(item, dict)
    ]

    lines = [
        "## 怎么读这些图",
        "",
        "### 传感器安装方向说明",
        f"- {axis_short}。",
        f"- 系统当前识别的安装方向是：`{axis_signature}`。",
        f"- {axis_long}",
        "",
        "### 这段数据是否适合判断",
        _markdown_table(["项目", "内容"], quality_rows),
        "",
        "### 相对健康基线偏离卡片",
        _markdown_table(["指标", "当前值", "解释"], anomaly_rows),
    ]
    if top_deviation_rows:
        lines.extend(
            [
                "",
                "### 偏离最明显的特征",
                _markdown_table(["特征", "当前值", "基线中位数", "偏离 z", "异常分"], top_deviation_rows),
            ]
        )
    return "\n".join(lines).strip()


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
    diagnosis_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_rows, used_new_only = _pick_effective_rows(rows)
    mapping = _axis_mapping_for_waveforms(diagnosis_result)
    features = build_feature_pack(rows, axis_mapping=mapping)
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
        "加速度三轴波形",
        [
            {"label": "Ax", "color": "#1F77B4", "xs": x_acc, "ys": ax},
            {"label": "Ay", "color": "#FF7F0E", "xs": x_acc, "ys": ay},
            {"label": "Az", "color": "#2CA02C", "xs": x_acc, "ys": az},
        ],
        width=width,
        height=height,
    )
    acceleration_chart = _echarts_block(
        "加速度三轴波形",
        [
            {"label": "Ax", "color": "#1F77B4", "xs": x_acc, "ys": ax},
            {"label": "Ay", "color": "#FF7F0E", "xs": x_acc, "ys": ay},
            {"label": "Az", "color": "#2CA02C", "xs": x_acc, "ys": az},
        ],
    )
    gyroscope = _plot_block(
        "角速度三轴波形",
        [
            {"label": "Gx", "color": "#9467BD", "xs": x_gyr, "ys": gx},
            {"label": "Gy", "color": "#D62728", "xs": x_gyr, "ys": gy},
            {"label": "Gz", "color": "#8C564B", "xs": x_gyr, "ys": gz},
        ],
        width=width,
        height=height,
    )
    gyroscope_chart = _echarts_block(
        "角速度三轴波形",
        [
            {"label": "Gx", "color": "#9467BD", "xs": x_gyr, "ys": gx},
            {"label": "Gy", "color": "#D62728", "xs": x_gyr, "ys": gy},
            {"label": "Gz", "color": "#8C564B", "xs": x_gyr, "ys": gz},
        ],
    )
    magnitude = _plot_block(
        "合成加速度幅值",
        [
            {"label": "A_mag", "color": "#17BECF", "xs": x_mag, "ys": amag},
        ],
        width=width,
        height=height,
    )
    magnitude_chart = _echarts_block(
        "合成加速度幅值",
        [
            {"label": "A_mag", "color": "#17BECF", "xs": x_mag, "ys": amag},
        ],
    )
    low_frequency_spectrum, low_frequency_spectrum_chart = _build_low_frequency_spectrum(
        effective_rows,
        mapping=mapping,
        fs_hz=float(features.get("fs_hz", 0.0)),
        width=width,
        height=height,
    )
    insight_markdown = _build_insight_markdown(
        features=features,
        diagnosis_result=diagnosis_result,
        mapping=mapping,
        used_new_only=used_new_only,
    )

    markdown = "\n".join(
        [
            "## 波形图",
            "",
            low_frequency_spectrum["markdown"],
            "",
            acceleration["markdown"],
            "",
            gyroscope["markdown"],
            "",
            magnitude["markdown"],
        ]
    )
    markdown_echarts = "\n".join(
        [
            "## 波形图",
            "",
            low_frequency_spectrum_chart["markdown"],
            "",
            acceleration_chart["markdown"],
            "",
            gyroscope_chart["markdown"],
            "",
            magnitude_chart["markdown"],
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
            "sampling_ok": bool(features.get("sampling_ok", features.get("sampling_ok_40hz", False))),
            "sampling_ok_40hz": bool(features.get("sampling_ok_40hz", False)),
            "sampling_condition": str(features.get("sampling_condition", "unknown")),
            "axis_mapping_mode": str(features.get("axis_mapping_mode", "default")),
            "axis_mapping_signature": str(features.get("axis_mapping_signature", axis_mapping_signature(None))),
        },
        "axis_mapping": {
            "mode": str(features.get("axis_mapping_mode", "default")),
            "signature": str(features.get("axis_mapping_signature", axis_mapping_signature(None))),
            "vertical_axis": str(mapping["vertical"]),
            "lateral_axes": [str(mapping["lateral_x"]), str(mapping["lateral_y"])],
        },
        "sampling": {
            "sampling_ok": bool(features.get("sampling_ok", features.get("sampling_ok_40hz", False))),
            "sampling_ok_40hz": bool(features.get("sampling_ok_40hz", False)),
            "sampling_condition": str(features.get("sampling_condition", "unknown")),
            "sampling_label": _sampling_condition_text(str(features.get("sampling_condition", "unknown"))),
        },
        "insight_markdown": insight_markdown,
        "plots": {
            "acceleration": acceleration,
            "gyroscope": gyroscope,
            "acceleration_magnitude": magnitude,
            "low_frequency_spectrum": low_frequency_spectrum,
        },
        "echarts": {
            "acceleration": acceleration_chart,
            "gyroscope": gyroscope_chart,
            "acceleration_magnitude": magnitude_chart,
            "low_frequency_spectrum": low_frequency_spectrum_chart,
        },
        "markdown": markdown,
        "markdown_echarts": markdown_echarts,
    }
