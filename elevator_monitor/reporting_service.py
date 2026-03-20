from __future__ import annotations

import csv
import gzip
import io
import json
import time
from pathlib import Path
from typing import Any

from .maintenance_workflow import build_maintenance_package
from .waveform_service import build_waveform_payload, load_waveform_rows


_FAULT_LABELS = {
    "bearing_wear": "轴承磨损",
    "brake_jitter": "制动抖动",
    "car_imbalance": "轿厢受力不均",
    "coupling_misalignment": "联轴器不对中",
    "door_stuck": "门系统卡阻",
    "guide_rail_wear": "导轨磨损",
    "impact_shock": "冲击或急停类异常",
    "mechanical_looseness": "机械连接松动",
    "rail_wear": "导轨磨损",
    "rope_looseness": "钢丝绳松动或张力不均",
    "rope_tension_abnormal": "钢丝绳状态异常",
    "rubber_hardening": "减振橡胶硬化",
    "unknown": "暂无明确故障类型",
}

_FAULT_EXPLANATIONS = {
    "bearing_wear": "系统更像是在提示旋转部件磨损，需要重点检查轴承温升、噪声和润滑状态。",
    "brake_jitter": "系统看到与制动动作不平顺相似的振动表现，建议检查制动释放和抱闸过程。",
    "car_imbalance": "系统看到轿厢左右或前后受力不够均匀的迹象，常见于偏载或导向状态变化。",
    "coupling_misalignment": "系统看到联轴器或传动连接不同心的迹象，建议检查对中和紧固情况。",
    "door_stuck": "系统更像是在提示门机或门锁一侧存在阻力异常，需要检查门系统机械阻滞。",
    "guide_rail_wear": "系统看到与导轨或导靴磨损相似的振动模式，建议结合现场磨痕一起判断。",
    "impact_shock": "系统看到更像冲击或急停的瞬态异常，不一定是持续性机械故障。",
    "mechanical_looseness": "系统看到连接件松动类振动特征，建议检查紧固件、底座和连接部位。",
    "rail_wear": "系统看到与导轨或导靴磨损相似的振动模式，建议结合现场磨痕一起判断。",
    "rope_looseness": "系统看到与钢丝绳张力不均或松动相似的振动模式，建议优先检查钢丝绳受力是否均衡。",
    "rope_tension_abnormal": "系统看到相对健康基线的钢丝绳相关异常变化，建议优先检查钢丝绳张力、张力均衡和曳引轮接触状态。",
    "rubber_hardening": "系统看到减振橡胶变硬后常见的竖向响应和耦合变化，建议检查减振橡胶老化情况。",
    "unknown": "当前没有足够证据把问题稳定归到某一类具体故障。",
}

_SCREENING_LABELS = {
    "candidate_faults": "高置信候选",
    "watch_only": "重点关注",
    "normal": "未见明确异常",
    "low_quality": "数据不足",
}

_SCREENING_EXPLANATIONS = {
    "candidate_faults": "系统识别到了与某类故障比较吻合的振动模式，建议尽快安排现场复核。",
    "watch_only": "系统看到了可疑迹象，但证据还不够强，更适合复测或持续观察，不建议直接下故障定论。",
    "normal": "系统没有看到持续、明确的异常模式，目前不支持立即拆修或停梯处理。",
    "low_quality": "这次数据点数偏少或质量不足，系统不能可靠判断，建议重新采集更完整的数据。",
}

_PRIORITY_LABELS = {
    "P1": "立即处理",
    "P2": "尽快安排检查",
    "P3": "建议 24 小时内复检",
    "P4": "继续观察",
}

_RISK_LABELS = {
    "critical": "高风险",
    "high": "较高风险",
    "watch": "需要关注",
    "normal": "风险较低",
}

_BASELINE_LABELS = {
    "disabled": "本次没有健康基线，只能按保守规则做筛查，结果更偏向“宁可漏报也少误报”。",
    "json": "本次使用了已保存的健康基线做对比，结论更偏向“和这台电梯平时状态相比有没有明显变化”。",
    "dir": "本次使用了健康样本目录自动生成的基线做对比，结论更偏向“和这台电梯平时状态相比有没有明显变化”。",
}

_ACTION_TRANSLATIONS = {
    "Check rope tension balance across all ropes.": "检查各根钢丝绳张力是否均衡。",
    "Inspect traction sheave groove wear and rope slip marks.": "检查曳引轮绳槽磨损和钢丝绳打滑痕迹。",
    "Rebalance tension before returning to full load.": "在恢复满载运行前，先把钢丝绳张力重新调整平衡。",
    "Inspect anchor bolts and frame fasteners.": "检查地脚螺栓和机架紧固件是否松动。",
    "Recheck coupling tightness and vibration isolation mounts.": "复查联轴器紧固情况和减振支撑是否异常。",
    "Retest after torque recovery.": "恢复紧固后重新采集一次数据做复测。",
    "Inspect guide rail and shoe wear marks.": "检查导轨和导靴的磨损痕迹。",
    "Verify alignment and lubrication state.": "检查导向对中情况和润滑状态。",
    "Compare rail wear trend against previous inspection records.": "把本次磨损迹象和历史巡检记录做对比。",
    "Inspect brake engagement and release timing.": "检查制动器抱闸和松闸时序是否正常。",
    "Check motor base and coupling for intermittent impact.": "检查曳引机底座和联轴器是否存在间歇性冲击。",
    "Review recent starts/stops around the alert window.": "复核告警时间段前后的启动、制动工况。",
    "Inspect traction motor bearing temperature and noise.": "检查曳引机轴承温升和异响情况。",
    "Check lubrication status and shaft radial play.": "检查润滑状态和轴系径向间隙。",
    "Confirm whether bearing resonance matches the recent trend.": "核对轴承共振迹象是否与最近趋势一致。",
    "Inspect the mechanical path around the motor and traction system.": "检查曳引机及曳引系统周边机械传动链路。",
    "Compare the alert context waveform against the site baseline.": "把当前异常波形和本站点历史正常波形做对比。",
    "Confirm whether the anomaly repeats under both up and down runs.": "确认异常在上行和下行中是否都会重复出现。",
    "Prioritize remote review before dispatching a full repair team.": "优先做远程复核，再决定是否安排完整维保班组到场。",
}

_PART_TRANSLATIONS = {
    "brake pad": "制动衬片",
    "coupling insert": "联轴器弹性体",
    "door belt": "门机皮带",
    "door lock switch": "门锁开关",
    "door roller": "门滑轮",
    "fastener kit": "紧固件套件",
    "guide shoe": "导靴",
    "lubricant": "润滑剂",
    "motor bearing": "电机轴承",
    "rope clamp": "钢丝绳夹具",
    "rope tension gauge": "钢丝绳张力计",
    "vibration pad": "减振垫",
}


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
        "请基于输入的 diagnostics_result_json 与 maintenance_package_json 生成面向非专业读者的结构化 Markdown 报告。\n"
        "报告必须包含：\n"
        "1) 一句话结论\n"
        "2) 给非专业人员的解释\n"
        "3) 建议怎么做\n"
        "4) 本次判断依据\n"
        "5) 给维保人员的补充参考\n"
        "请优先使用日常中文表达，避免堆砌英文缩写和生硬术语。\n"
        "禁止编造输入中不存在的传感器类型、故障标签或现场信息。"
    )


def _fault_label(fault_type: str) -> str:
    key = fault_type.strip().lower()
    return _FAULT_LABELS.get(key, key or _FAULT_LABELS["unknown"])


def _fault_explanation(fault_type: str) -> str:
    key = fault_type.strip().lower()
    return _FAULT_EXPLANATIONS.get(key, _FAULT_EXPLANATIONS["unknown"])


def _screening_label(status: str) -> str:
    key = status.strip().lower()
    return _SCREENING_LABELS.get(key, key or "未知状态")


def _screening_explanation(status: str) -> str:
    key = status.strip().lower()
    return _SCREENING_EXPLANATIONS.get(key, "当前状态缺少解释信息，建议结合现场复核。")


def _priority_label(priority: str) -> str:
    key = priority.strip().upper()
    return _PRIORITY_LABELS.get(key, key or "未分级")


def _risk_label(level: str) -> str:
    key = level.strip().lower()
    return _RISK_LABELS.get(key, key or "未分级")


def _baseline_text(mode: str) -> str:
    key = mode.strip().lower()
    return _BASELINE_LABELS.get(key, _BASELINE_LABELS["disabled"])


def _translate_action(action: Any) -> str:
    text = str(action or "").strip()
    if not text:
        return ""
    return _ACTION_TRANSLATIONS.get(text, text)


def _translate_part(part: Any) -> str:
    text = str(part or "").strip()
    if not text:
        return ""
    return _PART_TRANSLATIONS.get(text, text)


def _confidence_text(score: float, status: str) -> str:
    screening_status = str(status or "").strip().lower()
    if screening_status == "watch_only":
        return "较低（待复测确认）"
    if screening_status == "normal":
        return "无明确异常"
    if screening_status == "low_quality":
        return "数据不足"
    if score >= 80.0:
        return "很高"
    if score >= 60.0:
        return "较高"
    if score >= 45.0:
        return "中等"
    return "较低"


def _quality_text(quality: float) -> str:
    if quality >= 0.8:
        return "数据质量较好"
    if quality >= 0.6:
        return "数据基本可用"
    return "数据质量一般，仅适合保守判断"


def _preferred_issue(diag: dict[str, Any]) -> dict[str, Any]:
    primary_issue = diag.get("primary_issue", {})
    if isinstance(primary_issue, dict) and primary_issue:
        return primary_issue
    screening = diag.get("screening", {})
    status = str(screening.get("status", "")).strip().lower()
    if status == "candidate_faults":
        candidate = diag.get("top_candidate", {})
        if isinstance(candidate, dict) and candidate:
            return candidate
    if status == "watch_only":
        watch_faults = diag.get("watch_faults", [])
        if isinstance(watch_faults, list) and watch_faults:
            first = watch_faults[0]
            if isinstance(first, dict):
                return first
    return {}


def _headline_text(status: str, issue: dict[str, Any], dispatch_hours: int) -> str:
    issue_label = _fault_label(str(issue.get("fault_type", "")))
    issue_fault_type = str(issue.get("fault_type", "")).strip().lower()
    score = _safe_float(issue.get("score"), 0.0)
    if status == "candidate_faults":
        if issue_fault_type in {"rope_looseness", "rope_tension_abnormal"}:
            return f"本次检测发现“{issue_label}”高置信，说明钢丝绳相关异常变化证据已经比较充分，建议在 {max(1, dispatch_hours)} 小时内安排现场检查。"
        return f"本次检测发现“{issue_label}”高置信候选，建议在 {max(1, dispatch_hours)} 小时内安排现场检查。"
    if status == "watch_only":
        if issue_fault_type == "unknown":
            return "本次检测看到相对健康基线的异常变化，但当前证据还不足以稳定归到钢丝绳或减振橡胶问题，建议尽快复测并结合现场检查确认。"
        if issue_fault_type in {"rope_looseness", "rope_tension_abnormal"}:
            return f"本次检测发现“{issue_label}”变化线索，说明相对健康基线已经有偏移，但钢丝绳专属性还不够强，建议尽快复测或结合现场检查确认。"
        return f"本次检测发现“{issue_label}”可疑迹象，但证据还不够强，建议尽快复测或结合现场检查确认。"
    if status == "low_quality":
        return "本次数据质量不足，暂时不能给出可靠结论，建议重新采集数据后再判断。"
    if score >= 0.0:
        return "本次检测未发现明确异常，当前更适合继续观察和按计划维保。"
    return "本次检测暂未形成明确判断。"


def _explanation_text(status: str, issue: dict[str, Any]) -> str:
    issue_label = _fault_label(str(issue.get("fault_type", "")))
    issue_fault_type = str(issue.get("fault_type", "")).strip().lower()
    if status == "candidate_faults":
        if issue_fault_type in {"rope_looseness", "rope_tension_abnormal"}:
            return f"{_screening_explanation(status)} 当前最像的问题是“{issue_label}”。这表示系统看到的相对健康基线偏移和钢丝绳相关特征同时成立，但仍建议以现场复核结果为准。"
        return f"{_screening_explanation(status)} 当前最像的问题是“{issue_label}”。这表示系统看到的振动模式与该类故障较为接近，但仍建议以现场复核结果为准。"
    if status == "watch_only":
        if issue_fault_type == "unknown":
            return "系统已经看到相对健康基线的异常偏移，但钢丝绳和减振橡胶两条专项规则都还没有形成足够稳定的类型证据，当前更适合继续观察、补采数据或安排现场复核。"
        if issue_fault_type in {"rope_looseness", "rope_tension_abnormal"}:
            return f"{_screening_explanation(status)} 当前最值得关注的是“{issue_label}”，它更像一个相对健康基线的钢丝绳异常提醒信号，而不是已经确认的故障结论。"
        return f"{_screening_explanation(status)} 当前最值得关注的是“{issue_label}”，它更像一个提醒信号，而不是已经确认的故障结论。"
    if status == "low_quality":
        return _screening_explanation(status)
    return _screening_explanation(status)


def _default_actions(status: str, issue_fault_type: str, dispatch_hours: int) -> list[str]:
    issue_label = _fault_label(issue_fault_type)
    if status == "low_quality":
        return [
            "重新采集一段更完整的运行数据，尽量覆盖稳定上行或下行过程。",
            "确认 CSV 中有效数据点足够，避免只有零散几条记录。",
            "复测后再做综合判断，避免依据低质量数据直接安排拆检。",
        ]
    if status == "normal":
        return [
            "保持当前维保计划，不必因为本次结果单独追加拆检。",
            "如果现场已经出现异响、抖动或乘坐不适，再补采一次数据做复核。",
            "建议持续积累健康样本，后续对比会更稳定。",
        ]
    if issue_fault_type in {"rope_looseness", "rope_tension_abnormal"}:
        return [
            f"建议在 {max(1, dispatch_hours)} 小时内检查各根钢丝绳的张力状态和张力均衡。",
            "检查曳引轮绳槽磨损、打滑痕迹和钢丝绳外观状态。",
            "处理后再复测一次，确认钢丝绳异常信号是否回落。",
        ]
    if issue_fault_type == "rubber_hardening":
        return [
            f"建议在 {max(1, dispatch_hours)} 小时内检查曳引机减振橡胶是否老化、变硬或开裂。",
            "检查曳引机底座支撑和紧固状态，确认是否存在刚性传递增强。",
            "处理后再采集一段同工况数据对比。",
        ]
    return [
        f"建议在 {max(1, dispatch_hours)} 小时内安排现场检查，重点复核“{issue_label}”相关部位。",
        "结合现场异响、抖动和历史维保记录一起判断，不建议只凭一次筛查结果直接定性。",
        "处理或复核后再采集一次同类数据，确认问题是否持续存在。",
    ]


def _render_action_lines(actions: list[Any], status: str, issue_fault_type: str, dispatch_hours: int) -> list[str]:
    translated = [_translate_action(item) for item in actions]
    translated = [item for item in translated if item]
    if not translated:
        translated = _default_actions(status, issue_fault_type, dispatch_hours)
    return [f"- {item}" for item in translated]


def _render_parts_lines(parts: list[Any]) -> list[str]:
    translated = [_translate_part(item) for item in parts]
    translated = [item for item in translated if item]
    if not translated:
        return ["- 本次报告没有给出必须更换的固定备件，建议以现场复核结果为准。"]
    return [f"- {item}" for item in translated]


def _md_cell(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    return text.replace("\n", "<br>").replace("|", "/")


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    if not headers or not rows:
        return []
    table = [
        "| " + " | ".join(_md_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        padded = list(row[: len(headers)]) + [""] * max(0, len(headers) - len(row))
        table.append("| " + " | ".join(_md_cell(item) for item in padded[: len(headers)]) + " |")
    return table


def build_report_context(
    *,
    diagnosis_result: dict[str, Any],
    maintenance_package: dict[str, Any],
    language: str = "zh-CN",
    report_style: str = "standard",
    waveform_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    diag = diagnosis_result if isinstance(diagnosis_result, dict) else {}
    package = maintenance_package if isinstance(maintenance_package, dict) else {}
    waveforms = waveform_payload if isinstance(waveform_payload, dict) else {}
    top_fault = dict(diag.get("top_fault", {})) if isinstance(diag.get("top_fault"), dict) else {}
    rope_primary = dict(diag.get("rope_primary", {})) if isinstance(diag.get("rope_primary"), dict) else {}
    rubber_primary = dict(diag.get("rubber_primary", {})) if isinstance(diag.get("rubber_primary"), dict) else {}
    system_abnormality = dict(diag.get("system_abnormality", {})) if isinstance(diag.get("system_abnormality"), dict) else {}
    summary = dict(diag.get("summary", {})) if isinstance(diag.get("summary"), dict) else {}
    screening = dict(diag.get("screening", {})) if isinstance(diag.get("screening"), dict) else {}
    risk = dict(package.get("risk", {})) if isinstance(package.get("risk"), dict) else {}
    baseline = dict(diag.get("baseline", {})) if isinstance(diag.get("baseline"), dict) else {}
    preferred_issue = _preferred_issue(diag)

    elevator_id = str(package.get("elevator_id", "")).strip() or "elevator-unknown"
    site_name = str(package.get("site_name", "")).strip() or "unknown-site"
    priority = str(package.get("priority", "P4")).strip() or "P4"
    top_fault_type = str(top_fault.get("fault_type", "unknown")).strip() or "unknown"
    top_fault_score = _safe_float(top_fault.get("score"), 0.0)
    screening_status = str(screening.get("status", "normal")).strip() or "normal"
    risk_now = _safe_float(risk.get("risk_score"), 0.0)
    risk_24h = _safe_float(risk.get("risk_24h"), 0.0)
    dispatch_hours = _safe_int(package.get("dispatch_within_hours"), 72)
    preferred_fault_type = str(preferred_issue.get("fault_type", "")).strip() or "unknown"
    preferred_fault_score = _safe_float(preferred_issue.get("score"), 0.0)

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
        "screening_status": screening_status,
        "screening_label": _screening_label(screening_status),
        "top_fault_type": top_fault_type,
        "top_fault_score": top_fault_score,
        "fault_level": str(top_fault.get("level", "normal")),
        "preferred_fault_type": preferred_fault_type,
        "preferred_fault_label": _fault_label(preferred_fault_type),
        "preferred_fault_score": preferred_fault_score,
        "rope_branch": str(rope_primary.get("rope_branch", "")),
        "rope_rule_score": _safe_float(rope_primary.get("rope_rule_score"), 0.0),
        "system_abnormality_status": str(system_abnormality.get("status", "normal")),
        "system_abnormality_score": _safe_float(system_abnormality.get("score"), 0.0),
        "risk_score_now": risk_now,
        "risk_24h": risk_24h,
        "risk_level_now": str(risk.get("risk_level_now", "normal")),
        "risk_level_24h": str(risk.get("risk_level_24h", "normal")),
        "sample_count_raw": _safe_int(summary.get("n_raw"), 0),
        "sample_count_effective": _safe_int(summary.get("n_effective"), 0),
        "sample_rate_hz": _safe_float(summary.get("fs_hz"), 0.0),
        "baseline_mode": str(baseline.get("mode", "disabled")),
        "waveform_enabled": bool(waveforms),
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
        "screening": {
            "status": screening_status,
            "label": _screening_label(screening_status),
        },
        "baseline": baseline,
        "top_fault": {
            "fault_type": top_fault_type,
            "score": top_fault_score,
            "level": str(top_fault.get("level", "normal")),
            "reasons": list(top_fault.get("reasons", [])) if isinstance(top_fault.get("reasons"), list) else [],
        },
        "rope_primary": rope_primary,
        "rubber_primary": rubber_primary,
        "system_abnormality": system_abnormality,
        "preferred_issue": {
            "fault_type": preferred_fault_type,
            "fault_label": _fault_label(preferred_fault_type),
            "score": preferred_fault_score,
            "level": str(preferred_issue.get("level", "normal")),
            "quality_factor": _safe_float(preferred_issue.get("quality_factor"), 0.0),
        },
        "risk": {
            "risk_score": risk_now,
            "risk_24h": risk_24h,
            "risk_level_now": str(risk.get("risk_level_now", "normal")),
            "risk_level_24h": str(risk.get("risk_level_24h", "normal")),
        },
        "diagnosis_result": diag,
        "maintenance_package": package,
        "waveform_payload": waveforms,
        "dify_prompt_template": prompt,
        "dify_report_inputs": dify_report_inputs,
    }


def _screening_status_from_event(event: dict[str, Any]) -> str:
    level = str(event.get("level", "normal")).strip().lower()
    fault_type = str(event.get("fault_type", "unknown")).strip().lower()
    if level == "anomaly":
        return "candidate_faults"
    if level == "warning" and fault_type not in {"", "unknown", "normal"}:
        return "watch_only"
    return "normal"


def _event_issue_payload(event: dict[str, Any], screening_status: str) -> dict[str, Any]:
    confidence = _safe_float(event.get("fault_confidence"), 0.0)
    score = confidence * 100.0 if confidence <= 1.0 else confidence
    return {
        "fault_type": str(event.get("fault_type", "unknown")),
        "score": round(score, 2),
        "level": str(event.get("level", "normal")),
        "triggered": screening_status in {"candidate_faults", "watch_only"},
        "quality_factor": 1.0,
        "reasons": [],
    }


def _load_waveform_payload_from_event_context(event: dict[str, Any]) -> dict[str, Any]:
    context = event.get("context", {}) if isinstance(event.get("context"), dict) else {}
    stored_path = str(context.get("stored_path", "")).strip() or str(context.get("local_path", "")).strip()
    if not stored_path:
        return {}
    path = Path(stored_path)
    if not path.exists():
        return {}
    try:
        if str(context.get("compression", "")).strip().lower() == "gzip":
            text = gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace")
            rows = [dict(row) for row in csv.DictReader(io.StringIO(text))]
            if rows:
                return build_waveform_payload(rows, source=stored_path)
            return {}
        rows, source = load_waveform_rows([], "", stored_path)
        if rows:
            return build_waveform_payload(rows, source=source)
    except Exception:
        return {}
    return {}


def build_report_context_from_edge_event(
    *,
    alert_event: dict[str, Any],
    language: str = "zh-CN",
    report_style: str = "standard",
    include_waveforms: bool = True,
) -> dict[str, Any]:
    event = dict(alert_event or {})
    screening_status = _screening_status_from_event(event)
    issue = _event_issue_payload(event, screening_status)
    diagnosis_result = {
        "input": "edge_event",
        "screening": {"status": screening_status},
        "summary": {"n_raw": 0, "n_effective": 0, "fs_hz": 0.0},
        "baseline": {"mode": "disabled"},
        "top_fault": dict(issue),
        "top_candidate": dict(issue) if screening_status == "candidate_faults" else {},
        "watch_faults": [dict(issue)] if screening_status == "watch_only" else [],
        "preferred_issue": dict(issue),
    }

    alert_row = {
        "elevator_id": str(event.get("elevator_id", "")),
        "ts_ms": str(event.get("ts_ms", "")),
        "level": str(event.get("level", "normal")),
        "predictive_only": str(event.get("predictive_only", 0)),
        "fault_type": str(event.get("fault_type", "unknown")),
        "fault_confidence": str(event.get("fault_confidence", 0.0)),
        "risk_score": str(event.get("risk_score", 0.0)),
        "risk_level_now": str(event.get("risk_level_now", "normal")),
        "risk_24h": str(event.get("risk_24h", 0.0)),
        "risk_level_24h": str(event.get("risk_level_24h", "normal")),
        "alert_context_csv": str(event.get("alert_context_csv", "")),
    }
    maintenance_package = build_maintenance_package(
        alert_rows=[alert_row],
        health_payload=dict(event.get("health_payload") or {}),
        site_name=str(event.get("site_name", "")),
        alert_csv_path="",
        health_json_path="",
        manifest_payload={},
        manifest_path="",
    )
    waveform_payload = _load_waveform_payload_from_event_context(event) if include_waveforms else {}
    report_ctx = build_report_context(
        diagnosis_result=diagnosis_result,
        maintenance_package=maintenance_package,
        language=language,
        report_style=report_style,
        waveform_payload=waveform_payload,
    )
    report_ctx["event_id"] = str(event.get("event_id", ""))
    return report_ctx


def render_report_markdown(report_context: dict[str, Any]) -> str:
    top_fault = report_context.get("top_fault", {})
    preferred_issue = report_context.get("preferred_issue", {})
    screening = report_context.get("screening", {})
    baseline = report_context.get("baseline", {})
    diagnosis = report_context.get("diagnosis_result", {})
    summary = diagnosis.get("summary", {}) if isinstance(diagnosis, dict) else {}
    risk = report_context.get("risk", {})
    package = report_context.get("maintenance_package", {})
    waveforms = report_context.get("waveform_payload", {})
    actions = package.get("recommended_actions", []) or []
    parts = package.get("suggested_parts", []) or []
    screening_status = str(screening.get("status", "normal")).strip().lower()
    issue_fault_type = str(preferred_issue.get("fault_type", "")).strip() or "unknown"
    issue_label = _fault_label(issue_fault_type)
    issue_score = _safe_float(preferred_issue.get("score"), 0.0)
    quality_factor = _safe_float(preferred_issue.get("quality_factor"), 0.0)
    dispatch_hours = _safe_int(package.get("dispatch_within_hours"), 72)
    baseline_mode = str(baseline.get("mode", "disabled"))
    n_raw = _safe_int(summary.get("n_raw"), 0)
    n_effective = _safe_int(summary.get("n_effective"), 0)
    fs_hz = _safe_float(summary.get("fs_hz"), 0.0)

    lines = [
        f"# {report_context.get('report_title', '电梯故障诊断报告')}",
        "",
        "## 1. 一句话结论",
        _headline_text(screening_status, preferred_issue, dispatch_hours),
        "",
        "## 2. 给非专业人员的解释",
        _explanation_text(screening_status, preferred_issue),
        "",
        "## 3. 建议怎么做",
    ]

    lines.extend(_render_action_lines(actions, screening_status, issue_fault_type, dispatch_hours))

    basis_rows = [
        ["筛查状态", _screening_label(screening_status)],
        ["当前最值得关注的问题", issue_label],
        ["参考匹配分数", f"{issue_score:.1f}/100（当前可信度：{_confidence_text(issue_score, screening_status)}）"],
        ["数据情况", f"{n_effective} 个有效点 / {n_raw} 个原始点，采样频率约 {fs_hz:.2f} Hz，{_quality_text(quality_factor)}"],
        ["风险判断", f"当前 {_risk_label(str(risk.get('risk_level_now', 'normal')))}，24 小时内 {_risk_label(str(risk.get('risk_level_24h', 'normal')))}"],
        ["处理优先级", _priority_label(str(report_context.get("priority", "P4")))],
        ["基线说明", _baseline_text(baseline_mode)],
        ["故障解释", _fault_explanation(issue_fault_type)],
        ["说明", "本报告属于振动筛查结果，不等同于拆检后的最终结论，建议结合现场复核判断。"],
    ]
    maintenance_rows = [
        ["系统故障标签", issue_fault_type or "unknown"],
        ["原始最高分故障", f"{_fault_label(str(top_fault.get('fault_type', 'unknown')))} / {_safe_float(top_fault.get('score'), 0.0):.1f}"],
        ["维保时限建议", f"{max(1, dispatch_hours)} 小时内"],
        ["备件与工具参考", "<br>".join(line.removeprefix("- ").strip() for line in _render_parts_lines(parts))],
    ]

    lines.extend(["", "## 4. 本次判断依据"])
    lines.extend(_markdown_table(["项目", "内容"], basis_rows))
    lines.extend(["", "## 5. 给维保人员的补充参考"])
    lines.extend(_markdown_table(["项目", "内容"], maintenance_rows))

    waveform_markdown = ""
    if isinstance(waveforms, dict):
        waveform_markdown = str(waveforms.get("markdown_echarts") or waveforms.get("markdown", "")).strip()
    if waveform_markdown:
        lines.extend(["", waveform_markdown])

    return "\n".join(lines).strip() + "\n"


def build_diagnosis_result_from_alert(
    alert_payload: dict[str, Any],
    *,
    health_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    alert = alert_payload if isinstance(alert_payload, dict) else {}
    health = health_payload if isinstance(health_payload, dict) else {}

    level = str(alert.get("level", "normal")).strip().lower()
    fault_type = str(alert.get("fault_type", "")).strip() or str(health.get("last_fault_type", "unknown")).strip() or "unknown"
    score = _safe_float(alert.get("fault_confidence"), _safe_float(health.get("last_fault_confidence"), 0.0))
    if 0.0 <= score <= 1.0:
        score *= 100.0

    quality_factor = 1.0 if bool(health.get("baseline_ready", False)) else 0.65
    reasons_text = str(alert.get("fault_reasons", "") or alert.get("reasons", "")).strip()
    reasons = [item for item in reasons_text.split("|") if item]

    if fault_type not in {"", "unknown", "normal"} and level in {"warning", "anomaly"}:
        screening_status = "candidate_faults"
    elif str(alert.get("risk_level_24h", "normal")).strip().lower() in {"watch", "high", "critical"}:
        screening_status = "watch_only"
    else:
        screening_status = "normal"

    top_fault = {
        "fault_type": fault_type,
        "score": round(score, 2),
        "level": level or "normal",
        "reasons": reasons,
    }
    top_candidate = dict(top_fault) if screening_status == "candidate_faults" else {}
    watch_faults = [dict(top_fault)] if screening_status == "watch_only" else []

    return {
        "source": "edge_alert_event",
        "summary": {
            "n_raw": 0,
            "n_effective": 0,
            "fs_hz": 0.0,
            "event_ts_ms": _safe_int(alert.get("ts_ms"), 0),
        },
        "screening": {
            "status": screening_status,
            "label": _screening_label(screening_status),
        },
        "baseline": {
            "mode": "edge_event",
        },
        "top_fault": top_fault,
        "top_candidate": top_candidate,
        "candidate_faults": [dict(top_fault)] if screening_status == "candidate_faults" else [],
        "watch_faults": watch_faults,
        "preferred_issue": dict(top_fault) if screening_status != "normal" else {},
    }
