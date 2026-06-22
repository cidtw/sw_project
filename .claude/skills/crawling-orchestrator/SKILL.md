---
name: crawling-orchestrator
description: "Orchestrator for Crawling projects. Triggers on 'crawl site', 'bypass scraping blocks', 'configure scrapy', or 'scrapy-playwright'."
---

# Crawling Orchestrator

Orchestration skill for setting up crawling tasks, designing Scrapy spiders, and deploying anti-bot bypass strategies.

## Execution Mode: Sub-agents

## Agent Configuration

| Agent | subagent_type | Role | Skill | Output |
|-------|--------------|------|-------|--------|
| crawling-expert | crawling-expert | Scrapy Spider & Stealth setup | scrapy / crawl | spiders/ & settings.py |

## Workflow

### Phase 1: Target Analysis
1. Analyze target website URL structure, headers, security levels (e.g. Cloudflare, Incapsula, Akamai).
2. Check for public data APIs or mobile web endpoints as high-priority fallbacks.

### Phase 2: Stealth Architecture Selection
1. Evaluate if raw HTTP requests (using `httpx` or `curl_cffi` for TLS fingerprinting) are sufficient.
2. If JavaScript execution or interactive login is required:
   - Configure **Playwright** with `playwright-stealth` (via `scrapy-playwright`).
   - Reason: Async native, avoids blocking event loop, supports context segregation.

### Phase 3: Code Generation
1. Delegate spider code generation to `crawling-expert` with `model: "opus"`.
2. Generate `settings.py` and middlewares configuring user-agent rotation, residential proxies, and stealth arguments.
