---
name: ui-design-orchestrator
description: "Orchestrator for Web Design and UI Audit. Triggers on 'design web app', 'improve UI aesthetics', 'run UI audit', or 'ui-skills'."
---

# UI Design Orchestrator

Orchestration skill applying `ibelick/ui-skills` principles to build and review premium user interfaces.

## Execution Mode: Agent Team

## Agent Configuration

| Teammate | Agent Type | Role | Skill | Output |
|----------|------------|------|-------|--------|
| web-designer | web-designer | Premium UI Styling | styling | index.css / design system |
| ui-auditor | ui-auditor | UI Audit (Aesthetics, Accessibility) | audit | _workspace/ui_audit_report.md |

## Workflow

### Phase 1: Initialize Project Design
1. Analyze user design requests, branding parameters, and layouts.
2. Establish target styles (color palette, typography, glassmorphism, responsive breakpoints).

### Phase 2: Design and Build
1. Delegate styling rules creation to `web-designer` with `model: "opus"`.
2. Generate base stylesheets (e.g. `index.css`) containing high-fidelity variables and micro-animations.

### Phase 3: Audit & Polish
1. Run `ui-auditor` on the generated styles and HTML DOM.
2. Audit accessibility (ARIA, tags), motion performance (duration, delays), and SEO metadata.
3. Apply auditor feedback to refine the design system.

### Phase 4: Finalize
1. Review final layout on local/dev build.
2. Compile components into the production-ready bundle.
