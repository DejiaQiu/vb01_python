# Project Agents

## Requirement-Driven Delivery

When the user asks to implement a new feature, bugfix, workflow, or API from a written requirement, follow this order:

1. Read the requirement file the user points to. If the user does not provide a path, default to `requirements/feature_request.md`.
2. Validate the requirement with `python -m elevator_monitor.feature_requirements <path>` before editing code.
3. If required fields are missing or still contain placeholders, stop and ask only for the blocking items.
4. Limit edits to the modules declared in the requirement unless the implementation clearly needs a small supporting change elsewhere.
5. Treat `验收标准` and `测试用例` as implementation requirements, not optional notes. Add or update automated tests accordingly.
6. Preserve the existing elevator monitor, training, and reporting flows unless the requirement explicitly changes them.

## Project Conventions

- New API endpoints belong under `elevator_monitor/api/routers/`.
- Shared parsing, validation, or workflow helpers belong under `elevator_monitor/`.
- Do not add new dependencies unless the requirement explicitly justifies them.
- Prefer small incremental changes over broad refactors.
