import { NextResponse } from "next/server";

const KIBANA_URL = process.env.KIBANA_URL || "";
const API_KEY    = process.env.ELASTIC_API_KEY || "";
const AGENT_ID   = process.env.AGENT_ID || "opsmemory-enforcer";

// A2A Agent Card — the machine-readable "business card" that lets
// any external agent discover and call OpsMemory as a sub-agent.
// Spec: https://google.github.io/A2A/
export async function GET() {
  // Try to fetch the live agent card from Kibana Agent Builder
  if (KIBANA_URL && API_KEY) {
    try {
      const res = await fetch(
        `${KIBANA_URL}/api/agent_builder/a2a/${AGENT_ID}.json`,
        {
          headers: {
            Authorization: `ApiKey ${API_KEY}`,
            "kbn-xsrf": "true",
          },
          signal: AbortSignal.timeout(8000),
        }
      );
      if (res.ok) {
        const card = await res.json();
        return NextResponse.json(card);
      }
    } catch {
      // Fall through to static card
    }
  }

  // Static agent card — spec-compliant A2A format
  const agentCard = {
    name: "OpsMemory Enforcer",
    description:
      "Production-grade CI/CD deployment gate that checks every deployment against organizational incident history and architectural decisions. Prevents recurring production failures by enforcing institutional memory at merge time.",
    url: `${KIBANA_URL}/api/agent_builder/converse`,
    version: "1.0.0",
    provider: {
      organization: "OpsMemory AI",
      url: "https://github.com/atharvaawatade/opsmemory",
    },
    capabilities: {
      streaming: false,
      pushNotifications: false,
      stateTransitionHistory: true,
    },
    defaultInputModes: ["text/plain"],
    defaultOutputModes: ["text/plain"],
    skills: [
      {
        id: "intercept_deployment",
        name: "Intercept Deployment",
        description:
          "Checks a proposed deployment against ADRs (policy_search), historical incidents (incident_memory_search), and failure patterns (cascading_pattern_detector). Returns APPROVE, DENY, or NEEDS_REVIEW with full evidence chain.",
        tags: ["ci-cd", "deployment", "policy", "incident-memory"],
        examples: [
          "Is it safe to deploy checkout-service v4.1.0 with retry_count increased to 50?",
          "Should we approve this PR that removes the circuit breaker on payment-gateway?",
        ],
        inputModes: ["text/plain"],
        outputModes: ["text/plain"],
      },
      {
        id: "investigate_incident",
        name: "Investigate Active Incident",
        description:
          "Semantic search over historical incidents to find similar past failures and suggest root cause and resolution for an active problem.",
        tags: ["incident-response", "root-cause", "semantic-search"],
        examples: [
          "Checkout service latency has spiked to 8 seconds — what caused this before?",
          "We're seeing 500 errors on payment-gateway after today's deployment",
        ],
        inputModes: ["text/plain"],
        outputModes: ["text/plain"],
      },
    ],
    authentication: {
      schemes: ["ApiKey"],
    },
  };

  return NextResponse.json(agentCard, {
    headers: { "Content-Type": "application/json" },
  });
}
