# PE-Workflow development

- This is a standalone project. Do not modify sibling `aeep-dsh` when developing it.
- Backend source of truth: `backend/pe_workflow/models.py`; generated JSON Schema is `/api/schema` and `examples/workflow.schema.json`.
- Never guess missing bindings, silently use zero for failed models, or execute uploaded Python with eval/exec/importlib.
- Preserve module immutability, workflow optimistic version checks, frozen run snapshots and replay behavior.
- Keep physical declarations and runtime output validation aligned. Units and aggregation semantics are part of contracts.
- New code runs from this project root; application data belongs in `data/`, never `backend/`.
- Backend checks: `.venv/Scripts/python.exe -m pytest backend/tests -q`.
- Frontend checks: in `frontend/`, `npm run build`. Use `npm ci` with the lockfile for installation.
- Windows run: `powershell -File scripts/start.ps1`; default loopback port 8020.
- Do not claim detailed DBSRC/DAB or real-device accuracy from the simplified thermal demonstration.
