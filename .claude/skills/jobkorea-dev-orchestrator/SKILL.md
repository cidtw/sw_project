---
name: jobkorea-dev-orchestrator
description: "Orchestrator for the Developer Audit team. Triggers on 'audit jobkorea-talent-search', 'check jobkorea-talent-search errors', or 'jobkorea developer audit'."
---

# JobKorea Developer Audit Orchestrator

Orchestration skill for the backend and frontend developers to inspect the JobKorea talent search package.

## Execution Mode: Agent Team

## Agent Configuration

| Teammate | Agent Type | Role | Skill | Output |
|----------|------------|------|-------|--------|
| backend-dev | backend-dev | Python Backend Audit | code review | _workspace/03_backend_report.md |
| frontend-dev | frontend-dev | Selector & UI Audit | code review | _workspace/03_frontend_report.md |

## Workflow

### Phase 1: Context Verification
1. Verify existence of `jobkorea-talent-search/` directory.
2. Initialize `_workspace/` for logs and reports.

### Phase 2: Team Creation
1. Spawn the developer team using `TeamCreate`.
2. Register tasks via `TaskCreate` for backend-dev to audit python network call / cookie session handling, and frontend-dev to audit selector resilience.

### Phase 3: Audit Execution
- `backend-dev` reads `jobkorea_talent_search.py` and `jobkorea_talent_search_condition.py`.
- `frontend-dev` reads `jobkorea_talent_parse.py` and `test_jobkorea_talent_search.py`.
- Team members share findings via `SendMessage`.

### Phase 4: Consolidation
1. Consolidate backend and frontend reports.
2. Output a unified report highlighting unimplemented features, structural bugs, and proposed fixes.
3. Clean up the team with `TeamDelete`.
