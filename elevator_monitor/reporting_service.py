from __future__ import annotations

import json
import time
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _build_dify_prompt(language: str) -> str:
    lang = language.strip() or "zh-CN"
    return (
        f"你是电梯预测性维保报告助手，输出语言为 {lang}。\n"
        "请基于输入的 diagnostics_result_json 与 maintenance_package_json 生成结构化 Markdown 报告。\n"
        "报告必须包含：\n"
        "1) 执行摘要\n"
        "2) 诊断结论（Top fault + 证据）\n"
        "3) 风险与优先级解释\n"
        "4) 维保建议与备件建议\n"
        "5) 后续验证计划（如何复检）\n"
        "禁止编造输入中不存在的传感器类型、故障标签或现场信息。"
    )


def build_report_context(
    *,
    diagnosis_result: dict[str, Any],
    maintenance_package: dict[str, Any],
    language: str = "zh-CN",
    report_style: str = "standard",
) -> dict[str, Any]:
    diag = diagnosis_result if isinstance(diagnosis_result, dict) else {}
    package = maintenance_package if isinstance(maintenance_package, dict) else {}
    top_fault = dict(diag.get("top_fault", {})) if isinstance(diag.get("top_fault"), dict) else {}
    summary = dict(diag.get("summary", {})) if isinstance(diag.get("summary"), dict) else {}
    risk = dict(package.get("risk", {})) if isinstance(package.get("risk"), dict) else {}

    elevator_id = str(package.get("elevator_id", "")).strip() or "elevator-unknown"
    site_name = str(package.get("site_name", "")).strip() or "unknown-site"
    priority = str(package.get("priority", "P4")).strip() or "P4"
    top_fault_type = str(top_fault.get("fault_type", "unknown")).strip() or "unknown"
    top_fault_score = _safe_float(top_fault.get("score"), 0.0)
    risk_now = _safe_float(risk.get("risk_score"), 0.0)
    risk_24h = _safe_float(risk.get("risk_24h"), 0.0)
    dispatch_hours = _safe_int(package.get("dispatch_within_hours"), 72)

    report_title = f"{site_name} / {elevator_id} 诊断与维保报告"
    prompt = _build_dify_prompt(language)

    dify_report_inputs = {
        "report_title": report_title,
        "report_language": language,
        "report_style": report_style,
        "site_name": site_name,
        "elevator_id": elevator_id,
        "priority": priority,
        "dispatch_within_hours": dispatch_hours,
        "top_fault_type": top_fault_type,
        "top_fault_score": top_fault_score,
        "fault_level": str(top_fault.get("level", "normal")),
        "risk_score_now": risk_now,
        "risk_24h": risk_24h,
        "risk_level_now": str(risk.get("risk_level_now", "normal")),
        "risk_level_24h": str(risk.get("risk_level_24h", "normal")),
        "sample_count_raw": _safe_int(summary.get("n_raw"), 0),
        "sample_count_effective": _safe_int(summary.get("n_effective"), 0),
        "sample_rate_hz": _safe_float(summary.get("fs_hz"), 0.0),
        "maintenance_summary": str(package.get("summary", "")),
        "recommended_actions_text": " | ".join(package.get("recommended_actions", []) or []),
        "suggested_parts_text": ", ".join(package.get("suggested_parts", []) or []),
        "diagnostics_result_json": json.dumps(diag, ensure_ascii=False),
        "maintenance_package_json": json.dumps(package, ensure_ascii=False),
    }

    return {
        "report_context_version": 1,
        "generated_at_ms": int(time.time() * 1000),
        "report_title": report_title,
        "language": language,
        "report_style": report_style,
        "site_name": site_name,
        "elevator_id": elevator_id,
        "priority": priority,
        "top_fault": {
            "fault_type": top_fault_type,
            "score": top_fault_score,
            "level": str(top_fault.get("level", "normal")),
            "reasons": list(top_fault.get("reasons", [])) if isinstance(top_fault.get("reasons"), list) else [],
        },
        "risk": {
            "risk_score": risk_now,
            "risk_24h": risk_24h,
            "risk_level_now": str(risk.get("risk_level_now", "normal")),
            "risk_level_24h": str(risk.get("risk_level_24h", "normal")),
        },
        "diagnosis_result": diag,
        "maintenance_package": package,
        "dify_prompt_template": prompt,
        "dify_report_inputs": dify_report_inputs,
    }


def render_report_markdown(report_context: dict[str, Any]) -> str:
    top_fault = report_context.get("top_fault", {})
    risk = report_context.get("risk", {})
    package = report_context.get("maintenance_package", {})
    actions = package.get("recommended_actions", []) or []
    parts = package.get("suggested_parts", []) or []

    lines = [
        f"# {report_context.get('report_title', 'Elevator Diagnostic Report')}",
        "",
        f"- Site: {report_context.get('site_name', '')}",
        f"- Elevator: {report_context.get('elevator_id', '')}",
        f"- Priority: {report_context.get('priority', '')}",
        "",
        "## Executive Summary",
        str(package.get("summary", "")),
        "",
        "## Diagnosis",
        f"- Top fault: {top_fault.get('fault_type', 'unknown')}",
        f"- Fault score: {_safe_float(top_fault.get('score'), 0.0):.2f}",
        f"- Fault level: {top_fault.get('level', 'normal')}",
        "",
        "## Risk",
        f"- Risk now: {_safe_float(risk.get('risk_score'), 0.0):.2f} ({risk.get('risk_level_now', 'normal')})",
        f"- Risk 24h: {_safe_float(risk.get('risk_24h'), 0.0):.2f} ({risk.get('risk_level_24h', 'normal')})",
        "",
        "## Recommended Actions",
    ]

    if actions:
        for item in actions:
            lines.append(f"- {item}")
    else:
        lines.append("- Continue monitoring and collect more labeled windows.")

    lines.extend(["", "## Suggested Parts"])
    if parts:
        for item in parts:
            lines.append(f"- {item}")
    else:
        lines.append("- No mandatory replacement parts suggested.")

    return "\n".join(lines).strip() + "\n"
