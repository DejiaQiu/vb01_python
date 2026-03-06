from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path


REQUIRED_METADATA_FIELDS = ("name", "status", "module", "priority")
REQUIRED_SECTIONS = (
    "目标",
    "范围",
    "实现位置",
    "接口与输入输出",
    "业务规则",
    "验收标准",
    "测试用例",
)
PLACEHOLDER_MARKERS = ("待填写", "TODO", "[待填写", "<待填写", "TBD")
SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class FeatureRequirement:
    path: Path
    metadata: dict[str, str]
    sections: dict[str, str]
    raw_text: str

    def section(self, name: str) -> str:
        return self.sections.get(name, "").strip()


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text

    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text

    frontmatter_block = text[4:end]
    body = text[end + 5 :]
    metadata: dict[str, str] = {}
    for raw_line in frontmatter_block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata, body


def _parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(SECTION_PATTERN.finditer(text))
    for index, match in enumerate(matches):
        section_name = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[section_name] = text[start:end].strip()
    return sections


def load_feature_requirement(path: str | Path) -> FeatureRequirement:
    requirement_path = Path(path).expanduser().resolve()
    text = requirement_path.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(text)
    return FeatureRequirement(
        path=requirement_path,
        metadata=metadata,
        sections=_parse_sections(body),
        raw_text=text,
    )


def _has_placeholder(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    return any(marker in stripped for marker in PLACEHOLDER_MARKERS)


def _extract_bullets(text: str) -> list[str]:
    bullets: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(("- ", "* ")):
            bullets.append(line[2:].strip())
    return bullets


def validate_feature_requirement(requirement: FeatureRequirement) -> list[str]:
    errors: list[str] = []

    for key in REQUIRED_METADATA_FIELDS:
        value = requirement.metadata.get(key, "")
        if _has_placeholder(value):
            errors.append(f"metadata.{key} 未填写")

    for section_name in REQUIRED_SECTIONS:
        value = requirement.section(section_name)
        if _has_placeholder(value):
            errors.append(f"section.{section_name} 未填写")

    acceptance_items = [item for item in _extract_bullets(requirement.section("验收标准")) if not _has_placeholder(item)]
    if len(acceptance_items) < 3:
        errors.append("section.验收标准 至少需要 3 条可验证标准")

    test_items = [item for item in _extract_bullets(requirement.section("测试用例")) if not _has_placeholder(item)]
    if len(test_items) < 3:
        errors.append("section.测试用例 至少需要 3 条测试点")

    return errors


def summarize_feature_requirement(requirement: FeatureRequirement) -> dict[str, object]:
    return {
        "path": str(requirement.path),
        "name": requirement.metadata.get("name", ""),
        "status": requirement.metadata.get("status", ""),
        "module": requirement.metadata.get("module", ""),
        "priority": requirement.metadata.get("priority", ""),
        "implementation_targets": _extract_bullets(requirement.section("实现位置")),
        "acceptance_items": _extract_bullets(requirement.section("验收标准")),
        "test_items": _extract_bullets(requirement.section("测试用例")),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and summarize a feature requirement markdown file")
    parser.add_argument("path", nargs="?", default="requirements/feature_request.md", help="path to requirement markdown")
    parser.add_argument("--json", action="store_true", help="print summary as json")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    requirement = load_feature_requirement(args.path)
    errors = validate_feature_requirement(requirement)
    summary = summarize_feature_requirement(requirement)

    if args.json:
        print(json.dumps({"summary": summary, "errors": errors}, ensure_ascii=False, indent=2))
    else:
        print(f"Requirement: {summary['name'] or requirement.path.name}")
        print(f"Path: {summary['path']}")
        print(f"Module: {summary['module'] or '-'}")
        print(f"Priority: {summary['priority'] or '-'}")
        print(f"Status: {summary['status'] or '-'}")
        if errors:
            print("Validation: failed")
            for error in errors:
                print(f"- {error}")
        else:
            print("Validation: ok")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
