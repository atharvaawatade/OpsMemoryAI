# OpsMemory AI — Technical Architecture (Domination Edition)

## 🏗️ High-Level Architecture

```mermaid
graph TD
    User[Developer] -->|git push| GitHub[GitHub Actions]
    GitHub -->|Invokes| Gateway[CI Gateway Agent (Python)]
    
    subgraph "OpsMemory Core (Elasticsearch)"
        Gateway -->|1. Semantic Search| ELSER[ELSER Model (.elser_model_2)]
        Gateway -->|2. Pattern Detection| ESQL[ES|QL Engine]
        Gateway -->|3. Severity Check| Aggregations[Data Aggregations]
        
        ELSER -->|Retrieves| Incidents[ops-incidents]
        ESQL -->|Analyzes| Incidents
    end
    
    subgraph "Reasoning Engine (GPT-4o)"
        Gateway -->|4. Synthesis| LLM[GPT-4o]
        LLM -->|5. Verdict| Decision[Final Decision]
    end
    
    Decision -->|Block/Allow| GitHub
    Decision -->|Log Trace| TraceIndex[agent-reasoning-traces]
```

## 🧩 Component Breakdown

### 1. CI Gateway Agent (`ci_agent.py`)
The "Orchestrator" that runs in CI/CD. It doesn't just call an API; it performs a **multi-step cognitive process**:
1.  **Context Extraction**: Parses PR description, files changed, and deployment time.
2.  **Tool Selection**:
    *   *Semantic Search Tool*: Finds semantically similar past incidents.
    *   *Cascading Graph Tool*: Uses ES|QL to find if this root cause triggers others.
    *   *Severity Predictor*: Calculates potential financial impact.
3.  **Reasoning Loop**: Synthesizes all data into a JSON "Reasoning Trace".
4.  **Enforcement**: Returns exit code `1` (BLOCK) or `0` (ALLOW).

### 2. Knowledge Store (Elasticsearch)
*   `ops-incidents`: Stores past failure data with `semantic_text` embeddings.
*   `agent-reasoning-traces`: **[NEW]** Stores the full "thought process" of the agent for every single pull request.
*   `agent-overrides`: **[NEW]** Tracks when a human overrides the agent, for "Reinforcement Learning".

## 🔄 The "Reasoning Trace" (Gap Stickiveness)
Unlike standard linters, we verify **why** a decision was made. Every decision produces a trace:
```json
{
  "trace_id": "uuid",
  "steps": [
    {"tool": "semantic_search", "output": "Found INC-089 (89% match)"},
    {"tool": "temporal_check", "output": "Incident is recent (3 months ago)"},
    {"tool": "graph_analysis", "output": "High risk of cascading failure (67%)"}
  ],
  "reasoning": "Blocked because semantic match + cascading risk signals a repeat of INC-089.",
  "outcome": "DENY"
}
```

## 🛡️ Resilience & Performance
*   **Fail-Safe**: If Elasticsearch is down, we default to `ALLOW` (don't block pipeline on tool failure) but alert the team.
*   **Latency**: Target decision time < 200ms using `connection_pool` and caching.
