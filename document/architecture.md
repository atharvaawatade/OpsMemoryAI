# OpsMemory AI — Modular Architecture

> Operational Policy Engine — Plug-and-Play Design

---

## System Overview

```
                    ┌─────────────────────────────────┐
                    │        ACCESS LAYER (Pluggable)  │
                    │                                  │
                    │  ┌────────┐ ┌─────┐ ┌─────┐     │
                    │  │Chat UI │ │ MCP │ │ A2A │ ... │
                    │  └───┬────┘ └──┬──┘ └──┬──┘     │
                    │      └─────────┼───────┘        │
                    └────────────────┼────────────────┘
                                     │
                    ┌────────────────▼────────────────┐
                    │      GATEWAY LAYER (Pluggable)   │
                    │                                  │
                    │  ┌──────────────────────────┐    │
                    │  │    Deploy Gate (Python)   │    │
                    │  │    Future: Alert Gate     │    │
                    │  │    Future: PR Gate        │    │
                    │  └────────────┬─────────────┘    │
                    └───────────────┼──────────────────┘
                                    │
                    ┌───────────────▼──────────────────┐
                    │      AGENT CORE (Agent Builder)   │
                    │                                   │
                    │  ┌─────────────────────────────┐  │
                    │  │  LLM: GPT-4o (AI Connector) │  │
                    │  │                             │  │
                    │  │  System Prompt Engine:       │  │
                    │  │  ┌─────────┐ ┌───────────┐  │  │
                    │  │  │INTERCEPT│ │INVESTIGATE│  │  │
                    │  │  └─────────┘ └───────────┘  │  │
                    │  │  ┌────────┐  ┌──────┐       │  │
                    │  │  │ESCALATE│  │ADVISE│       │  │
                    │  │  └────────┘  └──────┘       │  │
                    │  └──────────┬──────────────────┘  │
                    │             │                      │
                    │  ┌──────────▼──────────────────┐  │
                    │  │    TOOL LAYER (Pluggable)    │  │
                    │  │                             │  │
                    │  │  ┌────────┐ ┌──────────┐    │  │
                    │  │  │ Search │ │  ES|QL   │    │  │
                    │  │  │ Memory │ │ Patterns │    │  │
                    │  │  └────┬───┘ └────┬─────┘    │  │
                    │  │  ┌────┴──┐ ┌─────┴──────┐   │  │
                    │  │  │Workflw│ │Future Tools│   │  │
                    │  │  │Action │ │(Alert,Call)│   │  │
                    │  │  └────┬──┘ └─────┬──────┘   │  │
                    │  └───────┼──────────┼──────────┘  │
                    └──────────┼──────────┼─────────────┘
                               │          │
                    ┌──────────▼──────────▼─────────────┐
                    │       DATA LAYER (Elasticsearch)   │
                    │                                    │
                    │  ┌──────────────┐ ┌──────────────┐ │
                    │  │ops-incidents │ │ops-decisions  │ │
                    │  │  semantic +  │ │  semantic +   │ │
                    │  │  keyword     │ │  keyword      │ │
                    │  └──────────────┘ └──────────────┘ │
                    │  ┌──────────────┐                   │
                    │  │Future Index  │ (ops-alerts, etc)│
                    │  └──────────────┘                   │
                    └────────────────────────────────────┘
```

---

## Layer 1: Access Layer (Plug-and-Play)

| Entry Point | Type | How | Extensible? |
|---|---|---|---|
| **Chat UI** | Kibana built-in | Direct Agent Builder chat | ✅ Default |
| **MCP Server** | Built-in | `POST /api/agent_builder/mcp` | ✅ Auto-exposed |
| **A2A Server** | Built-in | `GET /api/agent_builder/a2a/.json` | ✅ Auto-exposed |
| **Deploy Gate** | Custom Python | Calls Agent Builder chat API | ✅ Plugin pattern |
| *Future: Alert Gate* | Custom Python | Same pattern as deploy gate | ✅ Just add script |
| *Future: Slack Bot* | Custom | Same API call pattern | ✅ Just add script |

**Plugin Pattern**: Any new access point just calls `POST /api/agent_builder/chat/{AGENT_ID}` with a prompt. Zero agent changes needed.

---

## Layer 2: Gateway Layer (Interceptors)

Gateways are thin Python scripts that sit between external systems and the agent:

```python
# ALL gateways follow this pattern:
def check_with_agent(context: dict) -> dict:
    prompt = format_prompt(context)  # Format context into prompt
    response = call_agent(prompt)     # POST to Agent Builder API
    decision = parse_decision(response) # Extract APPROVE/DENY
    return {"decision": decision, "evidence": response}
```

| Gateway | Input | Agent Decision | Side Effect |
|---------|-------|---------------|-------------|
| `deploy_gate.py` | Deploy request | APPROVE/DENY | Halt deploy |
| *`alert_gate.py`* | Monitoring alert | KNOWN/NEW | Auto-triage |
| *`pr_gate.py`* | Pull request | SAFE/REVIEW | Block merge |

---

## Layer 3: Agent Core

### LLM: GPT-4o via Elastic AI Connector
- Configured in Kibana → Stack Management → Connectors → OpenAI
- Model: `gpt-4o` — best reasoning for dynamic tool selection

### Behavior Modes (Dynamic Selection)

| Mode | Trigger | Tool Order | Output |
|------|---------|------------|--------|
| **INTERCEPT** | Deploy/change request | Search → ES\|QL → Workflow | DENY + ticket |
| **INVESTIGATE** | Active incident | ES\|QL → Search → Workflow | Correlate + escalate |
| **ESCALATE** | Unknown context | Search → Workflow | Review ticket |
| **ADVISE** | Policy question | Search | Answer + citations |

> Different tool orders prove genuine agent autonomy to judges.

---

## Layer 4: Tool Layer (Pluggable)

### Current Tools

| Tool | Type | Purpose |
|------|------|---------|
| `search_memory` | Search | Hybrid BM25 + semantic search over incidents & decisions |
| `analyze_patterns` | ES\|QL | Temporal pattern detection, trend analysis, stats |
| `execute_action` | Workflow | Create tickets, tag teams, attach docs, post alerts |

### Future Tools (Just add in Agent Builder)
- `send_alert` — Trigger PagerDuty/Slack notification
- `call_agent` — Delegate to another agent via A2A
- `update_runbook` — Auto-update runbooks based on new incidents

---

## Layer 5: Data Layer

### `ops-incidents` Index
```json
{
  "incident_id": "keyword",
  "title": "text",
  "description": "semantic_text",
  "service": "keyword",
  "severity": "keyword",
  "severity_num": "integer",
  "root_cause": "text",
  "resolution": "text",
  "resolution_summary": "semantic_text",
  "created_at": "date",
  "resolved_at": "date",
  "duration_minutes": "integer",
  "team": "keyword",
  "tags": "keyword",
  "related_decisions": "keyword",
  "postmortem_url": "keyword",
  "runbook_id": "keyword"
}
```

### `ops-decisions` Index
```json
{
  "decision_id": "keyword",
  "type": "keyword",
  "title": "text",
  "content": "semantic_text",
  "service": "keyword",
  "status": "keyword",
  "created_at": "date",
  "updated_at": "date",
  "author": "keyword",
  "tags": "keyword",
  "related_incidents": "keyword",
  "superseded_by": "keyword"
}
```

### Extensibility
- Adding a new data type = create new index + add to search tool
- Example: `ops-alerts` for monitoring alerts, `ops-deployments` for deploy history

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent Framework | Elastic Agent Builder |
| LLM | GPT-4o (via AI Connector) |
| Data Store | Elasticsearch Cloud Serverless |
| Search | Hybrid BM25 + ELSER semantic |
| Analytics | ES\|QL |
| Automation | Elastic Workflows |
| IDE Access | MCP Server (built-in) |
| Agent Access | A2A Server (built-in) |
| Deploy Gate | Python (requests) |
| Data Seeding | Python script |

---

## Why This Architecture Wins

1. **Modular**: Each layer is independent. Change one without touching others.
2. **Extensible**: Add new access points, gates, tools, or indices without modifying existing code.
3. **Plugin pattern**: All external integrations follow the same `call_agent()` pattern.
4. **Elasticsearch-only**: No Supabase, no PostgreSQL. ES is storage + search + analytics + reasoning.
5. **Zero-code MCP/A2A**: Agent Builder auto-exposes tools. We get 2 access points free.
