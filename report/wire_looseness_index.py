#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

DEFAULT_GLOB = str(PROJECT_ROOT / "data" / "captures" / "vibration_30s_20260303_*.csv")
DEFAULT_MODEL_PATH = BASE_DIR / "wire_looseness_model_latest.json"

# Candidate features for generic looseness scoring.
CANDIDATE_FEATURES = [
    "mag_std",
    "mag_p2p",
    "mag_rms_ac",
    "mag_qspread",
    "mag_kurt",
    "mag_jerk_rms",
    "ax_std",
    "ay_std",
    "az_std",
    "gx_std",
    "gy_std",
    "gz_std",
    "temp_std",
]


def parse_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    raw = str(text or "").strip()
    if not raw:
        return ranges
    for token in raw.split(","):
        item = token.strip()
        if not item:
            continue
        if "-" in item:
            a, b = item.split("-", 1)
            lo = int(a.strip())
            hi = int(b.strip())
        else:
            lo = int(item)
            hi = lo
        lo = max(0, min(59, lo))
        hi = max(0, min(59, hi))
        if lo > hi:
            lo, hi = hi, lo
        ranges.append((lo, hi))
    return ranges


def in_ranges(minute: int, ranges: list[tuple[int, int]]) -> bool:
    return any(lo <= minute <= hi for lo, hi in ranges)


def parse_hhmmss_from_name(path: Path) -> Optional[tuple[int, int, int]]:
    m = re.search(r"vibration_30s_\d{8}_(\d{6})$", path.stem)
    if not m:
        return None
    hhmmss = m.group(1)
    hh = int(hhmmss[0:2])
    mm = int(hhmmss[2:4])
    ss = int(hhmmss[4:6])
    return hh, mm, ss


def pick_label(
    *,
    hour: int,
    minute: int,
    target_hour: int,
    normal_ranges: list[tuple[int, int]],
    loose1_ranges: list[tuple[int, int]],
    loose2_ranges: list[tuple[int, int]],
) -> Optional[str]:
    if target_hour >= 0 and hour != target_hour:
        return None
    if in_ranges(minute, normal_ranges):
        return "normal"
    if in_ranges(minute, loose1_ranges):
        return "loose_1"
    if in_ranges(minute, loose2_ranges):
        return "loose_2"
    return None


def to_numeric_col(df: pd.DataFrame, names: list[str]) -> np.ndarray:
    for col in names:
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
    return np.full(len(df), np.nan, dtype=float)


def parse_time_s(df: pd.DataFrame) -> Optional[np.ndarray]:
    if "ts_ms" in df.columns:
        ts_ms = pd.to_numeric(df["ts_ms"], errors="coerce").to_numpy(dtype=float)
        if np.isfinite(ts_ms).sum() >= 3:
            return ts_ms / 1000.0
    if "ts" in df.columns:
        dt = pd.to_datetime(df["ts"], errors="coerce")
        if dt.notna().sum() >= 3:
            out = np.full(len(df), np.nan, dtype=float)
            mask = dt.notna().to_numpy()
            out[mask] = (dt[mask].astype("int64") // 10**9).to_numpy(dtype=float)
            return out
    return None


def excess_kurtosis(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < 4:
        return 0.0
    mu = float(np.mean(x))
    m2 = float(np.mean((x - mu) ** 2))
    if m2 < 1e-12:
        return 0.0
    m4 = float(np.mean((x - mu) ** 4))
    return float(m4 / (m2 * m2) - 3.0)


def weighted_jerk_rms(x: np.ndarray, t_s: Optional[np.ndarray]) -> float:
    x = np.asarray(x, dtype=float)
    if x.size < 3:
        return 0.0

    if t_s is not None:
        t = np.asarray(t_s, dtype=float)
        if t.size == x.size:
            dx = np.diff(x)
            dt = np.diff(t)
            valid = np.isfinite(dx) & np.isfinite(dt) & (dt > 1e-9)
            if valid.sum() >= 2:
                v = dx[valid] / dt[valid]
                w = dt[valid]
                denom = float(np.sum(w))
                if denom > 1e-12:
                    return float(np.sqrt(np.sum((v ** 2) * w) / denom))

    # Fallback when timestamps are unavailable/unreliable.
    d = np.diff(x)
    return float(np.sqrt(np.mean(d ** 2))) if d.size > 0 else 0.0


def build_feature_row(path: Path) -> dict[str, Any]:
    df = pd.read_csv(path)
    ax = to_numeric_col(df, ["Ax", "AX"])
    ay = to_numeric_col(df, ["Ay", "AY"])
    az = to_numeric_col(df, ["Az", "AZ"])

    valid = np.isfinite(ax) & np.isfinite(ay) & np.isfinite(az)
    ax, ay, az = ax[valid], ay[valid], az[valid]
    if ax.size < 4:
        raise ValueError(f"{path.name}: too few valid Ax/Ay/Az rows ({ax.size})")

    gx = to_numeric_col(df, ["Gx", "GX"])[valid]
    gy = to_numeric_col(df, ["Gy", "GY"])[valid]
    gz = to_numeric_col(df, ["Gz", "GZ"])[valid]
    temp = to_numeric_col(df, ["t", "T", "temp", "TEMP"])[valid]

    t_all = parse_time_s(df)
    t_s = t_all[valid] if (t_all is not None and len(t_all) == len(df)) else None

    mag = np.sqrt(ax * ax + ay * ay + az * az)
    mag_ac = mag - float(np.mean(mag))

    if t_s is not None:
        t_valid = np.isfinite(t_s)
        if t_valid.sum() >= 2:
            t_span = float(np.max(t_s[t_valid]) - np.min(t_s[t_valid]))
        else:
            t_span = 0.0
    else:
        t_span = 0.0

    row = {
        "file": path.name,
        "path": str(path.resolve()),
        "sample_count": float(len(mag)),
        "duration_s": float(max(0.0, t_span)),
        "mag_mean": float(np.mean(mag)),
        "mag_std": float(np.std(mag)),
        "mag_p2p": float(np.max(mag) - np.min(mag)),
        "mag_rms_ac": float(np.sqrt(np.mean(mag_ac ** 2))),
        "mag_qspread": float(np.percentile(mag, 95) - np.percentile(mag, 5)),
        "mag_kurt": float(excess_kurtosis(mag)),
        "mag_jerk_rms": float(weighted_jerk_rms(mag, t_s)),
        "ax_std": float(np.std(ax)),
        "ay_std": float(np.std(ay)),
        "az_std": float(np.std(az)),
        "gx_std": float(np.std(gx[np.isfinite(gx)])) if np.isfinite(gx).any() else 0.0,
        "gy_std": float(np.std(gy[np.isfinite(gy)])) if np.isfinite(gy).any() else 0.0,
        "gz_std": float(np.std(gz[np.isfinite(gz)])) if np.isfinite(gz).any() else 0.0,
        "temp_std": float(np.std(temp[np.isfinite(temp)])) if np.isfinite(temp).any() else 0.0,
    }

    hhmmss = parse_hhmmss_from_name(path)
    if hhmmss is not None:
        hh, mm, ss = hhmmss
        row["hour"] = hh
        row["minute"] = mm
        row["second"] = ss
        row["time_key"] = f"{hh:02d}:{mm:02d}:{ss:02d}"
    else:
        row["hour"] = -1
        row["minute"] = -1
        row["second"] = -1
        row["time_key"] = "NA"
    return row


def feature_separation(normal_df: pd.DataFrame, loose_df: pd.DataFrame, feature: str) -> tuple[float, float, float, float]:
    n_vals = normal_df[feature].astype(float).to_numpy()
    l_vals = loose_df[feature].astype(float).to_numpy()
    mu_n = float(np.mean(n_vals))
    mu_l = float(np.mean(l_vals))
    sd_n = float(np.std(n_vals))
    sd_l = float(np.std(l_vals))

    gap = abs(mu_l - mu_n)
    pooled = sd_n + sd_l
    scale = max(pooled, 0.20 * gap, 1e-6)
    sep = gap / scale
    return sep, mu_n, mu_l, scale


def select_features(train_df: pd.DataFrame, topk: int) -> pd.DataFrame:
    normal_df = train_df[train_df["label"] == "normal"]
    loose_df = train_df[train_df["label"].isin(["loose_1", "loose_2"])]
    rows: list[dict[str, Any]] = []
    for f in CANDIDATE_FEATURES:
        if f not in train_df.columns:
            continue
        if float(train_df[f].std()) < 1e-12:
            continue
        sep, mu_n, mu_l, scale = feature_separation(normal_df, loose_df, f)
        rows.append(
            {
                "feature": f,
                "separation": float(sep),
                "mu_normal": float(mu_n),
                "mu_loose": float(mu_l),
                "scale": float(scale),
            }
        )
    if not rows:
        raise RuntimeError("failed to select features: no valid candidates")
    sep_df = pd.DataFrame(rows).sort_values("separation", ascending=False).reset_index(drop=True)
    topk = max(3, min(int(topk), len(sep_df)))
    return sep_df.head(topk).copy()


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def score_row(feat: dict[str, Any], model_df: pd.DataFrame, score_thr: float) -> dict[str, Any]:
    dn_list: list[float] = []
    dl_list: list[float] = []
    d1_list: list[float] = []
    d2_list: list[float] = []
    evidence: list[tuple[str, float, float, float, float, float]] = []

    for _, row in model_df.iterrows():
        k = str(row["feature"])
        if k not in feat:
            continue
        x = float(feat[k])
        mu_n = float(row["mu_normal"])
        mu_l = float(row["mu_loose"])
        mu_l1 = float(row["mu_loose_1"])
        mu_l2 = float(row["mu_loose_2"])
        scale = float(row["scale"])

        dn = abs(x - mu_n) / scale
        d1 = abs(x - mu_l1) / scale
        d2 = abs(x - mu_l2) / scale
        dl = min(d1, d2)
        support = dn - dl  # positive => closer to loose template

        dn_list.append(dn)
        dl_list.append(dl)
        d1_list.append(d1)
        d2_list.append(d2)
        evidence.append((k, support, x, mu_n, mu_l1, mu_l2))

    if not dn_list:
        return {
            "looseness_score": 0.0,
            "label": "unknown",
            "dist_normal": float("nan"),
            "dist_loose": float("nan"),
            "gap_normal_minus_loose": float("nan"),
            "evidence_top": "N/A",
        }

    dist_n = float(np.mean(dn_list))
    dist_l = float(np.mean(dl_list))
    dist_l1 = float(np.mean(d1_list))
    dist_l2 = float(np.mean(d2_list))
    gap = dist_n - dist_l
    score = 100.0 * sigmoid(1.8 * gap)
    stage_hint = "loose_1" if dist_l1 <= dist_l2 else "loose_2"

    if score >= score_thr and gap > 0:
        label = "loose"
    elif score <= (100.0 - score_thr) and gap < 0:
        label = "normal_like"
    else:
        label = "uncertain"

    evidence.sort(key=lambda x: x[1], reverse=True)
    top = []
    for k, support, x, mu_n, mu_l1, mu_l2 in evidence[:4]:
        if support <= 0:
            continue
        top.append(f"{k}(x={x:.4g},N={mu_n:.4g},L1={mu_l1:.4g},L2={mu_l2:.4g})")

    return {
        "looseness_score": float(max(0.0, min(100.0, score))),
        "label": label,
        "stage_hint": stage_hint,
        "dist_normal": dist_n,
        "dist_loose": dist_l,
        "dist_loose_1": dist_l1,
        "dist_loose_2": dist_l2,
        "gap_normal_minus_loose": gap,
        "evidence_top": "; ".join(top) if top else "N/A",
    }


def simple_metrics(eval_df: pd.DataFrame) -> dict[str, Any]:
    labeled = eval_df[eval_df["truth_binary"].isin(["normal", "loose"])].copy()
    if labeled.empty:
        return {"accuracy": float("nan"), "support": 0, "confusion": pd.DataFrame()}

    pred_binary = labeled["pred_binary"].astype(str).to_list()
    truth = labeled["truth_binary"].astype(str).to_list()
    total = len(truth)
    correct = sum(1 for t, p in zip(truth, pred_binary) if t == p)
    acc = correct / total if total > 0 else float("nan")
    conf = pd.crosstab(pd.Series(truth, name="truth"), pd.Series(pred_binary, name="pred"))
    return {"accuracy": acc, "support": total, "confusion": conf}


def df_to_text(df: pd.DataFrame, digits: int = 4) -> str:
    out = df.copy()
    num_cols = out.select_dtypes(include=[np.number]).columns
    out[num_cols] = out[num_cols].round(digits)
    return out.to_string()


def build_report(
    *,
    cfg: dict[str, Any],
    labeled_files_df: pd.DataFrame,
    model_df: pd.DataFrame,
    metrics: dict[str, Any],
    result_df: pd.DataFrame,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Wire Looseness Generic Algorithm Report",
        "",
        f"- Generated at: {now}",
        f"- Data glob: `{cfg['data_glob']}`",
        f"- Label ranges (normal): `{cfg['normal_ranges']}`",
        f"- Label ranges (loose_1): `{cfg['loose1_ranges']}`",
        f"- Label ranges (loose_2): `{cfg['loose2_ranges']}`",
        f"- Target hour filter: `{cfg['hour']}` (`-1` means all hours)",
        f"- Decision threshold: `score >= {cfg['score_thr']}` => loose, `<= {100-cfg['score_thr']}` => normal",
        "",
        "## Algorithm Formula",
        "- Step1: extract window-level vibration features from each 30s file.",
        "- Step2: build three templates from labeled ranges: normal, loose_1, loose_2.",
        "- Step3: for each selected feature, compute scaled distances to all templates.",
        "- Step4: define loose distance as `dist_to_loose = min(dist_to_loose_1, dist_to_loose_2)`.",
        "- Step5: aggregate distance gap `gap = mean(dist_to_normal) - mean(dist_to_loose)`.",
        "- Step6: map to looseness score `score = 100 * sigmoid(1.8 * gap)`.",
        "",
        "## Labeled Training Files",
        "```text",
        df_to_text(labeled_files_df[["file", "time_key", "minute", "truth_stage", "truth_binary"]]),
        "```",
        "",
        "## Selected Signature Features",
        "```text",
        df_to_text(model_df),
        "```",
        "",
        "## Validation (On Labeled Files)",
        f"- Accuracy: `{metrics['accuracy']:.4f}` on `{metrics['support']}` samples",
    ]
    conf = metrics.get("confusion")
    if isinstance(conf, pd.DataFrame) and not conf.empty:
        lines += [
            "```text",
            df_to_text(conf),
            "```",
        ]

    lines += [
        "",
        "## Scoring Output (All Matched Files)",
        "```text",
        df_to_text(
            result_df[
                [
                    "file",
                    "time_key",
                    "truth_stage",
                    "truth_binary",
                    "pred_binary",
                    "stage_hint",
                    "label",
                    "looseness_score",
                    "gap_normal_minus_loose",
                    "evidence_top",
                ]
            ]
        ),
        "```",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generic wire looseness scoring algorithm from labeled time ranges")
    parser.add_argument("--data-glob", default=DEFAULT_GLOB, help="input file glob")
    parser.add_argument("--hour", type=int, default=10, help="target hour in filename HHMMSS; -1 means all")
    parser.add_argument("--normal-minutes", default="16-19,50-57", help="normal minute ranges, e.g. 16-19,50-57")
    parser.add_argument("--loose1-minutes", default="36-41", help="loose_1 minute ranges, e.g. 36-41")
    parser.add_argument("--loose2-minutes", default="45-48", help="loose_2 minute ranges, e.g. 45-48")
    parser.add_argument("--topk", type=int, default=8, help="number of selected signature features")
    parser.add_argument("--score-thr", type=float, default=55.0, help="looseness decision threshold (0-100)")
    parser.add_argument("--model-out", type=Path, default=DEFAULT_MODEL_PATH, help="output model json path")
    parser.add_argument(
        "--report",
        type=Path,
        default=BASE_DIR / f"wire_looseness_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
        help="output report path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    normal_ranges = parse_ranges(args.normal_minutes)
    loose1_ranges = parse_ranges(args.loose1_minutes)
    loose2_ranges = parse_ranges(args.loose2_minutes)
    if not normal_ranges or not loose1_ranges or not loose2_ranges:
        raise ValueError("normal/loose1/loose2 ranges must not be empty")

    paths = sorted(Path(p) for p in [str(x) for x in Path().glob(args.data_glob)] if Path(p).is_file())
    if not paths:
        raise FileNotFoundError(f"no files matched: {args.data_glob}")

    rows: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    for p in paths:
        try:
            row = build_feature_row(p)
        except Exception as ex:
            skipped.append((p.name, str(ex)))
            continue
        label = pick_label(
            hour=int(row["hour"]),
            minute=int(row["minute"]),
            target_hour=args.hour,
            normal_ranges=normal_ranges,
            loose1_ranges=loose1_ranges,
            loose2_ranges=loose2_ranges,
        )
        row["truth_stage"] = label
        if label == "normal":
            row["truth_binary"] = "normal"
        elif label in {"loose_1", "loose_2"}:
            row["truth_binary"] = "loose"
        else:
            row["truth_binary"] = None
        rows.append(row)

    all_df = pd.DataFrame(rows).sort_values(["hour", "minute", "second", "file"]).reset_index(drop=True)
    train_df = all_df[all_df["truth_stage"].notna()].copy()
    train_df = train_df.rename(columns={"truth_stage": "label"})

    class_counts = train_df["label"].value_counts().to_dict()
    if class_counts.get("normal", 0) < 2 or class_counts.get("loose_1", 0) < 2 or class_counts.get("loose_2", 0) < 2:
        raise RuntimeError(f"insufficient labeled data: {class_counts}")

    model_df = select_features(train_df, topk=args.topk)
    loose1_df = train_df[train_df["label"] == "loose_1"]
    loose2_df = train_df[train_df["label"] == "loose_2"]
    for idx, row in model_df.iterrows():
        f = str(row["feature"])
        model_df.loc[idx, "mu_loose_1"] = float(loose1_df[f].mean())
        model_df.loc[idx, "mu_loose_2"] = float(loose2_df[f].mean())

    results: list[dict[str, Any]] = []
    for _, row in all_df.iterrows():
        pred = score_row(row.to_dict(), model_df, score_thr=float(args.score_thr))
        truth_stage = row.get("truth_stage")
        truth_binary = row.get("truth_binary")
        pred_binary = "loose" if float(pred["looseness_score"]) >= float(args.score_thr) else "normal"
        results.append(
            {
                "file": row["file"],
                "path": row["path"],
                "time_key": row["time_key"],
                "minute": row["minute"],
                "truth_stage": truth_stage,
                "truth_binary": truth_binary,
                "pred_binary": pred_binary,
                "stage_hint": pred["stage_hint"],
                "label": pred["label"],
                "looseness_score": pred["looseness_score"],
                "dist_normal": pred["dist_normal"],
                "dist_loose": pred["dist_loose"],
                "dist_loose_1": pred["dist_loose_1"],
                "dist_loose_2": pred["dist_loose_2"],
                "gap_normal_minus_loose": pred["gap_normal_minus_loose"],
                "evidence_top": pred["evidence_top"],
            }
        )

    result_df = pd.DataFrame(results).sort_values(["minute", "time_key", "file"]).reset_index(drop=True)
    metrics = simple_metrics(result_df)

    model_payload = {
        "model_type": "wire_looseness_signature_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "data_glob": args.data_glob,
        "hour": int(args.hour),
        "normal_ranges": args.normal_minutes,
        "loose1_ranges": args.loose1_minutes,
        "loose2_ranges": args.loose2_minutes,
        "score_threshold": float(args.score_thr),
        "signature_features": model_df.to_dict(orient="records"),
    }
    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    args.model_out.write_text(json.dumps(model_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    cfg = {
        "data_glob": args.data_glob,
        "normal_ranges": args.normal_minutes,
        "loose1_ranges": args.loose1_minutes,
        "loose2_ranges": args.loose2_minutes,
        "hour": args.hour,
        "score_thr": args.score_thr,
    }
    labeled_files_df = result_df[result_df["truth_binary"].isin(["normal", "loose"])].copy()
    report_text = build_report(
        cfg=cfg,
        labeled_files_df=labeled_files_df,
        model_df=model_df,
        metrics=metrics,
        result_df=result_df,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report_text, encoding="utf-8")

    print("=== Wire Looseness Generic Model Built ===")
    print(f"labeled counts: {class_counts}")
    print(f"accuracy_on_labeled={metrics['accuracy']:.4f} support={metrics['support']}")
    print(f"used_files={len(rows)} skipped_files={len(skipped)}")
    if skipped:
        print("skipped examples:")
        for name, reason in skipped[:5]:
            print(f"  - {name}: {reason}")
    print(f"model: {args.model_out.resolve()}")
    print(f"report: {args.report.resolve()}")


if __name__ == "__main__":
    main()
