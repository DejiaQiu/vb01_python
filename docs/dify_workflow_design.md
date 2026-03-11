# Dify Workflow Design (FastAPI Backend)

This project already provides a backend API:

- `POST /api/v1/diagnostics/rule-engine`
- `POST /api/v1/diagnostics/waveform-plot`
- `POST /api/v1/workflows/maintenance-package`
- `POST /api/v1/workflows/diagnosis-report`
- `GET /api/v1/health/monitor`

Official Dify docs references:

- Workflow app: https://docs.dify.ai/en/guides/application-orchestrate/workflow-app
- HTTP Request node: https://docs.dify.ai/guides/workflow/node/http-request
- Execute Workflow API: https://docs.dify.ai/api-reference/workflow-execution/execute-workflow

You can integrate Dify in two standard ways:

1. Pull mode (recommended first): Dify workflow uses HTTP Request nodes to call this backend.
2. Push mode (optional): monitor service calls Dify Workflow API when alert is emitted.

The monitor-side push mode is now supported by runtime args:

- `MONITOR_DIFY_ENABLED`
- `MONITOR_DIFY_BASE_URL` (example: `https://your-dify-domain/v1`)
- `MONITOR_DIFY_API_KEY`
- `MONITOR_DIFY_MIN_LEVEL`
- `MONITOR_DIFY_COOLDOWN_S`

## A. Pull Mode Workflow (Dify calls backend)

### Node 1: Input (Start)

Input variables:

- `site_name` (string)
- `csv_path` (string, optional)
- `csv_text` (string, optional)
- `rows` (array, optional)
- `alert_csv` (string, default `data/elevator_alerts_live.csv`)
- `health_json` (string, default `data/monitor_health.json`)
- `manifest_json` (string, optional)

Rule:

- Provide one of `rows` / `csv_text` / `csv_path` for diagnosis input.

### Node 2: HTTP Request - Rule Diagnosis

Request:

- Method: `POST`
- URL: `http://<backend-host>:8085/api/v1/diagnostics/rule-engine`
- Headers: `Content-Type: application/json`
- Body:

```json
{
  "csv_path": "{{#start.csv_path#}}",
  "csv_text": "{{#start.csv_text#}}",
  "rows": {{#start.rows#}}
}
```

Expected output fields:

- `top_fault.fault_type`
- `top_fault.score`
- `top_fault.level`
- `results` (all 8-rule outputs)

### Node 2.5: HTTP Request - Waveform Plot

Request:

- Method: `POST`
- URL: `http://<backend-host>:8085/api/v1/diagnostics/waveform-plot`
- Headers: `Content-Type: application/json`
- Body:

```json
{
  "csv_path": "{{#start.csv_path#}}",
  "csv_text": "{{#start.csv_text#}}",
  "rows": {{#start.rows#}},
  "width": 920,
  "height": 320,
  "max_points": 240
}
```

Expected output fields:

- `plots.acceleration.data_uri`
- `plots.gyroscope.data_uri`
- `plots.acceleration_magnitude.data_uri`
- `markdown`

### Node 3: HTTP Request - Maintenance Package

Request:

- Method: `POST`
- URL: `http://<backend-host>:8085/api/v1/workflows/maintenance-package`
- Headers: `Content-Type: application/json`
- Body:

```json
{
  "site_name": "{{#start.site_name#}}",
  "alert_csv": "{{#start.alert_csv#}}",
  "health_json": "{{#start.health_json#}}",
  "manifest_json": "{{#start.manifest_json#}}"
}
```

Expected output fields:

- `priority`
- `maintenance_mode`
- `summary`
- `recommended_actions`
- `suggested_parts`
- `dify_inputs` (normalized payload for downstream systems)

### Node 4: HTTP Request - Diagnosis Report Context (recommended)

Request:

- Method: `POST`
- URL: `http://<backend-host>:8085/api/v1/workflows/diagnosis-report`
- Headers: `Content-Type: application/json`
- Body:

```json
{
  "site_name": "{{#start.site_name#}}",
  "csv_path": "{{#start.csv_path#}}",
  "csv_text": "{{#start.csv_text#}}",
  "rows": {{#start.rows#}},
  "maintenance_package": {{#node3#}},
  "language": "zh-CN",
  "report_style": "standard"
}
```

Expected output fields:

- `dify_prompt_template`
- `dify_report_inputs`
- `report_markdown_draft`

### Node 5: LLM - Final Human-readable Report

Input:

- `dify_prompt_template` + `dify_report_inputs` from Node 4

Output:

- A short Chinese incident brief for dispatch/notification.

### Node 6: Branch / Notification

Branch by `priority`:

- `P1/P2`: immediate notification path
- `P3/P4`: watchlist path

Output payload for downstream:

```json
{
  "ticket_title": "{{#node3.dify_inputs.ticket_title#}}",
  "ticket_priority": "{{#node3.dify_inputs.ticket_priority#}}",
  "elevator_id": "{{#node3.dify_inputs.elevator_id#}}",
  "summary": "{{#node3.dify_inputs.summary#}}",
  "report_markdown": "{{#node5.text#}}"
}
```

## B. Push Mode Workflow (Monitor calls Dify)

The realtime monitor can call Dify Workflow API (`POST /v1/workflows/run`) directly after alert emission.
This backend sends `maintenance_package` + flattened `dify_inputs` as Workflow inputs.

Recommended Dify Workflow Start variables for push mode:

- `ticket_title`
- `ticket_priority`
- `site_name`
- `elevator_id`
- `maintenance_mode`
- `dispatch_within_hours`
- `status`
- `fault_type`
- `fault_confidence`
- `risk_level_now`
- `risk_level_24h`
- `risk_24h`
- `predictive_only`
- `summary`
- `recommended_actions_text`
- `suggested_parts_text`
- `alert_context_csv`
- `model_ids`
- `maintenance_package` (full JSON string)

## API Output Contract (for Dify node mapping)

### Rule engine (`/api/v1/diagnostics/rule-engine`)

- `input`: source identifier
- `summary`: `{n_raw, n_effective, fs_hz, used_new_only, new_ratio}`
- `top_fault`: `{fault_type, score, level, triggered, reasons[]}`
- `results`: list of all rule outputs

### Maintenance package (`/api/v1/workflows/maintenance-package`)

- `workflow_type`
- `generated_at_ms`
- `site_name`
- `status`
- `elevator_id`
- `priority`
- `maintenance_mode`
- `dispatch_within_hours`
- `summary`
- `current_fault_type`
- `current_fault_confidence`
- `risk` object
- `recent_alert_stats` object
- `monitor` object
- `recommended_actions` list
- `suggested_parts` list
- `evidence` object
- `model_context` object
- `dify_inputs` object (for direct workflow mapping)

### Diagnosis report (`/api/v1/workflows/diagnosis-report`)

- `report_context_version`
- `report_title`
- `language`
- `priority`
- `top_fault` object
- `risk` object
- `diagnosis_result` object
- `maintenance_package` object
- `waveform_payload` object (when `include_waveforms=true` or payload provided)
- `dify_prompt_template` string
- `dify_report_inputs` object
- `report_markdown_draft` string
