# Dify Workflow Design (Scheduled Diagnosis + Offline Report)

This project now uses one main Dify workflow centered on direct text queries.

1. Default production path: users ask directly, and Dify reads the latest report for the target elevator.
2. CSV upload is no longer the default interaction mode; keep it only for debug or offline validation when needed.

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
- `GET/POST /api/v1/workflows/diagnosis-report-latest`
- `POST /api/v1/workflows/diagnosis-report-by-event`
- `GET /api/v1/health/monitor`

Recommended backend address for Dify:

- `http://<backend-host>:8085`

## Integrated Workflow Logic

Recommended Dify DSL file:

- [elevator_diagnosis_report_with_waveform_v2.yml](/Users/qiudejia/Downloads/vb01_python/docs/dify_workflows/elevator_diagnosis_report_with_waveform_v2.yml)

Branch rule:

- Default production workflow should rely on `sys.query` and call `diagnosis-report-latest`
- For Dify HTTP nodes, prefer `POST` with JSON body over `GET` query params to avoid URL encoding issues
- File upload can be disabled in the default app so the workflow always enters the direct-query branch
- If a separate debug workflow keeps CSV upload enabled, the CSV branch still does not depend on `sys.query`
- Only when `system_abnormality.baseline_mode == robust_baseline` and `top_deviations` contains real `value / median / z / effective_scale` may Dify render or describe a "baseline median / deviation z" table
- If `baseline_mode` is `self_normalized_fallback` or `mapping_mismatch_fallback`, Dify should only present the anomaly score and explain that no directly comparable health-baseline statistics are available; do not render missing stats as `0`

### Branch A: No CSV uploaded

Purpose:

- Answer questions like "current status", "whether abnormal", "should maintenance be arranged now"
- Use one backend call to return the latest report context for the requested elevator
- Keep `latest-status` and `diagnosis-report-by-event` only as compatibility or sidecar APIs

Node flow:

1. `Start`
2. `Code` -> parse `sys.query` and extract elevator id
3. `HTTP Request` -> `POST /api/v1/workflows/diagnosis-report-latest`
4. `Code` -> compact the report JSON and extract chart markdown / tables
5. `LLM` -> answer in natural Chinese using the structured report
6. `Answer` -> render the text conclusion first, then render waveform charts from the returned report context

Suggested environment variables:

- `ip`: backend base URL, for example `http://192.168.5.132:8085`
- `site_name`: display name in the reply
- `elevator_id`: current elevator identifier for the status query branch
- `latest_json`: status file path, default `data/diagnosis/latest_status.json`

Expected report payload:

- `status`
- `primary_issue`
- `detector_results`
- `system_abnormality`
- `preferred_issue` as a compatibility field
- `top_candidate` as a compatibility field
- `watch_faults`
- `risk`
- `recommendation`
- `latest_file_name`
- `generated_at_ms`
- `waveform_payload` when `include_waveforms=true`
- `report_markdown_draft`
- `dify_report_inputs`

Typical answer style:

- detection date of the latest scheduled batch diagnosis
- current status
- risk level now
- 24h risk level
- abnormal conclusion
- abnormal clues when present
- whether to continue observation or arrange inspection
- then fixed charts in this order: full-frequency spectrum, low-frequency spectrum, acceleration, gyroscope, acceleration magnitude

### Branch B: CSV uploaded

Purpose:

- Optional debug path only
- Upload a CSV
- run the candidate-fault screening chain
- if the anomaly gate is hit, run a conservative detector attribution step
- draw full-frequency and low-frequency spectrum charts plus waveform charts in Dify
- generate a readable report
- this branch should tolerate empty user text and rely on the uploaded file as the primary input

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

- Edge/cloud协同模式下，Dify 的主入口应该是 `diagnosis-report-latest` 这样的直接报告 API，而不是文件上传。
- Waveforms are now rendered on the Dify side with `echarts` code blocks.
- The backend `waveform-plot` API is still available for other clients, but the current Dify report workflow does not need to depend on it.
- Dify has a per-variable size limit. For uploaded CSV, the workflow should compact and sample the extracted text before storing it in `csv_text`; do not pass the full extracted file text through workflow variables.
- For explanation cards and report tables, treat `system_abnormality.top_deviations` as displayable only in `robust_baseline` mode. Fallback mode may still have a score, but it does not mean backend has a real baseline median or z-statistic for each feature.

How Dify should explain the method:

- Describe the main algorithm as: first compare the window with this elevator's own health baseline, then do a conservative detector attribution only after the anomaly gate is hit.
- If users ask about the "template" or "rule", explain that the template is a weak prior made from manually chosen feature directions/ranges; it is not a trained model and not a hard threshold.
- If `primary_issue` or `preferred_issue` is `rope_looseness` / `rubber_hardening`, phrase it as "更像 / 偏向" instead of "已经确认".
- If the backend returns `unknown`, Dify must clearly say "已检测到异常，但类型待确认" and stop there.

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
- include backend-generated `report_markdown_draft`, so the online branch can directly display a report-style summary

For multi-elevator deployments:

- store each elevator under `data/diagnosis/elevator_<id>/latest_status.json`
- let the online branch pass `elevator_id`
- if the user query contains text like `002号梯` or `elevator_002`, Dify should extract that id and call `POST /api/v1/workflows/diagnosis-report-latest` with `{"elevator_id":"002"}` in the JSON body
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
- `GET/POST /api/v1/workflows/diagnosis-report-latest`

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

### `GET/POST /api/v1/workflows/diagnosis-report-latest`

Input:

- `elevator_id`
- `site_name`
- `latest_json`
- `latest_root`
- `include_waveforms`

Output:

- latest report context for the target elevator
- `status`
- `screening`
- `primary_issue`
- `preferred_issue`
- `detector_results`
- `risk`
- `report_markdown_draft`
- `waveform_payload` when enabled

### `POST /api/v1/diagnostics/rule-engine`

Input:

- `csv_path` or `csv_text` or `rows`

Output:

- `summary`
- `screening`
- `primary_issue`
- `detector_results`
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
- Use `system_abnormality.score` for generic abnormal-vs-normal language
- Only mention `top_deviations[*].median` or `top_deviations[*].z` when `system_abnormality.baseline_mode == robust_baseline`
- When `primary_issue.fault_type` is `unknown`, keep the wording at the “abnormal but type pending” level; do not force it into rope or rubber
- Treat `preferred_issue` and `top_candidate` as compatibility fallbacks only
- Prefer `system_abnormality` when you only need to answer "abnormal or not"
- When `status=watch_only` and `primary_issue.fault_type=unknown`, render the main conclusion as `已检测到异常，但类型待确认`

## Recommended Product Split

No CSV uploaded:

- read the latest report only
- return a short answer
- answer current risk and whether the latest batch already shows abnormality

CSV uploaded:

- inspect one batch in detail
- show waveforms and reasoning
- produce a report for maintenance review
