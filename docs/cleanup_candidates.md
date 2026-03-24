# Cleanup Candidates

These files look like legacy or experimental paths that are not part of the current mainline runtime (`elevator_monitor.monitor`) or the current FastAPI backend (`elevator_monitor.api`).

## Removed In This Iteration

- `report/app.py`
- `report/one_click_vib_to_ding.py`
- `report/rubber_hardening_detector.py`
- `report/vib_anomaly_detector.py`
- `report/fault_algorithms/detect_rope_looseness.py`
- `report/fault_algorithms/detect_rubber_hardening.py`
- `report/fault_algorithms/rope_looseness_timeline.py`
- `tests/test_rope_looseness_detector.py`
- `tests/test_rubber_hardening_detector.py`
- `tests/rope_tension.py`

## Remaining Legacy Candidate

- `report/wire_looseness_index.py`
  - Separate experimental wire-looseness scoring/model builder.
  - Not part of `report/fault_algorithms/run_all.py`.
  - Keeps an alternative rope/wire path that can conflict with the main rope-looseness rule.

## Generated Artifacts

These are output files, not source code, and can usually be archived or removed from the repo/workspace:

- `report/*.md` generated diagnosis reports
- `report/*_latest.json`
- `report/restored_vibration/`
- `report/converted_vibration/`
- `report/__pycache__/`
- root `__pycache__/`

## Suggested Cleanup Order

1. Keep only one rope-related experimental path (`report/wire_looseness_index.py` vs current mainline `report/fault_algorithms/rope_vs_rubber.py`).
2. Archive generated artifacts periodically.
