
# 🎥 OpsMemory AI: The "Real Deal" Demo Script
**Objective:** Record a 3-minute video showing REAL policy enforcement using Semantic Search + Agents.

---

## 🎬 Prologue: The Setup (0:00 - 0:30)
**Visual:** Show `TECHNICAL_EXPLANATION.md` or a slide with "Organizational Memory".
**Voiceover:**
"We've all been there. A deployment causes an outage. We write a post-mortem. We promise to fix it. Six months later... the exact same incident happens again. Why? Because documents don't stop code. Agents do."

## 🎬 Scenario 1: The Gate (0:30 - 1:30)
**Visual:** Terminal (VS Code).
**Action:**
1.  Clear terminal.
2.  Run the **Real CI Agent**:
    ```bash
    python gateway/ci_agent.py "checkout-service" "v2.5.0" "Increased retry_count to 10"
    ```
3.  **Pause** while it searches (show the "Real-Time Search..." message).
4.  **Highlight** the output:
    *   "Found 3 relevant past incidents" (INC-0027 is the key one).
    *   "Found 2 relevant policies" (ADR-0023).
    *   **"Verdict: DENY"**
    *   **"⛔ DEPLOYMENT BLOCKED"**

**Voiceover:**
"Here, I'm deploying a change to increase retry counts. OpsMemory intercepts this. It doesn't just scan for syntax errors. It uses semantic search to recall Incident 27—a retry storm caused by this exact configuration last year. It enforces the lesson we learned the hard way. Deployment blocked."

## 🎬 Scenario 2: The Investigation (1:30 - 2:30)
**Visual:** Kibana -> Agent Builder -> "OpsMemory AI" -> Chat.
**Action:**
1.  Type: *"What happened in the checkout service last time we had latency issues?"*
2.  Agent finds INC-0027 and explains.
3.  Type: *"Run an analysis on recent timeout patterns."*
4.  Agent runs ES|QL query.

**Voiceover:**
"Engineers can also talk to OpsMemory directly. Using the Agent Builder, I can ask about history or analyze live patterns using ES|QL. It turns our passive runbooks into an active teammate."

## 🎬 Scenario 3: The Impact (2:30 - 3:00)
**Visual:** Show the GitHub Action YAML (`.github/workflows/deploy.yml`) or a simple metrics slide.
**Voiceover:**
"This isn't a simulation. It's running in our CI/CD pipeline, powered by Elastic's vector database and GPT-4. We aren't just summarizing incidents; we're preventing them. This is Organizational Memory as Code."

---

## 🛠️ Recording Tips
1.  **Zoom In:** Make sure the terminal font is large.
2.  **Red is Good:** The "DEPLOYMENT BLOCKED" banner is your money shot. Hold on it.
3.  **Real Data:** Emphasize that "INC-0027" is a real record in your database, not a hardcoded string.
