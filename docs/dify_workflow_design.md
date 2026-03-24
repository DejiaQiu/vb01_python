# Dify Workflow Design (Scheduled Diagnosis + Offline Report)

This project now uses one main Dify workflow with two branches:

1. No CSV uploaded: query the latest scheduled batch diagnosis result.
2. CSV uploaded: generate a detailed offline diagnosis report.

The old realtime rule engine under `elevator_monitor/monitor/` is no longer the primary path for user-facing status replies. It can still be kept for acquisition health or compatibility, but the main diagnosis source is the newer candidate-fault screening logic under `report/fault_algorithms/`.

## Primary APIs

- `POST /api/v1/ingest/heartbeat`
- `POST /api/v1/ingest/alert`
- `POST /api/v1/ingest/context`
- `GET /api/v1/elevators/{elevator_id}/latest-status`
- `GET /api/v1/elevators/{elevator_id}/alerts`
- `GET /api/v1/alerts/{event_id}`
- `POST /api/v1/diagnostics/rule-engine`
- `POST /api/v1/diagnostics/batch-run`
- `GET /api/v1/diagnostics/latest-status`
- `POST /api/v1/diagnostics/waveform-plot`
- `POST /api/v1/workflows/maintenance-package`
- `POST /api/v1/workflows/diagnosis-report`
- `POST /api/v1/workflows/diagnosis-report-by-event`
- `GET /api/v1/health/monitor`

Recommended backend address for Dify:

- `http://<backend-host>:8085`

## Integrated Workflow Logic

Recommended Dify DSL file:

- [elevator_diagnosis_report_with_waveform_v2.yml](/Users/qiudejia/Downloads/vb01_python/docs/dify_workflows/elevator_diagnosis_report_with_waveform_v2.yml)

Branch rule:

- `sys.files` is empty: go to online status query branch
- `sys.files` has uploaded CSV: go to offline diagnosis report branch

### Branch A: No CSV uploaded

Purpose:

- Answer questions like "current status", "whether abnormal", "should maintenance be arranged now"
- In the edge/cloud path, read the latest synchronized elevator status and recent alert event
- Keep the old scheduled-batch `latest_status.json` query path only as a compatibility fallback

Node flow:

1. `Start`
2. `If/Else`
3. `HTTP Request` -> `GET /api/v1/elevators/{elevator_id}/latest-status`
4. `Code` -> parse the latest status JSON and build a concise summary
5. `LLM` -> answer in natural Chinese using the structured status
6. If latest status contains `last_event_id`, optionally call `POST /api/v1/workflows/diagnosis-report-by-event`
7. `Answer` -> render the text conclusion first, then render waveform charts when the event report contains them

Suggested environment variables:

- `ip`: backend base URL, for example `http://192.168.5.132:8085`
- `site_name`: display name in the reply
- `elevator_id`: current elevator identifier for the status query branch
- `latest_json`: status file path, default `data/diagnosis/latest_status.json`

Expected status payload:

- `status`
- `primary_issue`
- `system_abnormality`
- `preferred_issue` as a compatibility field
- `top_candidate` as a compatibility field
- `watch_faults`
- `risk`
- `recommendation`
- `latest_file_name`
- `generated_at_ms`
- `waveform_payload` when `include_waveforms=true`

Typical answer style:

- detection date of the latest scheduled batch diagnosis
- current status
- risk level now
- 24h risk level
- abnormal conclusion
- abnormal clues when present
- whether to continue observation or arrange inspection
- then fixed charts in this order: acceleration, gyroscope, acceleration magnitude

### Branch B: CSV uploaded

Purpose:

- Upload a CSV
- run the candidate-fault screening chain
- draw waveform charts in Dify
- generate a readable report

Current recommended node flow:

1. `Start` with file upload
2. `Document Extractor`
3. `Code` -> normalize CSV text / rows
4. `HTTP Request` -> `POST /api/v1/diagnostics/rule-engine`
5. `HTTP Request` -> `POST /api/v1/workflows/maintenance-package`
6. `Code` -> build ECharts config directly in Dify
7. `HTTP Request` -> `POST /api/v1/workflows/diagnosis-report`
8. `LLM` -> rewrite for normal readers
9. `Answer`

Notes:

- Edge/cloud协同模式下，Dify 的主入口应该是状态/事件 API，而不是文件上传。
- Waveforms are now rendered on the Dify side with `echarts` code blocks.
- The backend `waveform-plot` API is still available for other clients, but the current Dify report workflow does not need to depend on it.
- Dify has a per-variable size limit. For uploaded CSV, the workflow should compact and sample the extracted text before storing it in `csv_text`; do not pass the full extracted file text through workflow variables.

## Scheduled Batch Diagnosis

This is the new bridge between "online reply" and "offline algorithm".

Instead of running an always-on streaming rule engine, run scheduled jobs several times per day:

```bash
python3 -m elevator_monitor.batch_diagnosis \
  --input-dir data/captures \
  --max-files 12 \
  --baseline-dir data/captures \
  --baseline-start-hhmm 1015 \
  --baseline-end-hhmm 1019 \
  --latest-json data/diagnosis/latest_status.json \
  --history-jsonl data/diagnosis/history.jsonl \
  --pretty
```

What it does:

- pick the latest batch of CSV files
- reuse `report/fault_algorithms/run_all.py`
- keep the user-facing result conservative and centered on abnormal vs normal
- compute a trend-aware risk score from repeated appearances, score trend, and data quality
- write a stable `latest_status.json` for Dify to query
- include backend-generated `report_markdown_draft`, so the online-status branch can directly display a report-style summary without re-running the report workflow

For multi-elevator deployments:

- store each elevator under `data/diagnosis/elevator_<id>/latest_status.json`
- let the online-status branch pass `elevator_id`
- if the user query contains text like `002号梯` or `elevator_002`, Dify should extract that id and call `GET /api/v1/diagnostics/latest-status?elevator_id=002`
- if no elevator id is mentioned, the workflow may fall back to the default `latest_json`

Recommended scheduling:

- run `3` to `6` times per day
- keep a stable latest file path
- append history to `history.jsonl`

## Legacy Realtime Chain

The old monitor path is still present in code:

- `elevator_monitor/monitor/runtime.py`
- `elevator_monitor/fault_types.py`
- `elevator_monitor/risk_predictor.py`

Recommended role now:

- acquisition health
- process supervision
- compatibility only

Not recommended as the main source for user-facing diagnosis:

- old generic realtime fault labels
- old realtime risk score

For user-facing online status, prefer:

- scheduled batch diagnosis result
- `GET /api/v1/diagnostics/latest-status`

## API Contracts

### `POST /api/v1/diagnostics/batch-run`

Input:

- `input_dir`
- `csv_paths`
- `max_files`
- `baseline_json`
- `baseline_dir`
- `baseline_start_hhmm`
- `baseline_end_hhmm`
- `latest_json`
- `history_jsonl`
- `write_outputs`

Output:

- `workflow_type`
- `generated_at_ms`
- `status`
- `primary_issue`
- `system_abnormality`
- `preferred_issue`
- `top_candidate`
- `watch_faults`
- `risk`
- `recommendation`
- `latest_result`
- `history`
- `output_files`

### `GET /api/v1/diagnostics/latest-status`

Query:

- `latest_json`

Output:

- latest scheduled batch diagnosis payload
- `latest_json`

### `POST /api/v1/diagnostics/rule-engine`

Input:

- `csv_path` or `csv_text` or `rows`

Output:

- `summary`
- `screening`
- `primary_issue`
- `system_abnormality`
- `top_fault`
- `top_candidate`
- `candidate_faults`
- `watch_faults`
- `results`

### `POST /api/v1/workflows/diagnosis-report`

Output:

- `report_title`
- `dify_prompt_template`
- `dify_report_inputs`
- `report_markdown_draft`
- `waveform_payload` when enabled or supplied

Important Dify mapping rule:

- Prefer `primary_issue` as the main diagnosis source
- Treat `preferred_issue` and `top_candidate` as compatibility fallbacks only
- Prefer `system_abnormality` when you only need to answer "abnormal or not"
- When `status=watch_only` and `primary_issue.fault_type=unknown`, render the main conclusion as `已检测到异常，但类型待确认`

## Recommended Product Split

No CSV uploaded:

- read latest status only
- return a short answer
- answer current risk and whether the latest batch already shows abnormality

CSV uploaded:

- inspect one batch in detail
- show waveforms and reasoning
- produce a report for maintenance review
