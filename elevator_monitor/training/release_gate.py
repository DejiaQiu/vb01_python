from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class GateConfig:
    expected_task: str = ""
    min_accuracy: Optional[float] = None
    min_macro_f1: Optional[float] = None
    min_weighted_f1: Optional[float] = None
    min_support: Optional[int] = None
    positive_label: str = ""
    min_positive_precision: Optional[float] = None
    min_positive_recall: Optional[float] = None
    min_positive_f1: Optional[float] = None


def _check_min(name: str, actual: Optional[float], required: Optional[float], failures: list[str]) -> None:
    if required is None:
        return
    value = float(actual or 0.0)
    if value < required:
        failures.append(f"{name}={value:.4f} < required={required:.4f}")


def evaluate_gate(model_payload: dict[str, Any], cfg: GateConfig) -> dict[str, Any]:
    failures: list[str] = []
    task = str(model_payload.get("task", ""))
    metrics = dict(model_payload.get("metrics", {}))

    if cfg.expected_task and task != cfg.expected_task:
        failures.append(f"task mismatch actual={task} expected={cfg.expected_task}")

    _check_min("accuracy", metrics.get("accuracy"), cfg.min_accuracy, failures)
    _check_min("macro_f1", metrics.get("macro_f1"), cfg.min_macro_f1, failures)
    _check_min("weighted_f1", metrics.get("weighted_f1"), cfg.min_weighted_f1, failures)

    support = int(metrics.get("support", 0) or 0)
    if cfg.min_support is not None and support < cfg.min_support:
        failures.append(f"support={support} < required={cfg.min_support}")

    if cfg.positive_label:
        per_class = metrics.get("per_class", {})
        pos_metrics = per_class.get(cfg.positive_label)
        if not isinstance(pos_metrics, dict):
            failures.append(f"positive label missing in per_class: {cfg.positive_label}")
        else:
            _check_min("positive_precision", pos_metrics.get("precision"), cfg.min_positive_precision, failures)
            _check_min("positive_recall", pos_metrics.get("recall"), cfg.min_positive_recall, failures)
            _check_min("positive_f1", pos_metrics.get("f1"), cfg.min_positive_f1, failures)

    return {
        "pass": len(failures) == 0,
        "task": task,
        "metrics": metrics,
        "failures": failures,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模型上线门槛检查")
    parser.add_argument("--model-json", required=True, help="模型文件")
    parser.add_argument("--expected-task", default="", help="期望task（fault_type/risk_24h等）")
    parser.add_argument("--min-accuracy", type=float, default=None, help="最小accuracy")
    parser.add_argument("--min-macro-f1", type=float, default=None, help="最小macro_f1")
    parser.add_argument("--min-weighted-f1", type=float, default=None, help="最小weighted_f1")
    parser.add_argument("--min-support", type=int, default=None, help="最小评估样本数")
    parser.add_argument("--positive-label", default="", help="正类标签名")
    parser.add_argument("--min-positive-precision", type=float, default=None, help="正类最小precision")
    parser.add_argument("--min-positive-recall", type=float, default=None, help="正类最小recall")
    parser.add_argument("--min-positive-f1", type=float, default=None, help="正类最小f1")
    parser.add_argument("--output-json", default="", help="输出评估结果JSON（可选）")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    payload = json.loads(Path(args.model_json).read_text(encoding="utf-8"))
    cfg = GateConfig(
        expected_task=args.expected_task,
        min_accuracy=args.min_accuracy,
        min_macro_f1=args.min_macro_f1,
        min_weighted_f1=args.min_weighted_f1,
        min_support=args.min_support,
        positive_label=args.positive_label,
        min_positive_precision=args.min_positive_precision,
        min_positive_recall=args.min_positive_recall,
        min_positive_f1=args.min_positive_f1,
    )
    result = evaluate_gate(payload, cfg)

    print(f"gate pass={result['pass']} task={result['task']}")
    if result["failures"]:
        print("failures:")
        for item in result["failures"]:
            print(f"- {item}")
    else:
        print("all checks passed")

    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"gate output: {out_path}")

    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
