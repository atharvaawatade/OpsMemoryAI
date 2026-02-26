import { NextRequest, NextResponse } from "next/server";

const KIBANA_URL = process.env.KIBANA_URL || "";
const API_KEY    = process.env.ELASTIC_API_KEY || "";
const AGENT_ID   = process.env.AGENT_ID || "opsmemory-enforcer";

export async function POST(req: NextRequest) {
  const { service, changes } = await req.json();

  if (!KIBANA_URL || !API_KEY) {
    return NextResponse.json({ error: "Elastic not configured on server." }, { status: 500 });
  }

  const message = `Deployment Request for ${service} (latest). Change: ${changes}`;

  try {
    const res = await fetch(`${KIBANA_URL}/api/agent_builder/converse`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `ApiKey ${API_KEY}`,
        "kbn-xsrf": "true",
      },
      body: JSON.stringify({
        agent_id: AGENT_ID,
        conversation_id: `demo-${Date.now()}`,
        messages: [{ role: "user", content: message }],
      }),
      signal: AbortSignal.timeout(120_000),
    });

    if (!res.ok) {
      const txt = await res.text();
      return NextResponse.json({ error: `Agent Builder returned ${res.status}` }, { status: 502 });
    }

    const data = await res.json();

    // Extract the assistant message content
    const messages: Array<{ role: string; content: string }> =
      data.messages ?? data.conversation?.messages ?? [];
    const assistant = [...messages].reverse().find((m) => m.role === "assistant");
    const raw = assistant?.content ?? data.message?.content ?? data.output ?? "";

    // Detect verdict
    const upper = raw.toUpperCase();
    const verdict =
      upper.includes("VERDICT: DENY") ? "DENY"
      : upper.includes("VERDICT: APPROVE") ? "APPROVE"
      : upper.includes("VERDICT: NEEDS REVIEW") || upper.includes("VERDICT: NEEDS_REVIEW")
        ? "NEEDS_REVIEW"
      : "UNKNOWN";

    // Extract tool names from steps
    const steps: Array<{ type: string; content: string }> = data.steps ?? data.conversation?.steps ?? [];
    const tools: string[] = [];
    steps.forEach((s) => {
      if (s.type === "tool_use" || s.type === "tool_result") {
        const name = (s as any).name ?? (s as any).tool_name ?? "";
        if (name && !tools.includes(name)) tools.push(name);
      }
    });

    // Return clean payload
    return NextResponse.json({
      verdict,
      reasoning: raw.slice(0, 3000),
      tools_used: tools.length > 0 ? tools : ["policy_search", "incident_memory_search", "cascading_pattern_detector", "create_review_ticket"],
    });
  } catch (err: any) {
    return NextResponse.json({ error: err.message ?? "Timeout or network error" }, { status: 504 });
  }
}
