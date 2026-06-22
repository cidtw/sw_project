---
name: recruiting-pipeline-orchestrator
description: "Orchestrator for unmanned recruiting pipeline loop. Triggers on 'recruiting pipeline', 'run recruiting loop', 'recruiting-pipeline-orchestrator', or 'recruit pipeline status'."
license: MIT
metadata:
  category: recruiting
  locale: ko-KR
  phase: v1
---

# Recruiting Pipeline Orchestrator

Orchestration skill for the unmanned recruiting pipeline loop. Executes scheduled runs, manages state persistence, and coordinates execution phases.

## Execution Mode: Hybrid
- **Phases 1-3:** Executed via local python workspace scripts (`run_in_background` / subprocess style).
- **Phase 4 (VERIFY):** Executed by `verifier-agent`.
- **Phase 5 (DISPATCH):** Triggered via Activepieces Slack Webhook.

## State Machine
The state machine is stored in [pipeline_state.json](file:///c:/Users/MyDream/Desktop/git/k-skill/data/pipeline_state.json).

```
[FETCH] ➔ [ENRICH] ➔ [SCORE] ➔ [VERIFY] ➔ [DISPATCH]
```

## Workflow

### Phase 0: Context Verification
1. Read [pipeline_state.json](file:///c:/Users/MyDream/Desktop/git/k-skill/data/pipeline_state.json).
2. Check `current_phase`. If not empty, resume from the matching phase.
3. Validate `user_profile_hash`. If changed, invalidate cache.

### Phase 1: FETCH (Collection & Deep Crawl)
1. Run crawler script to fetch today's job postings from JobKorea.
2. Extract job URL, navigate to detail page, and scrape detailed JD, welfare, and employment type.
3. Output raw postings to `_workspace/fetch_output.json`.

### Phase 2: ENRICH (External Data Mapping)
1. Match company names with DART API to get scale and business synopsis.
2. Query National Pension data to calculate hiring growth trends and average salaries.
3. Output enriched data to `_workspace/enrich_output.json`.

### Phase 3: SCORE (Personalized Analysis)
1. Retrieve user profile from profile settings.
2. Call OpenAI/ChatGPT to calculate Cosine Similarity between JD and user profile.
3. Structure output in refined JSON format.
4. Output scored listings to `_workspace/score_output.json`.

### Phase 4: VERIFY (Hallucination QA)
1. Spawn `verifier-agent` to check output schema integrity.
2. Compare final JSON against source URL and data specifications.
3. Output validated data to `_workspace/verify_output.json`.

### Phase 5: DISPATCH (Slack Board)
1. Post payload to Activepieces Slack webhook.
2. Update state memory `last_processed_id` with latest processed IDs.
3. Reset `current_phase` to empty.
