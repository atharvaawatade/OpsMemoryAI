#!/usr/bin/env python3
"""
OpsMemory — First-Run Elastic Seeder
======================================
Idempotent setup script that creates the 3 required indices and
loads starter ADRs + incident patterns.

Runs automatically on first GitHub Action execution (auto_seed: true).
Skip by setting auto_seed: false once you have your own data indexed.

Usage:
    ELASTICSEARCH_URL=https://... ELASTIC_API_KEY=... python3 scripts/seed_elastic.py
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from elasticsearch import Elasticsearch, BadRequestError, NotFoundError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SEED] %(message)s")
log = logging.getLogger(__name__)

ES_URL  = os.getenv("ELASTICSEARCH_URL", "")
API_KEY = os.getenv("ELASTIC_API_KEY", "")

DECISIONS_INDEX = "ops-decisions"
INCIDENTS_INDEX = "ops-incidents"
ACTIONS_INDEX   = "ops-actions"

SEED_MARKER_ID  = "opsmemory-seed-v1"   # doc ID used to detect previous seeding


# ── Index mappings ────────────────────────────────────────────────────────────

DECISIONS_MAPPING = {
    "mappings": {
        "properties": {
            "adr_id":     {"type": "keyword"},
            "title":      {"type": "text"},
            "status":     {"type": "keyword"},
            "content":    {"type": "text"},
            "tags":       {"type": "keyword"},
            "created_at": {"type": "date"},
        }
    }
}

# ELSER semantic_text mapping — falls back to standard text if ELSER unavailable
INCIDENTS_MAPPING_ELSER = {
    "mappings": {
        "properties": {
            "incident_id":   {"type": "keyword"},
            "title":         {"type": "text"},
            "description":   {
                "type": "semantic_text",
                "inference_id": ".elser-2-elasticsearch"
            },
            "root_cause":    {
                "type": "semantic_text",
                "inference_id": ".elser-2-elasticsearch"
            },
            "service":       {"type": "keyword"},
            "severity":      {"type": "keyword"},
            "severity_num":  {"type": "integer"},
            "resolution":    {"type": "text"},
            "tags":          {"type": "keyword"},
            "created_at":    {"type": "date"},
            "duration_minutes": {"type": "integer"},
        }
    }
}

INCIDENTS_MAPPING_FALLBACK = {
    "mappings": {
        "properties": {
            "incident_id":   {"type": "keyword"},
            "title":         {"type": "text"},
            "description":   {"type": "text"},
            "root_cause":    {"type": "text"},
            "service":       {"type": "keyword"},
            "severity":      {"type": "keyword"},
            "severity_num":  {"type": "integer"},
            "resolution":    {"type": "text"},
            "tags":          {"type": "keyword"},
            "created_at":    {"type": "date"},
            "duration_minutes": {"type": "integer"},
        }
    }
}

ACTIONS_MAPPING = {
    "mappings": {
        "properties": {
            "action_type":   {"type": "keyword"},
            "ticket_id":     {"type": "keyword"},
            "service":       {"type": "keyword"},
            "verdict":       {"type": "keyword"},
            "reason":        {"type": "text"},
            "assigned_team": {"type": "keyword"},
            "status":        {"type": "keyword"},
            "created_at":    {"type": "date"},
            "source":        {"type": "keyword"},
        }
    }
}


# ── Seed data ─────────────────────────────────────────────────────────────────

def days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


STARTER_ADRS = [
    {
        "adr_id": "ADR-0001",
        "title": "Standardize Retry Policies Across All Services",
        "status": "ACCEPTED",
        "content": (
            "RULING: All synchronous RPC calls MUST limit retries to a maximum of 3 attempts "
            "with exponential backoff (base 500ms, max 30s). Retry counts above 5 are PROHIBITED. "
            "Rationale: INC-0001 demonstrated that retry_count=10 caused 10x traffic amplification "
            "during a transient network blip, saturating the database connection pool and triggering "
            "a 60-minute SEV-1. Circuit breakers MUST be implemented on all retry-enabled call paths."
        ),
        "tags": ["retry", "resilience", "rpc"],
        "created_at": days_ago(400),
    },
    {
        "adr_id": "ADR-0002",
        "title": "Circuit Breakers Mandatory for All External Dependencies",
        "status": "ACCEPTED",
        "content": (
            "RULING: Every service-to-service call and external API integration MUST implement "
            "a circuit breaker (open threshold: 5 failures in 10s, half-open timeout: 30s). "
            "Disabling or commenting out circuit breaker configuration is PROHIBITED without "
            "a formal change request and sign-off from the Platform team. "
            "Rationale: INC-0002 showed cascade failure across 4 services after circuit breaker "
            "was removed during 'cleanup'. Recovery took 2 hours."
        ),
        "tags": ["circuit-breaker", "resilience", "cascade"],
        "created_at": days_ago(380),
    },
    {
        "adr_id": "ADR-0003",
        "title": "Database Schema Migrations Require Staged Rollout",
        "status": "ACCEPTED",
        "content": (
            "RULING: All schema-altering migrations (ALTER TABLE, DROP COLUMN, DROP TABLE, "
            "TRUNCATE) MUST go through staged rollout: apply on staging → 48hr soak → "
            "production. Destructive migrations (DROP, TRUNCATE, DELETE without WHERE) require "
            "explicit DBA approval. Zero-downtime migration patterns (expand/contract) are required "
            "for columns used in active queries. "
            "Rationale: INC-0005 — developer ran DROP TABLE on prod during 'quick cleanup', "
            "3 hours of data loss, $180K estimated impact."
        ),
        "tags": ["database", "migration", "schema"],
        "created_at": days_ago(360),
    },
    {
        "adr_id": "ADR-0004",
        "title": "No Hardcoded Secrets or Credentials in Source Code",
        "status": "ACCEPTED",
        "content": (
            "RULING: API keys, passwords, tokens, database URIs, and any form of credential "
            "MUST NOT be hardcoded in source files, config files committed to version control, "
            "or GitHub Actions workflow files. All secrets must be injected via environment "
            "variables from a secrets manager (AWS Secrets Manager, HashiCorp Vault, GitHub Secrets). "
            "Violations trigger automatic DENY and security review. "
            "Rationale: INC-0008 — hardcoded AWS key exposed in public repo, credential "
            "harvested within 4 minutes, $22K in unauthorized compute charges."
        ),
        "tags": ["security", "secrets", "credentials"],
        "created_at": days_ago(340),
    },
    {
        "adr_id": "ADR-0005",
        "title": "TLS Certificate Verification Must Never Be Disabled",
        "status": "ACCEPTED",
        "content": (
            "RULING: TLS/SSL certificate verification (verify=True, ssl_verify=True) MUST be "
            "enabled in all service-to-service and external API communications. "
            "verify=False, InsecureRequestWarning suppression, and REQUESTS_CA_BUNDLE='' are "
            "PROHIBITED in production code. "
            "Rationale: Disabling TLS verification opens man-in-the-middle attack surface. "
            "Detected in 3 production services during security audit Q3-2024."
        ),
        "tags": ["security", "tls", "ssl"],
        "created_at": days_ago(320),
    },
    {
        "adr_id": "ADR-0006",
        "title": "Connection Pool Sizes Must Be Calculated, Not Guessed",
        "status": "ACCEPTED",
        "content": (
            "RULING: Database and HTTP connection pool sizes must be set based on the formula: "
            "pool_size = (core_count * 2) + effective_spindle_count. Maximum pool size is capped "
            "at 100 per instance. Pool sizes above 50 require Platform team review. "
            "Increasing pool size to compensate for slow queries is PROHIBITED — fix the query. "
            "Rationale: INC-0003 — pool_size=200 exhausted PostgreSQL max_connections, "
            "cascaded to 6 dependent services, 45-minute outage."
        ),
        "tags": ["database", "connection-pool", "performance"],
        "created_at": days_ago(300),
    },
    {
        "adr_id": "ADR-0007",
        "title": "Timeouts Required on All Outbound Calls",
        "status": "ACCEPTED",
        "content": (
            "RULING: All outbound HTTP, gRPC, and database calls MUST have explicit timeouts. "
            "connect_timeout: 5s max, read_timeout: 30s max, write_timeout: 30s max. "
            "No infinite timeouts (timeout=0 or timeout=None) in production code. "
            "Very low timeouts (< 200ms for read) require justification in the PR. "
            "Rationale: INC-0006 — missing timeout on payment API call caused thread pool "
            "exhaustion when downstream was slow, 90-minute incident."
        ),
        "tags": ["timeout", "resilience", "performance"],
        "created_at": days_ago(280),
    },
    {
        "adr_id": "ADR-0008",
        "title": "Rate Limiters Must Not Be Removed Without Traffic Analysis",
        "status": "ACCEPTED",
        "content": (
            "RULING: Rate limiters on public APIs and internal high-traffic endpoints may not "
            "be removed, disabled, or have their limits increased by more than 2x without: "
            "(1) traffic analysis showing current limits are incorrect, "
            "(2) load test results at the new limit, (3) Platform team approval. "
            "Rationale: INC-0009 — rate limiter removed from webhook endpoint, "
            "bot traffic caused 400% CPU spike, 25-minute degradation."
        ),
        "tags": ["rate-limiting", "traffic", "api"],
        "created_at": days_ago(260),
    },
]


STARTER_INCIDENTS = [
    # ── Retry storm cluster ───────────────────────────────────────────────────
    {
        "incident_id": "INC-0001",
        "title": "Retry storm caused database CPU saturation and 60-minute outage",
        "description": (
            "Transient network blip triggered massive retry amplification. The service had "
            "retry_count=10 with no circuit breaker, causing 10x traffic amplification instantly. "
            "Database connection pool exhausted within 30 seconds. All downstream services timed out."
        ),
        "service": "checkout-service",
        "severity": "SEV-1", "severity_num": 1,
        "root_cause": "Retry amplification: retry_count=10, no circuit breaker, no exponential backoff",
        "resolution": "Reduced retry_count to 3, enabled circuit breaker, added exponential backoff",
        "tags": ["retry-storm", "database", "cascade"],
        "created_at": days_ago(120), "duration_minutes": 60,
    },
    {
        "incident_id": "INC-0011",
        "title": "Transient DB connection errors from aggressive retry configuration",
        "description": "Spike in DB connection errors correlating with retry pressure during peak load. retry_count had been increased to 8 during 'stability improvement' PR.",
        "service": "checkout-service",
        "severity": "SEV-3", "severity_num": 3,
        "root_cause": "retry_count=8 causing connection pool exhaustion under load",
        "resolution": "Reverted retry_count to 3",
        "tags": ["retry", "database", "connection-pool"],
        "created_at": days_ago(30), "duration_minutes": 15,
    },
    {
        "incident_id": "INC-0012",
        "title": "Cascading timeout failures after retry count increase",
        "description": "After increasing max_retries from 3 to 7 for 'better reliability', peak traffic caused amplification that cascaded to inventory service. Developer described change as 'minor tuning'.",
        "service": "payment-gateway",
        "severity": "SEV-2", "severity_num": 2,
        "root_cause": "max_retries=7 under high load caused cascade to upstream services",
        "resolution": "Emergency rollback, retry count reduced to 3",
        "tags": ["retry-storm", "cascade", "payment"],
        "created_at": days_ago(60), "duration_minutes": 35,
    },
    # ── Circuit breaker removal ───────────────────────────────────────────────
    {
        "incident_id": "INC-0002",
        "title": "Cascade failure after circuit breaker removed during refactor",
        "description": (
            "Developer removed circuit breaker during 'cleanup refactor', describing it as "
            "'dead code'. Downstream service degradation was not contained and cascaded to "
            "4 dependent services. Recovery took 2 hours as each service had to be restarted."
        ),
        "service": "auth-service",
        "severity": "SEV-1", "severity_num": 1,
        "root_cause": "Circuit breaker removed, cascade propagated to 4 dependent services",
        "resolution": "Restored circuit breaker pattern, added mandatory circuit breaker test",
        "tags": ["circuit-breaker", "cascade", "auth"],
        "created_at": days_ago(90), "duration_minutes": 120,
    },
    {
        "incident_id": "INC-0013",
        "title": "Payment service unavailability after circuit breaker commented out",
        "description": "Circuit breaker configuration was commented out with note 'temporarily disabled for debugging'. Was never re-enabled. 3 weeks later a downstream timeout caused full payment service failure.",
        "service": "payment-gateway",
        "severity": "SEV-1", "severity_num": 1,
        "root_cause": "circuit_breaker_enabled: false (commented out 3 weeks prior)",
        "resolution": "Re-enabled circuit breaker, added automated test to verify it remains active",
        "tags": ["circuit-breaker", "payment", "timeout"],
        "created_at": days_ago(45), "duration_minutes": 90,
    },
    # ── Connection pool exhaustion ────────────────────────────────────────────
    {
        "incident_id": "INC-0003",
        "title": "PostgreSQL max_connections exhausted, 6 services down for 45 minutes",
        "description": (
            "DBA alert: max_connections hit. Root cause: developer increased pool_size from 20 "
            "to 200 per instance to 'fix slow queries'. With 8 instances, this sent 1600 "
            "connections to a DB configured for 100 max. All 6 dependent services lost DB access."
        ),
        "service": "inventory-api",
        "severity": "SEV-1", "severity_num": 1,
        "root_cause": "pool_size=200 × 8 instances = 1600 connections > PostgreSQL max_connections=100",
        "resolution": "Emergency pool_size rollback to 10, query optimization, PgBouncer added",
        "tags": ["connection-pool", "database", "postgresql"],
        "created_at": days_ago(180), "duration_minutes": 45,
    },
    # ── Destructive DB operation ──────────────────────────────────────────────
    {
        "incident_id": "INC-0005",
        "title": "Accidental DROP TABLE on production orders table",
        "description": (
            "Developer ran a migration script locally then accidentally against production. "
            "Script contained DROP TABLE orders. Table had 3 years of order history. "
            "No foreign key constraints prevented the drop. Backup restore took 3 hours."
        ),
        "service": "order-service",
        "severity": "SEV-1", "severity_num": 1,
        "root_cause": "Destructive migration (DROP TABLE) run against production without staged rollout",
        "resolution": "Restored from backup (3hr data loss), migration review process implemented",
        "tags": ["database", "migration", "data-loss", "drop-table"],
        "created_at": days_ago(200), "duration_minutes": 180,
    },
    # ── Secrets exposure ──────────────────────────────────────────────────────
    {
        "incident_id": "INC-0008",
        "title": "Hardcoded AWS API key exposed in public repository",
        "description": (
            "AWS API key hardcoded in config.py was committed to a public GitHub repository. "
            "Automated credential harvesting bots found it within 4 minutes. "
            "$22,000 in unauthorized EC2 compute charges accrued before key was rotated."
        ),
        "service": "data-pipeline",
        "severity": "SEV-1", "severity_num": 1,
        "root_cause": "Hardcoded AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in source code",
        "resolution": "Key rotated, moved to AWS Secrets Manager, pre-commit hook added for secret scanning",
        "tags": ["security", "secrets", "aws", "credential-exposure"],
        "created_at": days_ago(150), "duration_minutes": 240,
    },
    # ── Timeout cascade ───────────────────────────────────────────────────────
    {
        "incident_id": "INC-0006",
        "title": "Thread pool exhaustion from missing timeout on payment API call",
        "description": (
            "Payment service made outbound calls to processor API with no timeout configured. "
            "When processor became slow (P99 > 60s), threads accumulated waiting for response. "
            "Thread pool exhausted after 20 minutes, service became unresponsive."
        ),
        "service": "payment-gateway",
        "severity": "SEV-1", "severity_num": 1,
        "root_cause": "Missing read_timeout on external API call caused thread pool exhaustion",
        "resolution": "Added 30s timeout, implemented async processing with circuit breaker",
        "tags": ["timeout", "thread-pool", "payment"],
        "created_at": days_ago(240), "duration_minutes": 90,
    },
    {
        "incident_id": "INC-0014",
        "title": "Timeout set to 50ms caused 80% error rate under normal load",
        "description": "Timeout was changed from 5000ms to 50ms in 'optimization PR'. Under normal load, 80% of requests failed. Developer had tested with empty DB, not production data volumes.",
        "service": "search-service",
        "severity": "SEV-2", "severity_num": 2,
        "root_cause": "read_timeout=50ms too low for production data volumes",
        "resolution": "Reverted timeout to 5000ms",
        "tags": ["timeout", "configuration", "performance"],
        "created_at": days_ago(75), "duration_minutes": 40,
    },
    # ── Rate limiter removal ──────────────────────────────────────────────────
    {
        "incident_id": "INC-0009",
        "title": "Bot traffic caused 400% CPU spike after rate limiter removed",
        "description": (
            "Rate limiter on webhook endpoint was removed to 'reduce latency'. "
            "Bot traffic exploited the unprotected endpoint, causing 400% CPU spike. "
            "25 minutes of degraded service before IP blocking was implemented."
        ),
        "service": "notification-service",
        "severity": "SEV-2", "severity_num": 2,
        "root_cause": "Rate limiter removed from public endpoint, unprotected from bot traffic",
        "resolution": "Restored rate limiter with 1000 req/min limit, added IP-based blocking",
        "tags": ["rate-limiting", "bot-traffic", "cpu"],
        "created_at": days_ago(100), "duration_minutes": 25,
    },
    # ── Memory / config ───────────────────────────────────────────────────────
    {
        "incident_id": "INC-0010",
        "title": "OOM crash after JVM heap reduced to 256MB in cost-cutting PR",
        "description": "Heap size reduced from 2GB to 256MB in a cost optimization PR described as 'tuning JVM flags'. Service OOM-crashed under normal load within 10 minutes of deployment.",
        "service": "reporting-service",
        "severity": "SEV-2", "severity_num": 2,
        "root_cause": "JVM heap -Xmx reduced to 256MB, insufficient for production load",
        "resolution": "Reverted heap to 2GB, added heap sizing documentation",
        "tags": ["memory", "jvm", "oom"],
        "created_at": days_ago(50), "duration_minutes": 30,
    },
]


# ── Core seeding logic ────────────────────────────────────────────────────────

def get_client():
    if not ES_URL or not API_KEY:
        log.error("ELASTICSEARCH_URL and ELASTIC_API_KEY must be set")
        sys.exit(1)
    return Elasticsearch(ES_URL, api_key=API_KEY)


def already_seeded(client, index):
    """Check if OpsMemory seed marker exists — avoids double-seeding."""
    try:
        client.get(index=index, id=SEED_MARKER_ID)
        return True
    except NotFoundError:
        return False


def ensure_index(client, name, mapping):
    """Create index if it doesn't exist."""
    if not client.indices.exists(index=name):
        client.indices.create(index=name, body=mapping)
        log.info(f"Created index: {name}")
    else:
        log.info(f"Index already exists: {name} (skipping create)")


def seed_decisions(client):
    if already_seeded(client, DECISIONS_INDEX):
        log.info(f"ops-decisions already seeded — skipping")
        return

    ensure_index(client, DECISIONS_INDEX, DECISIONS_MAPPING)

    for adr in STARTER_ADRS:
        client.index(index=DECISIONS_INDEX, id=adr["adr_id"], document=adr)

    # Seed marker
    client.index(index=DECISIONS_INDEX, id=SEED_MARKER_ID, document={
        "adr_id": SEED_MARKER_ID, "title": "OpsMemory Seed Marker",
        "status": "SYSTEM", "content": "Auto-seeded by OpsMemory GitHub Action",
        "created_at": datetime.now(timezone.utc).isoformat(), "tags": ["system"]
    })
    client.indices.refresh(index=DECISIONS_INDEX)
    log.info(f"Seeded {len(STARTER_ADRS)} ADRs into ops-decisions")


def seed_incidents(client):
    if already_seeded(client, INCIDENTS_INDEX):
        log.info(f"ops-incidents already seeded — skipping")
        return

    # Try ELSER mapping first, fall back to standard text
    try:
        ensure_index(client, INCIDENTS_INDEX, INCIDENTS_MAPPING_ELSER)
        log.info("Using ELSER semantic_text mappings for ops-incidents")
    except (BadRequestError, Exception) as e:
        if "unknown field" in str(e).lower() or "semantic_text" in str(e).lower() or client.indices.exists(index=INCIDENTS_INDEX):
            if not client.indices.exists(index=INCIDENTS_INDEX):
                ensure_index(client, INCIDENTS_INDEX, INCIDENTS_MAPPING_FALLBACK)
                log.warning(f"ELSER not available — using standard text mappings (semantic search degraded): {e}")
        else:
            ensure_index(client, INCIDENTS_INDEX, INCIDENTS_MAPPING_FALLBACK)
            log.warning(f"ELSER not available — using standard text mappings: {e}")

    for inc in STARTER_INCIDENTS:
        client.index(index=INCIDENTS_INDEX, id=inc["incident_id"], document=inc)

    client.index(index=INCIDENTS_INDEX, id=SEED_MARKER_ID, document={
        "incident_id": SEED_MARKER_ID, "title": "OpsMemory Seed Marker",
        "description": "Auto-seeded by OpsMemory GitHub Action", "service": "system",
        "severity": "SYSTEM", "severity_num": 0, "root_cause": "system",
        "resolution": "system", "tags": ["system"],
        "created_at": datetime.now(timezone.utc).isoformat(), "duration_minutes": 0,
    })
    client.indices.refresh(index=INCIDENTS_INDEX)
    log.info(f"Seeded {len(STARTER_INCIDENTS)} incidents into ops-incidents")


def seed_actions(client):
    """Create ops-actions index (empty — gets populated by real ticket creation)."""
    ensure_index(client, ACTIONS_INDEX, ACTIONS_MAPPING)
    log.info("ops-actions index ready")


def main():
    log.info("OpsMemory first-run seeder starting...")
    client = get_client()

    # Verify connection
    try:
        info = client.info()
        log.info(f"Connected to Elasticsearch: {info['version']['number']}")
    except Exception as e:
        log.error(f"Cannot connect to Elasticsearch: {e}")
        sys.exit(1)

    seed_decisions(client)
    seed_incidents(client)
    seed_actions(client)

    log.info("Seed complete. OpsMemory indices ready:")
    log.info(f"  ops-decisions  → {len(STARTER_ADRS)} ADRs")
    log.info(f"  ops-incidents  → {len(STARTER_INCIDENTS)} incidents")
    log.info(f"  ops-actions    → ready for tickets")
    log.info("First deployment check will now have organizational memory to enforce.")


if __name__ == "__main__":
    main()
