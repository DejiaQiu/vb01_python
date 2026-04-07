from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from ...batch_diagnosis import load_latest_status
from ...control_plane import discover_elevator_ids, get_control_store
from ...ingest_store import get_ingest_store
from ...latest_status_service import attach_latest_waveforms, resolve_latest_status_path
from ...reporting_service import (
    build_report_context_from_edge_event,
    build_report_context_from_latest_status,
    render_report_markdown,
)


router = APIRouter(tags=["control"])


def _compact_issue(issue: dict[str, Any] | None) -> dict[str, Any]:
    payload = issue if isinstance(issue, dict) else {}
    if not payload:
        return {}
    return {
        "fault_type": str(payload.get("fault_type", "unknown")),
        "score": float(payload.get("score", 0.0) or 0.0),
        "level": str(payload.get("level", "normal")),
        "triggered": bool(payload.get("triggered", False)),
    }


def _load_diagnosis_latest(elevator_id: str) -> dict[str, Any]:
    path = resolve_latest_status_path("data/diagnosis/latest_status.json", elevator_id, "data/diagnosis")
    if not path.exists():
        return {}
    try:
        return load_latest_status(str(path))
    except Exception:
        return {}


def _load_latest_status_summary(elevator_id: str) -> dict[str, Any]:
    payload = get_ingest_store().get_latest_status(elevator_id)
    if not payload:
        payload = _load_diagnosis_latest(elevator_id)
    if not payload:
        return {}
    context = payload.get("context", {}) if isinstance(payload.get("context"), dict) else {}
    latest_file = (
        str(payload.get("latest_file", "")).strip()
        or str(payload.get("alert_context_path", "")).strip()
        or str(context.get("stored_path", context.get("local_path", ""))).strip()
    )
    latest_file_name = str(payload.get("latest_file_name", "")).strip()
    if not latest_file_name and latest_file:
        latest_file_name = Path(latest_file).name
    return {
        "status": str(payload.get("status", "unknown")),
        "generated_at_ms": int(payload.get("generated_at_ms", payload.get("received_at_ms", 0)) or 0),
        "preferred_issue": _compact_issue(payload.get("preferred_issue")),
        "top_candidate": _compact_issue(payload.get("top_candidate")),
        "risk": dict(payload.get("risk", {})) if isinstance(payload.get("risk"), dict) else {},
        "latest_file": latest_file,
        "latest_file_name": latest_file_name,
        "last_event_id": str(payload.get("last_event_id", payload.get("event_id", ""))),
    }


def _state_summary(elevator_id: str) -> dict[str, Any]:
    state = get_control_store().ensure_state(elevator_id)
    monitor = state.get("monitor", {}) if isinstance(state.get("monitor"), dict) else {}
    latest = _load_latest_status_summary(elevator_id)
    updated_at_ms = int(monitor.get("updated_at_ms", state.get("updated_at_ms", 0)) or 0)
    return {
        "elevator_id": elevator_id,
        "lifecycle_stage": str(state.get("lifecycle_stage", "commissioning")),
        "baseline_ready": bool(state.get("baseline_ready", False)),
        "baseline_count": int(state.get("baseline_count", 0) or 0),
        "baseline_frozen": bool(state.get("baseline_frozen", False)),
        "baseline_learning_enabled": bool(state.get("baseline_learning_enabled", False)),
        "alerts_enabled": bool(state.get("alerts_enabled", False)),
        "connected": bool(monitor.get("connected", False)),
        "monitor_status": str(monitor.get("status", "unknown")),
        "last_fault_type": str(monitor.get("last_fault_type", "unknown")),
        "last_screening_status": str(monitor.get("last_screening_status", "normal")),
        "last_risk_level_24h": str(monitor.get("last_risk_level_24h", "normal")),
        "updated_at_ms": updated_at_ms,
        "stale_seconds": round(max(0.0, time.time() - updated_at_ms / 1000.0), 1) if updated_at_ms > 0 else None,
        "last_applied_command": dict(state.get("last_applied_command", {})) if isinstance(state.get("last_applied_command"), dict) else {},
        "latest_status": latest,
    }


def _full_state(elevator_id: str) -> dict[str, Any]:
    store = get_control_store()
    state = store.ensure_state(elevator_id)
    monitor = state.get("monitor", {}) if isinstance(state.get("monitor"), dict) else {}
    latest = _load_latest_status_summary(elevator_id)
    alerts = get_ingest_store().list_alerts(elevator_id, limit=10)
    return {
        "elevator_id": elevator_id,
        "control_state": state,
        "monitor": monitor,
        "latest_status": latest,
        "recent_alerts": alerts,
        "report_url": f"/api/v1/workflows/diagnosis-report-latest?elevator_id={quote(elevator_id)}&include_waveforms=true",
    }


def _preview_from_diagnosis_latest(elevator_id: str) -> dict[str, Any]:
    path = resolve_latest_status_path("data/diagnosis/latest_status.json", elevator_id, "data/diagnosis")
    if not path.exists():
        return {}
    payload = load_latest_status(str(path))
    payload = dict(payload)
    payload["latest_json"] = str(path)
    payload["requested_elevator_id"] = elevator_id
    payload = attach_latest_waveforms(payload, width=560, height=220, max_points=180)
    waveforms = dict(payload.get("waveform_payload", {})) if isinstance(payload.get("waveform_payload"), dict) else {}
    report_ctx = build_report_context_from_latest_status(
        latest_status_payload=payload,
        elevator_id=elevator_id,
        waveform_payload=waveforms,
    )
    markdown = render_report_markdown(report_ctx)
    return {
        "source": "diagnosis_latest",
        "report_markdown_draft": markdown,
        "waveform_payload": waveforms,
        "waveform_error": str(payload.get("waveform_error", "")),
        "latest_file": str(payload.get("latest_file", "")),
        "latest_file_name": str(payload.get("latest_file_name", "")),
    }


def _preview_from_edge_latest(elevator_id: str) -> dict[str, Any]:
    latest = get_ingest_store().get_latest_status(elevator_id)
    if not latest:
        return {}
    event_id = str(latest.get("last_event_id", latest.get("event_id", ""))).strip()
    if not event_id:
        return {}
    event_payload = get_ingest_store().get_alert(event_id)
    if not event_payload:
        return {}
    report_ctx = build_report_context_from_edge_event(
        alert_event=event_payload,
        include_waveforms=True,
    )
    markdown = render_report_markdown(report_ctx)
    waveforms = dict(report_ctx.get("waveform_payload", {})) if isinstance(report_ctx.get("waveform_payload"), dict) else {}
    context = event_payload.get("context", {}) if isinstance(event_payload.get("context"), dict) else {}
    return {
        "source": "edge_event",
        "report_markdown_draft": markdown,
        "waveform_payload": waveforms,
        "waveform_error": "",
        "latest_file": str(context.get("stored_path", context.get("local_path", ""))),
        "latest_file_name": str(context.get("file_name", "")),
        "event_id": event_id,
    }


def _load_preview(elevator_id: str) -> dict[str, Any]:
    for loader in (_preview_from_diagnosis_latest, _preview_from_edge_latest):
        try:
            preview = loader(elevator_id)
        except Exception:
            preview = {}
        if preview:
            return preview
    return {
        "source": "none",
        "report_markdown_draft": "",
        "waveform_payload": {},
        "waveform_error": "no preview available",
        "latest_file": "",
        "latest_file_name": "",
    }


def _issue_control_command(elevator_id: str, action: str) -> dict[str, Any]:
    try:
        state = get_control_store().issue_command(elevator_id, action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "elevator_id": elevator_id,
        "action": action,
        "pending_command": state.get("pending_command", {}),
        "control_state": state,
    }


@router.get("/api/v1/control/elevators")
def control_elevators() -> dict[str, Any]:
    ids = discover_elevator_ids()
    items = [_state_summary(elevator_id) for elevator_id in ids]
    return {
        "count": len(items),
        "items": items,
    }


@router.get("/api/v1/control/elevators/{elevator_id}/state")
def control_elevator_state(elevator_id: str) -> dict[str, Any]:
    return _full_state(elevator_id)


@router.get("/api/v1/control/elevators/{elevator_id}/preview")
def control_elevator_preview(elevator_id: str) -> dict[str, Any]:
    payload = _load_preview(elevator_id)
    payload["elevator_id"] = elevator_id
    return payload


@router.post("/api/v1/control/elevators/{elevator_id}/baseline/start")
def control_start_baseline(elevator_id: str) -> dict[str, Any]:
    return _issue_control_command(elevator_id, "start_baseline")


@router.post("/api/v1/control/elevators/{elevator_id}/baseline/freeze")
def control_freeze_baseline(elevator_id: str) -> dict[str, Any]:
    return _issue_control_command(elevator_id, "freeze_baseline")


@router.post("/api/v1/control/elevators/{elevator_id}/baseline/resume")
def control_resume_baseline(elevator_id: str) -> dict[str, Any]:
    return _issue_control_command(elevator_id, "resume_baseline")


@router.post("/api/v1/control/elevators/{elevator_id}/baseline/reset")
def control_reset_baseline(elevator_id: str) -> dict[str, Any]:
    return _issue_control_command(elevator_id, "reset_baseline")


@router.get("/panel", response_class=HTMLResponse)
def control_panel() -> str:
    return """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Elevator Control Panel</title>
  <style>
    :root {
      --bg: #f5efe6;
      --bg-accent: #efe4d4;
      --panel: rgba(255, 251, 245, 0.88);
      --line: rgba(91, 72, 51, 0.14);
      --text: #2b2218;
      --muted: #6d6257;
      --ok: #2f6a4f;
      --warn: #ac6b12;
      --bad: #a43827;
      --action: #134b63;
      --action-2: #7f3f00;
      --shadow: 0 24px 60px rgba(52, 39, 24, 0.12);
      --radius: 20px;
      --font: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--font);
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.72), transparent 34%),
        linear-gradient(135deg, var(--bg) 0%, var(--bg-accent) 100%);
      min-height: 100vh;
    }
    .shell {
      width: min(1280px, calc(100vw - 32px));
      margin: 24px auto 40px;
      display: grid;
      gap: 18px;
    }
    .hero, .panel, .json-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }
    .hero {
      padding: 22px 24px;
      display: grid;
      gap: 14px;
    }
    .hero-top {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      flex-wrap: wrap;
    }
    .eyebrow {
      letter-spacing: 0.12em;
      text-transform: uppercase;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
    }
    .hero h1 {
      margin: 0;
      font-size: clamp(30px, 4vw, 48px);
      line-height: 0.95;
      max-width: 620px;
    }
    .hero p {
      margin: 0;
      color: var(--muted);
      max-width: 620px;
      line-height: 1.5;
    }
    .hero-tools {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
    }
    select, button, a.link-btn {
      border-radius: 999px;
      border: 1px solid var(--line);
      padding: 11px 16px;
      font: inherit;
    }
    select {
      min-width: 220px;
      background: rgba(255,255,255,0.82);
    }
    button, a.link-btn {
      background: var(--action);
      color: #fff;
      cursor: pointer;
      text-decoration: none;
      transition: transform 140ms ease, opacity 140ms ease;
    }
    button.secondary {
      background: var(--action-2);
    }
    button.ghost {
      background: transparent;
      color: var(--text);
    }
    button:hover, a.link-btn:hover {
      transform: translateY(-1px);
      opacity: 0.94;
    }
    .grid {
      display: grid;
      grid-template-columns: 1.15fr 0.85fr;
      gap: 18px;
    }
    .stack {
      display: grid;
      gap: 18px;
    }
    .panel {
      padding: 20px;
    }
    .panel h2 {
      margin: 0 0 14px;
      font-size: 18px;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .stat {
      padding: 14px;
      border-radius: 16px;
      background: rgba(255,255,255,0.62);
      border: 1px solid rgba(91, 72, 51, 0.08);
    }
    .stat .label {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .stat .value {
      font-size: 24px;
      font-weight: 700;
      line-height: 1.1;
    }
    .stat .sub {
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
    }
    .tag {
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      background: rgba(19, 75, 99, 0.12);
      color: var(--action);
    }
    .tag.ok { background: rgba(47, 106, 79, 0.14); color: var(--ok); }
    .tag.warn { background: rgba(172, 107, 18, 0.16); color: var(--warn); }
    .tag.bad { background: rgba(164, 56, 39, 0.16); color: var(--bad); }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }
    .table {
      display: grid;
      gap: 10px;
    }
    .row {
      display: grid;
      grid-template-columns: 120px 1fr;
      gap: 12px;
      align-items: start;
      padding: 10px 0;
      border-bottom: 1px dashed rgba(91, 72, 51, 0.14);
    }
    .row:last-child {
      border-bottom: 0;
      padding-bottom: 0;
    }
    .key {
      color: var(--muted);
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .value {
      word-break: break-word;
      line-height: 1.5;
    }
    .alert-list {
      display: grid;
      gap: 10px;
    }
    .alert-item {
      border-radius: 14px;
      border: 1px solid rgba(91, 72, 51, 0.10);
      background: rgba(255,255,255,0.72);
      padding: 12px 14px;
    }
    .alert-head {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
      margin-bottom: 8px;
      flex-wrap: wrap;
    }
    .mono, pre {
      font-family: "SFMono-Regular", "JetBrains Mono", monospace;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 12px;
      line-height: 1.5;
      max-height: 320px;
      overflow: auto;
      color: #31261b;
    }
    .json-card { padding: 16px 18px; }
    .preview-meta {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    .report-preview {
      max-height: 340px;
      overflow: auto;
      padding: 14px;
      border-radius: 16px;
      background: rgba(255,255,255,0.62);
      border: 1px solid rgba(91, 72, 51, 0.08);
      white-space: pre-wrap;
      line-height: 1.6;
      font-size: 13px;
    }
    .waveform-grid {
      display: grid;
      gap: 12px;
    }
    .wave-card {
      padding: 12px;
      border-radius: 16px;
      background: rgba(255,255,255,0.62);
      border: 1px solid rgba(91, 72, 51, 0.08);
    }
    .wave-card h3 {
      margin: 0 0 8px;
      font-size: 14px;
    }
    .wave-card img {
      display: block;
      width: 100%;
      height: auto;
      border-radius: 10px;
      border: 1px solid rgba(91, 72, 51, 0.08);
      background: #fff;
    }
    .muted { color: var(--muted); }
    .empty {
      padding: 18px;
      border-radius: 16px;
      background: rgba(255,255,255,0.58);
      color: var(--muted);
    }
    @media (max-width: 980px) {
      .grid {
        grid-template-columns: 1fr;
      }
      .stats {
        grid-template-columns: 1fr;
      }
      .row {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="hero-top">
        <div>
          <div class="eyebrow">Edge Commissioning Console</div>
          <h1>电梯设备首装与基线控制面板</h1>
          <p>把新设备接入、人工确认健康、开始建基线、自动切到正式监控，全部收敛到一个页面里。</p>
        </div>
        <div class="hero-tools">
          <select id="elevatorSelect"></select>
          <a id="reportLink" class="link-btn" href="#" target="_blank" rel="noreferrer">查看最新报告</a>
        </div>
      </div>
      <div id="heroTags" class="actions"></div>
    </section>

    <div class="grid">
      <div class="stack">
        <section class="panel">
          <h2>运行概览</h2>
          <div id="stats" class="stats"></div>
        </section>

        <section class="panel">
          <h2>控制动作</h2>
          <div class="actions">
            <button data-action="start">开始建基线</button>
            <button class="secondary" data-action="freeze">冻结基线</button>
            <button class="secondary" data-action="resume">恢复自适应</button>
            <button class="ghost" data-action="reset">重建基线</button>
          </div>
          <div id="commandInfo" class="muted" style="margin-top: 14px;"></div>
        </section>

        <section class="panel">
          <h2>状态详情</h2>
          <div id="details" class="table"></div>
        </section>

        <section class="panel">
          <h2>最近告警</h2>
          <div id="alerts" class="alert-list"></div>
        </section>
      </div>

      <div class="stack">
        <section class="panel">
          <h2>最新报告预览</h2>
          <div id="previewMeta" class="preview-meta"></div>
          <div id="reportPreview" class="report-preview muted">等待加载最新报告。</div>
        </section>

        <section class="panel">
          <h2>实时波形 / 频谱预览</h2>
          <div id="waveformPreview" class="waveform-grid">
            <div class="empty">等待加载波形预览。</div>
          </div>
        </section>

        <section class="json-card">
          <h2 style="margin-top: 0;">控制状态 JSON</h2>
          <pre id="controlJson">{}</pre>
        </section>
        <section class="json-card">
          <h2 style="margin-top: 0;">监控状态 JSON</h2>
          <pre id="monitorJson">{}</pre>
        </section>
      </div>
    </div>
  </div>

  <script>
    const elevatorSelect = document.getElementById("elevatorSelect");
    const statsEl = document.getElementById("stats");
    const detailsEl = document.getElementById("details");
    const alertsEl = document.getElementById("alerts");
    const controlJsonEl = document.getElementById("controlJson");
    const monitorJsonEl = document.getElementById("monitorJson");
    const commandInfoEl = document.getElementById("commandInfo");
    const heroTagsEl = document.getElementById("heroTags");
    const reportLinkEl = document.getElementById("reportLink");
    const previewMetaEl = document.getElementById("previewMeta");
    const reportPreviewEl = document.getElementById("reportPreview");
    const waveformPreviewEl = document.getElementById("waveformPreview");

    let currentElevatorId = "";
    let pollTimer = null;
    let previewPollTimer = null;

    function prettyJson(value) {
      return JSON.stringify(value || {}, null, 2);
    }

    function tagClass(value) {
      const text = String(value || "").toLowerCase();
      if (["monitoring", "normal", "connected", "true", "watch"].includes(text)) return "tag ok";
      if (["baseline_building", "warning"].includes(text)) return "tag warn";
      if (["commissioning", "false", "anomaly", "critical", "high"].includes(text)) return "tag bad";
      return "tag";
    }

    async function fetchJson(url, options) {
      const response = await fetch(url, options);
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || ("HTTP " + response.status));
      }
      return response.json();
    }

    function renderStats(payload) {
      const state = payload.control_state || {};
      const monitor = payload.monitor || {};
      const latest = payload.latest_status || {};
      const cards = [
        {
          label: "Lifecycle",
          value: state.lifecycle_stage || "commissioning",
          sub: "控制状态机"
        },
        {
          label: "Baseline",
          value: String(state.baseline_count || 0),
          sub: (state.baseline_ready ? "ready" : "warming") + (state.baseline_frozen ? " / frozen" : "")
        },
        {
          label: "Connection",
          value: monitor.connected ? "online" : "offline",
          sub: monitor.status || "unknown"
        },
        {
          label: "Latest",
          value: (latest.preferred_issue && latest.preferred_issue.fault_type) || monitor.last_fault_type || "unknown",
          sub: latest.status || monitor.last_screening_status || "normal"
        }
      ];
      statsEl.innerHTML = cards.map((card) => `
        <div class="stat">
          <div class="label">${card.label}</div>
          <div class="value">${card.value}</div>
          <div class="sub">${card.sub}</div>
        </div>
      `).join("");
    }

    function renderHeroTags(payload) {
      const state = payload.control_state || {};
      const monitor = payload.monitor || {};
      const latest = payload.latest_status || {};
      const tags = [
        state.lifecycle_stage || "commissioning",
        state.baseline_ready ? "baseline ready" : "baseline warming",
        state.baseline_frozen ? "baseline frozen" : "baseline adaptive",
        monitor.connected ? "device online" : "device offline",
        latest.status || monitor.last_screening_status || "normal"
      ];
      heroTagsEl.innerHTML = tags.map((tag) => `<span class="${tagClass(tag)}">${tag}</span>`).join("");
    }

    function renderDetails(payload) {
      const state = payload.control_state || {};
      const monitor = payload.monitor || {};
      const latest = payload.latest_status || {};
      const rows = [
        ["电梯 ID", payload.elevator_id || "-"],
        ["建基线确认", state.commissioning_confirmed ? "已确认" : "未确认"],
        ["基线学习", state.baseline_learning_enabled ? "开启" : "关闭"],
        ["告警放行", state.alerts_enabled ? "开启" : "关闭"],
        ["最近筛查", monitor.last_screening_status || "-"],
        ["24h 风险", monitor.last_risk_level_24h || (latest.risk || {}).risk_level_24h || "-"],
        ["最后命令", ((state.last_applied_command || {}).action || "-") + " / " + ((state.last_applied_command || {}).message || "-")],
        ["最新问题", (latest.preferred_issue || {}).fault_type || monitor.last_fault_type || "-"],
        ["画像路径", monitor.profile_path || state.profile_path || "-"]
      ];
      detailsEl.innerHTML = rows.map(([key, value]) => `
        <div class="row">
          <div class="key">${key}</div>
          <div class="value">${value}</div>
        </div>
      `).join("");
    }

    function renderAlerts(payload) {
      const alerts = payload.recent_alerts || [];
      if (!alerts.length) {
        alertsEl.innerHTML = `<div class="empty">当前没有最近告警事件。</div>`;
        return;
      }
      alertsEl.innerHTML = alerts.map((item) => {
        const alertPayload = item.alert_payload || {};
        return `
          <div class="alert-item">
            <div class="alert-head">
              <strong>${alertPayload.fault_type || "unknown"}</strong>
              <span class="${tagClass(alertPayload.level || "normal")}">${alertPayload.level || "normal"}</span>
            </div>
            <div class="muted mono">event=${item.event_id || "-"} ts=${item.ts_ms || "-"}</div>
            <div style="margin-top: 8px;">risk24h=${alertPayload.risk_level_24h || "-"} score=${alertPayload.fault_confidence || 0}</div>
          </div>
        `;
      }).join("");
    }

    function renderPreview(payload) {
      const waveformPayload = payload.waveform_payload || {};
      const plots = waveformPayload.plots || {};
      const metaTags = [
        payload.source || "none",
        payload.latest_file_name || "no-file",
        payload.waveform_error ? "waveform unavailable" : "waveform ready"
      ];
      previewMetaEl.innerHTML = metaTags.map((item) => `<span class="${tagClass(item)}">${item}</span>`).join("");
      reportPreviewEl.textContent = payload.report_markdown_draft || "当前没有可用报告预览。";

      const preferredOrder = [
        ["full_frequency_spectrum", "全频频谱"],
        ["low_frequency_spectrum", "低频频谱"],
        ["acceleration", "加速度波形"],
        ["gyroscope", "角速度波形"],
        ["acceleration_magnitude", "合成加速度"]
      ];
      const cards = preferredOrder
        .map(([key, title]) => {
          const plot = plots[key] || {};
          const src = plot.data_uri || "";
          if (!src) return "";
          return `
            <div class="wave-card">
              <h3>${title}</h3>
              <img src="${src}" alt="${title}" />
            </div>
          `;
        })
        .filter(Boolean);

      waveformPreviewEl.innerHTML = cards.length
        ? cards.join("")
        : `<div class="empty">${payload.waveform_error || "当前没有可用波形预览。"}</div>`;
    }

    async function refreshState(pushUrl = false) {
      if (!currentElevatorId) return;
      const payload = await fetchJson(`/api/v1/control/elevators/${encodeURIComponent(currentElevatorId)}/state`);
      renderHeroTags(payload);
      renderStats(payload);
      renderDetails(payload);
      renderAlerts(payload);
      controlJsonEl.textContent = prettyJson(payload.control_state);
      monitorJsonEl.textContent = prettyJson(payload.monitor);
      reportLinkEl.href = payload.report_url || "#";
      const lastCommand = ((payload.control_state || {}).last_applied_command || {});
      commandInfoEl.textContent = lastCommand.action ? `最后应用命令: ${lastCommand.action} / ${lastCommand.message || "-"}` : "尚未执行控制命令。";
      if (pushUrl) {
        const url = new URL(window.location.href);
        url.searchParams.set("elevator_id", currentElevatorId);
        window.history.replaceState({}, "", url.toString());
      }
    }

    async function refreshPreview() {
      if (!currentElevatorId) return;
      const payload = await fetchJson(`/api/v1/control/elevators/${encodeURIComponent(currentElevatorId)}/preview`);
      renderPreview(payload);
    }

    async function loadElevators() {
      const payload = await fetchJson("/api/v1/control/elevators");
      const items = payload.items || [];
      elevatorSelect.innerHTML = items.length
        ? items.map((item) => `<option value="${item.elevator_id}">${item.elevator_id}</option>`).join("")
        : `<option value="">未发现电梯</option>`;
      if (!items.length) {
        currentElevatorId = "";
        return;
      }
      const requested = new URL(window.location.href).searchParams.get("elevator_id");
      currentElevatorId = requested && items.some((item) => item.elevator_id === requested) ? requested : items[0].elevator_id;
      elevatorSelect.value = currentElevatorId;
      await refreshState(true);
      await refreshPreview();
    }

    async function sendAction(action) {
      if (!currentElevatorId) return;
      const endpointByAction = {
        start: "start",
        freeze: "freeze",
        resume: "resume",
        reset: "reset",
      };
      const suffix = endpointByAction[action];
      if (!suffix) return;
      await fetchJson(`/api/v1/control/elevators/${encodeURIComponent(currentElevatorId)}/baseline/${suffix}`, { method: "POST" });
      await refreshState(false);
      await refreshPreview();
    }

    elevatorSelect.addEventListener("change", async (event) => {
      currentElevatorId = event.target.value;
      await refreshState(true);
      await refreshPreview();
    });

    document.querySelectorAll("[data-action]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await sendAction(button.dataset.action);
        } finally {
          button.disabled = false;
        }
      });
    });

    (async function boot() {
      try {
        await loadElevators();
        pollTimer = window.setInterval(() => {
          refreshState(false).catch((error) => {
            commandInfoEl.textContent = error.message || String(error);
          });
        }, 3000);
        previewPollTimer = window.setInterval(() => {
          refreshPreview().catch((error) => {
            commandInfoEl.textContent = error.message || String(error);
          });
        }, 15000);
      } catch (error) {
        commandInfoEl.textContent = error.message || String(error);
      }
    })();
  </script>
</body>
</html>
"""
