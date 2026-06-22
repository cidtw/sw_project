---
name: caveman-orchestrator
description: "Orchestrator for the Caveman agent team. Triggers on 'caveman mode', '/caveman', or 'talk like caveman'. Supports running '/caveman full' to activate the default terse mode."
---

# Caveman Orchestrator

Orchestrator for the Caveman agent team to ensure token-efficient communications.

## Execution Mode: Sub-agents

## Agent Configuration

| Agent | subagent_type | Role | Skill | Output |
|-------|--------------|------|-------|--------|
| caveman-agent | caveman-agent | Terse Code Execution | caveman | console |

## Workflow

### Phase 1: Initialize Caveman Mode
1. Parse command options (e.g. `/caveman full`, `/caveman ultra`, `/caveman lite`).
2. If the user invokes `/caveman full`, transition the model's prose communication style to "full" caveman intensity:
   - Drop articles (a/an/the)
   - Use short fragments
   - Drop conversational filler (just/really/basically)
3. Delegate tasks to `caveman-agent` with `model: "opus"`.

### Phase 2: Execution
1. Send tasks to `caveman-agent`.
2. Ensure output adheres to target compression levels (e.g., ~75% token savings).
