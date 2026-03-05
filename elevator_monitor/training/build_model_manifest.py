from __future__ import annotations

import argparse
import json
from pathlib import Path

from .model_registry import build_manifest


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成模型版本清单(manifest)")
    parser.add_argument("--model-json", action="append", required=True, help="模型文件路径，可重复")
    parser.add_argument("--output", required=True, help="清单输出路径")
    parser.add_argument("--project", default="elevator-monitor", help="项目名")
    parser.add_argument("--environment", default="prod", help="环境标识")
    parser.add_argument("--created-by", default="unknown", help="发布人")
    parser.add_argument("--note", default="", help="发布说明")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    manifest = build_manifest(
        model_paths=args.model_json,
        project=args.project,
        environment=args.environment,
        created_by=args.created_by,
        note=args.note,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"manifest output: {out_path}")
    print(f"models: {len(manifest.get('models', []))}")
    for model in manifest.get("models", []):
        print(f"- {model['name']} task={model['task']} sha256={model['sha256'][:12]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
