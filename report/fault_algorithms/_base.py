from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any, Callable, Optional


EPS = 1e-9


def parse_float(value: Any) -> Optional[float]:
    """把 CSV / JSON 里的杂项输入统一转成 float，失败时返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() == "none":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(value: Any) -> Optional[int]:
    """在容忍字符串和浮点输入的前提下，提取一个稳定的整数值。"""
    fv = parse_float(value)
    if fv is None:
        return None
    return int(round(fv))


def clamp(value: float, lo: float, hi: float) -> float:
    """把数值裁剪到固定区间，避免打分和比值越界。"""
    return max(lo, min(hi, value))


def ratio_to_100(value: float, low: float, high: float) -> float:
    """把不同量纲的特征按经验区间压成 0~100 分，便于规则层组合。"""
    if high <= low:
        return 0.0
    return 100.0 * clamp((value - low) / (high - low), 0.0, 1.0)


def robust_fit(values: list[float]) -> tuple[float, float]:
    """用 median + MAD 拟合健康基线中心和尺度，减少离群窗口的影响。"""
    clean = [float(v) for v in values if math.isfinite(float(v))]
    if not clean:
        return 0.0, 1.0
    med = statistics.median(clean)
    abs_dev = [abs(v - med) for v in clean]
    mad = statistics.median(abs_dev) if abs_dev else 0.0
    if mad > EPS:
        scale = 1.4826 * mad
    else:
        std = statistics.pstdev(clean) if len(clean) > 1 else 0.0
        scale = max(std, 1e-6)
    return float(med), float(scale)


def build_feature_baseline(
    feature_rows: list[dict[str, Any]],
    keys: tuple[str, ...],
    *,
    min_samples: int = 8,
) -> dict[str, Any]:
    """按选定特征构建基线统计，用于后续“相对健康状态”的判断。"""
    eligible_rows = [row for row in feature_rows if parse_int(row.get("n")) is not None and int(parse_int(row.get("n")) or 0) >= min_samples]
    rows_to_use = eligible_rows if len(eligible_rows) >= 3 else feature_rows
    stats: dict[str, dict[str, float]] = {}
    for key in keys:
        values: list[float] = []
        for row in rows_to_use:
            value = parse_float(row.get(key))
            if value is None or not math.isfinite(value):
                continue
            values.append(float(value))
        if len(values) < 3:
            continue
        med, scale = robust_fit(values)
        stats[key] = {
            "median": float(med),
            "scale": float(scale),
            "count": float(len(values)),
        }
    return {
        "stats": stats,
        "count": int(len(rows_to_use)),
        "source_count": int(len(feature_rows)),
        "eligible_count": int(len(eligible_rows)),
        "min_samples": int(min_samples),
        "keys": list(keys),
    }


def build_clean_feature_baseline(
    feature_rows: list[dict[str, Any]],
    keys: tuple[str, ...],
    *,
    min_samples: int = 8,
    z_threshold: float = 3.5,
    max_drop_ratio: float = 0.20,
) -> dict[str, Any]:
    """在构建基线前先剔除明显异常的“伪健康”窗口，避免污染 baseline。"""
    baseline = build_feature_baseline(feature_rows, keys, min_samples=min_samples)
    stats = baseline.get("stats", {}) if isinstance(baseline.get("stats"), dict) else {}
    eligible_rows = [row for row in feature_rows if parse_int(row.get("n")) is not None and int(parse_int(row.get("n")) or 0) >= min_samples]
    rows_to_use = eligible_rows if len(eligible_rows) >= 3 else feature_rows
    if len(rows_to_use) < 5 or not stats:
        baseline["cleaning"] = {
            "mode": "skipped",
            "candidate_count": int(len(rows_to_use)),
            "kept_count": int(len(rows_to_use)),
            "dropped_count": 0,
            "z_threshold": float(z_threshold),
        }
        return baseline

    scored_rows: list[tuple[float, dict[str, Any]]] = []
    for row in rows_to_use:
        z_values: list[float] = []
        for key, item in stats.items():
            value = parse_float(row.get(key))
            if value is None or not math.isfinite(value):
                continue
            median = parse_float(item.get("median"))
            scale = parse_float(item.get("scale"))
            if median is None or scale is None or scale <= EPS:
                continue
            z_values.append(abs((float(value) - float(median)) / max(float(scale), 1e-6)))
        scored_rows.append((safe_mean(z_values), row))

    outliers = [(score, row) for score, row in scored_rows if score > float(z_threshold)]
    drop_ratio = len(outliers) / max(1, len(scored_rows))
    if not outliers:
        kept_rows = [row for _, row in scored_rows]
        mode = "none"
    elif drop_ratio <= max(0.0, float(max_drop_ratio)):
        kept_rows = [row for score, row in scored_rows if score <= float(z_threshold)]
        mode = "threshold"
    else:
        keep_n = max(3, int(math.ceil(len(scored_rows) * (1.0 - max(0.0, float(max_drop_ratio))))))
        kept_rows = [row for _, row in sorted(scored_rows, key=lambda item: item[0])[:keep_n]]
        mode = "top80"

    cleaned = build_feature_baseline(kept_rows, keys, min_samples=min_samples)
    cleaned["cleaning"] = {
        "mode": mode,
        "candidate_count": int(len(scored_rows)),
        "kept_count": int(len(kept_rows)),
        "dropped_count": int(max(0, len(scored_rows) - len(kept_rows))),
        "z_threshold": float(z_threshold),
    }
    return cleaned


def safe_mean(xs: list[float]) -> float:
    """空输入安全均值。"""
    if not xs:
        return 0.0
    return float(sum(xs) / len(xs))


def safe_std(xs: list[float]) -> float:
    """空输入安全标准差。"""
    if len(xs) < 2:
        return 0.0
    return float(statistics.pstdev(xs))


def safe_p2p(xs: list[float]) -> float:
    """空输入安全峰峰值。"""
    if not xs:
        return 0.0
    return float(max(xs) - min(xs))


def safe_percentile(xs: list[float], q: float) -> float:
    """轻量百分位实现，避免引入额外依赖。"""
    if not xs:
        return 0.0
    clean = sorted(float(x) for x in xs)
    if len(clean) == 1:
        return clean[0]
    q = clamp(float(q), 0.0, 100.0)
    pos = (len(clean) - 1) * q / 100.0
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return clean[lo]
    frac = pos - lo
    return float(clean[lo] * (1.0 - frac) + clean[hi] * frac)


def qspread(xs: list[float], low_q: float = 5.0, high_q: float = 95.0) -> float:
    """分位带宽，用于描述波动离散度，较标准差更抗尖峰。"""
    if not xs:
        return 0.0
    return float(safe_percentile(xs, high_q) - safe_percentile(xs, low_q))


def _hann_window(size: int) -> list[float]:
    """频域扫描前的 Hann 窗，减少谱泄漏。"""
    if size <= 1:
        return [1.0] * max(1, size)
    return [0.5 - 0.5 * math.cos((2.0 * math.pi * idx) / (size - 1)) for idx in range(size)]


def _goertzel_power(xs: list[float], fs_hz: float, target_hz: float) -> float:
    """在单个目标频点上估算能量，适合纯 Python 做稀疏频率扫描。"""
    if len(xs) < 4 or fs_hz <= EPS or target_hz <= 0.0 or target_hz >= fs_hz / 2.0:
        return 0.0
    omega = 2.0 * math.pi * target_hz / fs_hz
    coeff = 2.0 * math.cos(omega)
    prev = 0.0
    prev2 = 0.0
    for value in xs:
        cur = value + coeff * prev - prev2
        prev2 = prev
        prev = cur
    power = prev2 * prev2 + prev * prev - coeff * prev * prev2
    return float(max(0.0, power))


def _scan_spectrum(
    xs: list[float],
    fs_hz: float,
    *,
    freq_min_hz: float = 0.3,
    freq_max_hz: float = 8.0,
    step_hz: float = 0.1,
) -> list[tuple[float, float]]:
    """把时域信号扫描成稀疏频谱，供主频/低频占比等频域特征使用。"""
    if len(xs) < 8 or fs_hz <= EPS or step_hz <= EPS:
        return []
    centered = [float(value) - safe_mean(xs) for value in xs]
    if not any(abs(value) > EPS for value in centered):
        return []
    window = _hann_window(len(centered))
    windowed = [centered[idx] * window[idx] for idx in range(len(centered))]
    max_freq = min(float(freq_max_hz), fs_hz / 2.0 - 0.05)
    if max_freq <= freq_min_hz:
        return []

    bins: list[tuple[float, float]] = []
    freq = max(0.05, float(freq_min_hz))
    while freq <= max_freq + 1e-9:
        bins.append((round(freq, 4), _goertzel_power(windowed, fs_hz=fs_hz, target_hz=freq)))
        freq += step_hz
    return bins


def spectral_features(
    xs: list[float],
    fs_hz: float,
    *,
    low_band_hz: tuple[float, float] = (0.3, 1.8),
    freq_min_hz: float = 0.3,
    freq_max_hz: float = 8.0,
    step_hz: float = 0.1,
) -> dict[str, float]:
    """提取主频、谱峰集中度和低频占比，用于 rope / rubber 的频域画像。"""
    bins = _scan_spectrum(xs, fs_hz, freq_min_hz=freq_min_hz, freq_max_hz=freq_max_hz, step_hz=step_hz)
    if not bins:
        return {"dom_freq_hz": 0.0, "peak_ratio": 0.0, "low_band_ratio": 0.0}
    total_power = sum(power for _, power in bins)
    if total_power <= EPS:
        return {"dom_freq_hz": 0.0, "peak_ratio": 0.0, "low_band_ratio": 0.0}
    dom_freq_hz, dom_power = max(bins, key=lambda item: item[1])
    low_lo, low_hi = low_band_hz
    low_band_power = sum(power for freq, power in bins if low_lo <= freq <= low_hi)
    return {
        "dom_freq_hz": float(dom_freq_hz),
        "peak_ratio": float(dom_power / total_power),
        "low_band_ratio": float(low_band_power / total_power),
    }


def rms_ac(xs: list[float]) -> float:
    """去均值后的 RMS，强调动态波动而不是静态偏置。"""
    if not xs:
        return 0.0
    mu = safe_mean(xs)
    return float(math.sqrt(sum((x - mu) * (x - mu) for x in xs) / len(xs)))


def crest_factor(xs: list[float]) -> float:
    """峰值相对 RMS 的尖锐程度，用来识别冲击/拍击型信号。"""
    if not xs:
        return 0.0
    peak = max(abs(x) for x in xs)
    rms = math.sqrt(sum(x * x for x in xs) / len(xs))
    if rms <= EPS:
        return 0.0
    return float(peak / rms)


def kurtosis_excess(xs: list[float]) -> float:
    """超额峰度，补充判断信号是否比正常运行更尖峰、更重尾。"""
    n = len(xs)
    if n < 4:
        return 0.0
    mu = safe_mean(xs)
    m2 = sum((x - mu) ** 2 for x in xs) / n
    if m2 <= EPS:
        return 0.0
    m4 = sum((x - mu) ** 4 for x in xs) / n
    return float(m4 / (m2 * m2) - 3.0)


def zero_cross_rate(xs: list[float], duration_s: float) -> float:
    """过零率是低成本的快慢代理量，可粗略反映摆动节奏。"""
    if len(xs) < 2 or duration_s <= EPS:
        return 0.0
    cnt = 0
    prev = xs[0]
    for cur in xs[1:]:
        if (prev <= 0 < cur) or (prev >= 0 > cur):
            cnt += 1
        prev = cur
    return float(cnt / duration_s)


def correlation(xs: list[float], ys: list[float]) -> float:
    """线性相关系数，用于衡量轴间或加速度/角速度是否联动增强。"""
    if len(xs) != len(ys) or len(xs) < 3:
        return 0.0
    mx = safe_mean(xs)
    my = safe_mean(ys)
    sx = math.sqrt(sum((x - mx) * (x - mx) for x in xs))
    sy = math.sqrt(sum((y - my) * (y - my) for y in ys))
    if sx <= EPS or sy <= EPS:
        return 0.0
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return float(cov / (sx * sy))


def diff_rms(xs: list[float], fs_hz: float) -> float:
    """一阶差分 RMS，可近似描述 jerk/急变强度。"""
    if len(xs) < 2 or fs_hz <= EPS:
        return 0.0
    diffs = [(xs[i] - xs[i - 1]) * fs_hz for i in range(1, len(xs))]
    return float(math.sqrt(sum(d * d for d in diffs) / len(diffs)))


def count_peaks(xs: list[float], threshold: float) -> int:
    """统计超过阈值的局部峰数量，用来刻画尖峰事件密度。"""
    if len(xs) < 3:
        return 0
    cnt = 0
    for i in range(1, len(xs) - 1):
        if xs[i] > xs[i - 1] and xs[i] >= xs[i + 1] and xs[i] >= threshold:
            cnt += 1
    return cnt


def score_to_level(score: float) -> str:
    """把连续分数映射成统一告警等级，便于各算法输出口径一致。"""
    if score >= 80:
        return "alarm"
    if score >= 60:
        return "warning"
    if score >= 35:
        return "watch"
    return "normal"


def load_rows(path: Path) -> list[dict[str, str]]:
    """读取原始 CSV 为逐行字典，作为特征提取的统一输入。"""
    with path.open("r", encoding="utf-8", newline="") as fp:
        return list(csv.DictReader(fp))


def _extract_series(rows: list[dict[str, str]], names: tuple[str, ...]) -> list[float]:
    """按候选列名提取一条数值序列，兼容历史字段别名。"""
    out: list[float] = []
    for row in rows:
        value = None
        for name in names:
            value = parse_float(row.get(name))
            if value is not None:
                break
        out.append(float(value if value is not None else 0.0))
    return out


def _extract_ts_ms(rows: list[dict[str, str]]) -> list[int]:
    """提取时间戳；缺失时退化为行号，至少保证时序特征可计算。"""
    ts: list[int] = []
    for idx, row in enumerate(rows):
        value = parse_int(row.get("ts_ms"))
        if value is None:
            value = parse_int(row.get("data_ts_ms"))
        if value is None:
            value = idx
        ts.append(int(value))
    return ts


def _pick_effective_rows(rows: list[dict[str, str]], min_real_rows: int = 8) -> tuple[list[dict[str, str]], bool, float]:
    """优先保留真实采样帧，尽量把补点/重复帧对特征的污染降到最低。"""
    if not rows:
        return rows, False, 0.0
    raw_n = len(rows)
    real_rows: list[dict[str, str]] = []
    has_flag = False
    for row in rows:
        flag = parse_int(row.get("is_new_frame"))
        if flag is None:
            continue
        has_flag = True
        if flag == 1:
            real_rows.append(row)

    if has_flag and len(real_rows) >= min_real_rows:
        ratio = len(real_rows) / max(1, raw_n)
        return real_rows, True, float(ratio)
    return rows, False, 1.0


def _duration_seconds(ts_ms: list[int]) -> float:
    """由时间戳估算窗口时长，供采样率和频域特征复用。"""
    if len(ts_ms) < 2:
        return 0.0
    d = ts_ms[-1] - ts_ms[0]
    if d <= 0:
        return 0.0
    return float(d / 1000.0)


def _channel_feature_pack(xs: list[float], fs_hz: float) -> dict[str, float]:
    mean = safe_mean(xs)
    std = safe_std(xs)
    cv = float(std / max(abs(mean), EPS)) if abs(mean) > EPS else 0.0
    # 单通道统计量用于描述该方向的波动强度、摆幅、尖峰和急变程度。
    # 它们本身不直接对应具体故障，但会被上层算法组合成竖向/横向/阻尼变化等画像。
    return {
        "mean": float(mean),
        "std": float(std),
        "p2p": float(safe_p2p(xs)),
        "rms_ac": float(rms_ac(xs)),
        "kurt": float(kurtosis_excess(xs)),
        "jerk_rms": float(diff_rms(xs, fs_hz=fs_hz)),
        "qspread": float(qspread(xs)),
        "cv": float(cv),
    }


def build_feature_pack(rows: list[dict[str, str]]) -> dict[str, Any]:
    raw_n = len(rows)
    effective_rows, used_new_only, new_ratio = _pick_effective_rows(rows)
    n = len(effective_rows)

    ax = _extract_series(effective_rows, ("Ax", "AX"))
    ay = _extract_series(effective_rows, ("Ay", "AY"))
    az = _extract_series(effective_rows, ("Az", "AZ"))

    gx = _extract_series(effective_rows, ("Gx", "GX"))
    gy = _extract_series(effective_rows, ("Gy", "GY"))
    gz = _extract_series(effective_rows, ("Gz", "GZ"))

    t_arr = _extract_series(effective_rows, ("t", "TEMPB", "TEMP"))
    sx = _extract_series(effective_rows, ("sx", "DX"))
    sy = _extract_series(effective_rows, ("sy", "DY"))
    sz = _extract_series(effective_rows, ("sz", "DZ"))
    fx = _extract_series(effective_rows, ("fx", "HZX"))
    fy = _extract_series(effective_rows, ("fy", "HZY"))
    fz = _extract_series(effective_rows, ("fz", "HZZ"))

    # 合成幅值先把三轴原始波形压成“整体振动/整体转动”两个基底，
    # 后续共同异常强度特征大多从这里展开。
    a_mag = []
    g_mag = []
    for i in range(n):
        a = math.sqrt(ax[i] * ax[i] + ay[i] * ay[i] + az[i] * az[i])
        g = math.sqrt(gx[i] * gx[i] + gy[i] * gy[i] + gz[i] * gz[i])
        a_mag.append(a)
        g_mag.append(g)

    ts_ms = _extract_ts_ms(effective_rows)
    duration_s = _duration_seconds(ts_ms)
    fs_hz = float((n - 1) / duration_s) if n > 1 and duration_s > EPS else 0.0

    # 总体幅值特征：先回答“相对健康基线，整体振动是不是变强了”。
    a_std = safe_std(a_mag)
    a_mean = safe_mean(a_mag)
    a_rms_ac = rms_ac(a_mag)
    a_p2p = safe_p2p(a_mag)
    a_crest = crest_factor(a_mag)
    a_kurt = kurtosis_excess(a_mag)

    g_std = safe_std(g_mag)
    g_mean = safe_mean(g_mag)
    g_p2p = safe_p2p(g_mag)

    # 事件/粗糙度特征：看信号是慢摆、快抖还是尖峰冲击。
    threshold = a_mean + 3.0 * max(a_std, EPS)
    peak_count = count_peaks(a_mag, threshold=threshold)
    peak_rate_hz = float(peak_count / duration_s) if duration_s > EPS else 0.0

    zc_rate = zero_cross_rate([v - safe_mean(az) for v in az], duration_s=duration_s)
    jerk_rms = diff_rms(a_mag, fs_hz=fs_hz)

    # 单轴特征包用于保留“具体是哪个方向在变化”，
    # 上层 rope / rubber 规则会重点使用 az_*、ax/ay_* 等前缀特征。
    ax_pack = _channel_feature_pack(ax, fs_hz=fs_hz)
    ay_pack = _channel_feature_pack(ay, fs_hz=fs_hz)
    az_pack = _channel_feature_pack(az, fs_hz=fs_hz)
    mag_pack = _channel_feature_pack(a_mag, fs_hz=fs_hz)

    ax_std = safe_std(ax)
    ay_std = safe_std(ay)
    az_std = safe_std(az)
    side_std = 0.5 * (ax_std + ay_std)
    lateral_ratio = float(side_std / max(az_std, EPS))

    # 方向比例和耦合特征：用于区分“更偏横向摆动”还是“更偏竖向传递”，
    # 同时观察加速度与角速度、轴与轴之间是否出现联动增强。
    ag_corr = abs(correlation(a_mag, g_mag))
    gx_ax_corr = abs(correlation(gx, ax))
    gy_ay_corr = abs(correlation(gy, ay))
    corr_xy = correlation(ax, ay)
    corr_xz = correlation(ax, az)
    corr_yz = correlation(ay, az)

    # 能量重分配特征：对 rubber 更重要，也可辅助判断 rope 是否只是共同异常。
    energy_x_over_y = float(ax_pack["rms_ac"] / max(ay_pack["rms_ac"], EPS))
    energy_z_over_xy = float(az_pack["rms_ac"] / max(0.5 * (ax_pack["rms_ac"] + ay_pack["rms_ac"]), EPS))
    ax_mean = safe_mean(ax)
    ay_mean = safe_mean(ay)
    az_mean = safe_mean(az)
    lateral_signal = [
        math.sqrt((ax[idx] - ax_mean) * (ax[idx] - ax_mean) + (ay[idx] - ay_mean) * (ay[idx] - ay_mean))
        for idx in range(n)
    ]
    vertical_signal = [az[idx] - az_mean for idx in range(n)]
    # 频域特征把时域波形转成“主频、谱峰集中度、低频占比”。
    # 当前实现里：
    # - lateral 频域更偏向钢丝绳相关的横向摆动/低频画像
    # - vertical 频域更偏向橡胶圈或支撑刚度变化的竖向画像
    lateral_spectrum = spectral_features(lateral_signal, fs_hz=fs_hz)
    vertical_spectrum = spectral_features(vertical_signal, fs_hz=fs_hz)

    temp_rise = 0.0
    if t_arr:
        temp_rise = float(max(t_arr) - min(t_arr))

    feature = {
        "n_raw": raw_n,
        "n": n,
        "used_new_only": used_new_only,
        "new_ratio": float(new_ratio),
        "duration_s": float(duration_s),
        "fs_hz": float(fs_hz),
        "a_mean": float(a_mean),
        "a_std": float(a_std),
        "a_rms_ac": float(a_rms_ac),
        "a_p2p": float(a_p2p),
        "a_crest": float(a_crest),
        "a_kurt": float(a_kurt),
        "a_max": float(max(a_mag) if a_mag else 0.0),
        "g_mean": float(g_mean),
        "g_std": float(g_std),
        "g_p2p": float(g_p2p),
        "g_max": float(max(g_mag) if g_mag else 0.0),
        "jerk_rms": float(jerk_rms),
        "peak_rate_hz": float(peak_rate_hz),
        "zc_rate_hz": float(zc_rate),
        "lateral_ratio": float(lateral_ratio),
        "ag_corr": float(ag_corr),
        "gx_ax_corr": float(gx_ax_corr),
        "gy_ay_corr": float(gy_ay_corr),
        "corr_xy": float(corr_xy),
        "corr_xz": float(corr_xz),
        "corr_yz": float(corr_yz),
        "energy_x_over_y": float(energy_x_over_y),
        "energy_z_over_xy": float(energy_z_over_xy),
        "lat_dom_freq_hz": float(lateral_spectrum["dom_freq_hz"]),
        "lat_peak_ratio": float(lateral_spectrum["peak_ratio"]),
        "lat_low_band_ratio": float(lateral_spectrum["low_band_ratio"]),
        "z_dom_freq_hz": float(vertical_spectrum["dom_freq_hz"]),
        "z_peak_ratio": float(vertical_spectrum["peak_ratio"]),
        "z_low_band_ratio": float(vertical_spectrum["low_band_ratio"]),
        "temp_rise": float(temp_rise),
        "sx_std": float(safe_std(sx)),
        "sy_std": float(safe_std(sy)),
        "sz_std": float(safe_std(sz)),
        "fx_std": float(safe_std(fx)),
        "fy_std": float(safe_std(fy)),
        "fz_std": float(safe_std(fz)),
    }

    for prefix, pack in (("ax", ax_pack), ("ay", ay_pack), ("az", az_pack), ("mag", mag_pack)):
        for key, value in pack.items():
            feature[f"{prefix}_{key}"] = float(value)
    return feature


def build_result(
    *,
    fault_type: str,
    score: float,
    reasons: list[str],
    features: dict[str, Any],
    min_samples: int = 12,
    penalize_low_fs: bool = True,
) -> dict[str, Any]:
    score = clamp(float(score), 0.0, 100.0)
    n = int(features.get("n", 0))
    fs_hz = float(features.get("fs_hz", 0.0))

    quality = 1.0
    if n < min_samples:
        quality *= n / max(1.0, float(min_samples))
    if penalize_low_fs and fs_hz > 0 and fs_hz < 2.0:
        quality *= 0.55
    if features.get("used_new_only"):
        quality *= 1.0
    else:
        # 不是 new-only 的情况下，可能含补点，降低可信度
        quality *= 0.85

    quality = clamp(quality, 0.2, 1.0)
    final_score = score * quality
    level = score_to_level(final_score)

    return {
        "fault_type": fault_type,
        "score": round(final_score, 2),
        "level": level,
        "triggered": final_score >= 60.0,
        "quality_factor": round(quality, 3),
        "reasons": reasons,
        "feature_snapshot": {
            "n": n,
            "fs_hz": round(fs_hz, 3),
            "duration_s": round(float(features.get("duration_s", 0.0)), 3),
            "a_std": round(float(features.get("a_std", 0.0)), 6),
            "a_p2p": round(float(features.get("a_p2p", 0.0)), 6),
            "a_crest": round(float(features.get("a_crest", 0.0)), 4),
            "a_kurt": round(float(features.get("a_kurt", 0.0)), 4),
            "g_std": round(float(features.get("g_std", 0.0)), 6),
            "jerk_rms": round(float(features.get("jerk_rms", 0.0)), 6),
            "peak_rate_hz": round(float(features.get("peak_rate_hz", 0.0)), 6),
            "lateral_ratio": round(float(features.get("lateral_ratio", 0.0)), 4),
            "ag_corr": round(float(features.get("ag_corr", 0.0)), 4),
            "lat_dom_freq_hz": round(float(features.get("lat_dom_freq_hz", 0.0)), 4),
            "lat_peak_ratio": round(float(features.get("lat_peak_ratio", 0.0)), 4),
            "z_dom_freq_hz": round(float(features.get("z_dom_freq_hz", 0.0)), 4),
            "z_peak_ratio": round(float(features.get("z_peak_ratio", 0.0)), 4),
            "used_new_only": bool(features.get("used_new_only", False)),
            "new_ratio": round(float(features.get("new_ratio", 0.0)), 4),
        },
    }


def run_detector_cli(detector: Callable[[dict[str, Any]], dict[str, Any]], description: str) -> int:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--input", required=True, help="输入CSV路径")
    parser.add_argument("--pretty", action="store_true", help="格式化输出JSON")
    args = parser.parse_args()

    rows = load_rows(Path(args.input))
    features = build_feature_pack(rows)
    result = detector(features)

    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0
