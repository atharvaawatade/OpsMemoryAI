# OpsMemory AI — Demo Script
> Length: ~3 minutes | Goal: Show "not a chatbot" — it's an operational engine.

## Opening (20 seconds)
**Visual:** Flash a slide: "OpsMemory AI: Operational Policy Engine Powered by Organizational Memory".
**Voiceover:** "Engineering teams don't fail because servers go down. They fail because they repeat the same mistakes nobody remembers. OpsMemory is an operational policy engine — it reads your organization's complete history in Elasticsearch and autonomously prevents repeated failures."
**Visual:** Quick flash of the implementation diagram (4 access points).

## Scenario 1: The Deployment Gate (Intercept Mode) (70 seconds)
**Visual:** Split screen: Terminal on left (Deployment Gate), Agent Builder on right.
**Action:**
1. In Terminal, run: `python3 gateway/ci_agent.py "checkout-service" "3.0.0" "Increased retry_count to 50"`
2. Terminal shows: **"🤖 AGENT BUILDER EXECUTION TRACE"**
3. **Agent Builder UI:** Show the agent actively reasoning (thinking). It picks tools:
   - `[00:00.050] 🔧 Tool Selection: semantic_search, cascading_pattern_detector (Parallel)`
   - `[00:00.120] 🔍 Semantic Search: Found INC-0027 (Retry Storm)`
   - `[00:01.950] ✅ Decision Complete: DENY`
4. **Result:** Terminal shows HUGE RED ASCII BANNER: **🚫 DEPLOYMENT BLOCKED**.
5. **Impact:** Show the **"⚡ PERFORMANCE METRICS"** section at the bottom (Total Latency: 0.19ms).
**Voiceover:** "Here, a developer tries to increase retry counts. The agent doesn't just chat — it intercepts the deployment. Searching organizational memory with multi-vector search, it finds incident #27 (a retry storm caused by this exact change). Deployment halted in under 200 milliseconds. Crisis averted."

## Scenario 2: Active Incident Investigation (Investigate Mode) (50 seconds)
**Visual:** Agent Chat UI (Kibana).
**Action:**
1. Type: "Checkout page response times spiked to 8 seconds after today's release."
2. **Agent Builder UI:** Show the *different* tool order (ES|QL first this time!).
   - Tool Call 1: `analyze_patterns` (Start with checking recent trends)
   - Tool Call 2: `search_memory` (Then look for similar symptoms like "Redis pool exhaustion")
   - Decision: "Likely recurrence of Incident #38. Creating ticket..."
   - Tool Call 3: `execute_action` (Create Ticket INC-089)
**Voiceover:** "Now, an active incident. Notice the agent dynamically changes strategy. For incidents, it starts with ES|QL pattern analysis first, then search. It correctly identifies a likely Redis pool exhaustion similar to Incident #38, creates a ticket, and pages the right team automatically."

## MCP Demo: IDE Integration (20 seconds)
**Visual:** Cursor / VS Code / Claude Desktop.
**Action:**
1. In the chat window, type: "What is the retry policy for checkout-service?"
2. **Result:** The IDE uses the `opsmemory` MCP server to query the agent directly and get the answer (ADR-23) without leaving the editor.
**Voiceover:** "And because Agent Builder has a built-in MCP server, this organizational memory is available directly inside your IDE. Developers get context where they work."

## Closing (15 seconds)
**Visual:** Architecture diagram again.
**Voiceover:** "OpsMemory AI isn't a chatbot. It's an infrastructure layer that remembers every incident, decision, and policy — ensuring your organization never makes the same mistake twice. Built on Elastic Agent Builder."
