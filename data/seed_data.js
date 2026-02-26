
const { Client } = require("@elastic/elasticsearch");
require("dotenv").config();

// Initialize client
const client = new Client({
    node: process.env.ELASTICSEARCH_URL,
    auth: { apiKey: process.env.ELASTIC_API_KEY },
});

const INCIDENTS_INDEX = "ops-incidents";
const DECISIONS_INDEX = "ops-decisions";

// ------------------------------------------------------------------
// DATA GENERATORS
// ------------------------------------------------------------------

function generateIncidents() {
    const incidents = [];
    const services = ["checkout-service", "auth-service", "payment-gateway", "inventory-api", "frontend-bff"];
    const teams = ["checkout-team", "platform-security", "payments", "core-platform"];

    // 1. RETRY STORM CLUSTER (Critical for Scenario 1)
    // Incident #27: The reference incident
    incidents.push({
        incident_id: "INC-0027",
        title: "Checkout service latency spike and database connection saturation",
        description: "Sudden spike in checkout latency correlating with database CPU pegging at 100%. Investigation revealed a retry storm caused by a transient network blip. The checkout-service retry count was set to 10 with exponential backoff disabled, causing 10x traffic amplification instantly.",
        service: "checkout-service",
        severity: "SEV-1",
        severity_num: 1,
        root_cause: "Retry amplification due to misconfigured retry policy (count=10) and lack of circuit breaking.",
        resolution: "Rolled back the config change. Reduced max retries to 3. Added circuit breaker.",
        resolution_summary: "Reduced max retries to 3 and enabled circuit breaker pattern.",
        created_at: new Date(Date.now() - 1000 * 60 * 60 * 24 * 120).toISOString(), // 4 months ago
        resolved_at: new Date(Date.now() - 1000 * 60 * 60 * 24 * 120 + 3600000).toISOString(),
        duration_minutes: 60,
        team: "checkout-team",
        tags: ["retry-storm", "database", "latency"],
        related_decisions: ["ADR-0023"],
        postmortem_url: "https://wiki.internal/incidents/27",
        runbook_id: "RB-015"
    });

    // Recent retry tremors (to show pattern)
    [1, 2, 3].forEach((i) => {
        incidents.push({
            incident_id: `INC-00${40 + i}`,
            title: "Transient DB connection errors in checkout",
            description: "Minor spike in DB connection errors. Auto-resolved but indicates retry pressure during peak load.",
            service: "checkout-service",
            severity: "SEV-3",
            severity_num: 3,
            root_cause: "Aggressive retries on timeouts.",
            resolution: "Auto-resolved.",
            created_at: new Date(Date.now() - 1000 * 60 * 60 * 24 * (10 * i)).toISOString(), // Last 30 days
            duration_minutes: 15,
            team: "checkout-team",
            tags: ["database", "connection-pool"],
            related_decisions: ["ADR-0023"]
        });
    });

    // 2. REDIS POOL CLUSTER (Critical for Scenario 2)
    // Incident #38: The reference incident
    incidents.push({
        incident_id: "INC-0038",
        title: "Checkout page slow response times due to Redis pool exhaustion",
        description: "Checkout page load times increased to 5s+. Application logs showed 'Connection pool timeout' errors when connecting to Redis cache. Traffic volume was normal, but connection leasing was not released properly in the new 'user-context' middleware.",
        service: "checkout-service",
        severity: "SEV-2",
        severity_num: 2,
        root_cause: "Redis connection leak in user-context middleware. Pool size default (50) was insufficient for new pattern.",
        resolution: "Increased pool size to 80 temporarily. Patched middleware to ensure connection release in finally block.",
        resolution_summary: "Patched middleware leak and increased connection pool size.",
        created_at: new Date(Date.now() - 1000 * 60 * 60 * 24 * 45).toISOString(), // 1.5 months ago
        duration_minutes: 45,
        team: "checkout-team",
        tags: ["redis", "performance", "memory-leak"],
        related_decisions: [],
        runbook_id: "RB-008"
    });

    // 3. FILLER INCIDENTS (varied)
    for (let i = 0; i < 30; i++) {
        const svc = services[Math.floor(Math.random() * services.length)];
        incidents.push({
            incident_id: `INC-01${10 + i}`,
            title: `${svc} health check flapping`,
            description: `Intermittent failures in ${svc} health check endpoint. Suspect network congestion or GC pauses.`,
            service: svc,
            severity: "SEV-3",
            severity_num: 3,
            root_cause: "GC pauses.",
            resolution: "Tuned JVM heap settings.",
            created_at: new Date(Date.now() - 1000 * 60 * 60 * 24 * Math.floor(Math.random() * 180)).toISOString(),
            duration_minutes: 10 + Math.floor(Math.random() * 50),
            team: teams[Math.floor(Math.random() * teams.length)],
            tags: ["performance", "operations"]
        });
    }

    return incidents;
}

function generateDecisions() {
    const decisions = [];

    // ADR-23: The blocker for Scenario 1
    decisions.push({
        decision_id: "ADR-0023",
        type: "ADR",
        title: "Standardize Retry Policies Across Services",
        content: "Status: Accepted. Context: We have had multiple incidents caused by aggressive retry storms (multiplying traffic 10-100x during outages). Decision: All synchronous RPC calls must limit retries to maximum 3 attempts. Exponential backoff must be used. Circuit breakers must be implemented for all dependencies. Consequences: Services currently using simple retries > 3 must be refactored.",
        service: "global",
        status: "active",
        created_at: "2025-10-15T10:00:00Z",
        author: "platform-arch",
        tags: ["reliability", "retries", "standard"],
        related_incidents: ["INC-0027"]
    });

    // Runbook RB-015
    decisions.push({
        decision_id: "RB-015",
        type: "RUNBOOK",
        title: "Handling Retry Storms & Cascading Failures",
        content: "Symptoms: High latency, DB CPU 100%, massive increase in request volume. Mitigation: 1. Enable circuit breakers immediately via feature flag 'global.cb.enable'. 2. If valid traffic is blocked, scale read replicas. 3. Restart services with retry_count=0 if persistent.",
        service: "global",
        status: "active",
        created_at: "2025-11-01T10:00:00Z",
        author: "sre-team",
        tags: ["runbook", "incident-response"]
    });

    return decisions;
}

// ------------------------------------------------------------------
// INGESTION
// ------------------------------------------------------------------

async function seed() {
    try {
        console.log("🚀 Starting data seeding...");

        // 1. Create Mappings
        console.log(`Creating index ${INCIDENTS_INDEX}...`);
        // Delete if exists
        await client.indices.delete({ index: INCIDENTS_INDEX, ignore_unavailable: true });

        await client.indices.create({
            index: INCIDENTS_INDEX,
            body: {
                mappings: {
                    properties: {
                        incident_id: { type: "keyword" },
                        title: { type: "text" },
                        description: { type: "semantic_text" },
                        service: { type: "keyword" },
                        severity: { type: "keyword" },
                        severity_num: { type: "integer" },
                        root_cause: { type: "text" },
                        resolution: { type: "text" },
                        resolution_summary: { type: "semantic_text" },
                        created_at: { type: "date" },
                        resolved_at: { type: "date" },
                        duration_minutes: { type: "integer" },
                        team: { type: "keyword" },
                        tags: { type: "keyword" },
                        related_decisions: { type: "keyword" },
                        postmortem_url: { type: "keyword" },
                        runbook_id: { type: "keyword" }
                    }
                }
            }
        });

        console.log(`Creating index ${DECISIONS_INDEX}...`);
        await client.indices.delete({ index: DECISIONS_INDEX, ignore_unavailable: true });

        await client.indices.create({
            index: DECISIONS_INDEX,
            body: {
                mappings: {
                    properties: {
                        decision_id: { type: "keyword" },
                        type: { type: "keyword" },
                        title: { type: "text" },
                        content: { type: "semantic_text" },
                        service: { type: "keyword" },
                        status: { type: "keyword" },
                        created_at: { type: "date" },
                        updated_at: { type: "date" },
                        author: { type: "keyword" },
                        tags: { type: "keyword" },
                        related_incidents: { type: "keyword" },
                        superseded_by: { type: "keyword" }
                    }
                }
            }
        });

        // 2. Ingest Incidents
        const incidents = generateIncidents();
        console.log(`Ingesting ${incidents.length} incidents...`);
        const incidentBody = incidents.flatMap(doc => [{ index: { _index: INCIDENTS_INDEX } }, doc]);

        const { errors: incidentErrors, items: incidentItems } = await client.bulk({ body: incidentBody });
        if (incidentErrors) console.error("Errors ingesting incidents:", incidentItems.filter(i => i.index && i.index.error));
        else console.log("✅ Incidents ingested.");

        // 3. Ingest Decisions
        const decisions = generateDecisions();
        console.log(`Ingesting ${decisions.length} decisions...`);
        const decisionBody = decisions.flatMap(doc => [{ index: { _index: DECISIONS_INDEX } }, doc]);

        const { errors: decisionErrors, items: decisionItems } = await client.bulk({ body: decisionBody });
        if (decisionErrors) console.error("Errors ingesting decisions:", decisionItems.filter(d => d.index && d.index.error));
        else console.log("✅ Decisions ingested.");

        console.log("🎉 Seeding complete! Waiting for semantic inference (background)...");

    } catch (error) {
        console.error("❌ Seeding failed:", error);
    }
}

seed();
