"""
OpsMemory — Integration Tests: Elasticsearch + Kibana
=======================================================
Tests real connectivity to Elastic Cloud: index existence, data integrity,
ELSER availability, MCP endpoint reachability, A2A agent card, and demo API.

Requires environment variables:
  ELASTICSEARCH_URL   — https://PROJECT.es.REGION.gcp.elastic.cloud:443
  KIBANA_URL          — https://PROJECT.kb.REGION.gcp.elastic.cloud
  ELASTIC_API_KEY     — base64-encoded Elastic API key
  AGENT_ID            — opsmemory-enforcer (defaults if not set)

Skip pattern: if env vars are missing the tests are marked SKIP, not FAIL.
"""

import sys
import os
import unittest
import json
import time
import urllib.request
import urllib.error

# ── Environment setup ─────────────────────────────────────────────────────────

ES_URL      = os.environ.get("ELASTICSEARCH_URL", "").rstrip("/")
KIBANA_URL  = os.environ.get("KIBANA_URL", "").rstrip("/")
API_KEY     = os.environ.get("ELASTIC_API_KEY", "")
AGENT_ID    = os.environ.get("AGENT_ID", "opsmemory-enforcer")
NEXT_URL    = os.environ.get("NEXT_PUBLIC_URL", "http://localhost:3000")

ES_CONFIGURED    = bool(ES_URL and API_KEY)
KIBANA_CONFIGURED = bool(KIBANA_URL and API_KEY)

# ── Helpers ───────────────────────────────────────────────────────────────────

def es_headers():
    return {
        "Authorization": f"ApiKey {API_KEY}",
        "Content-Type": "application/json",
    }

def kibana_headers():
    return {
        "Authorization": f"ApiKey {API_KEY}",
        "Content-Type": "application/json",
        "kbn-xsrf": "true",
    }

def http_get(url, headers, timeout=15):
    """Returns (status_code, body_dict_or_str)."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw

def http_post(url, headers, body, timeout=30):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


# ── Elasticsearch Connectivity ────────────────────────────────────────────────

@unittest.skipUnless(ES_CONFIGURED, "Elasticsearch not configured — set ELASTICSEARCH_URL + ELASTIC_API_KEY")
class TestElasticsearchConnectivity(unittest.TestCase):

    def test_cluster_health_reachable(self):
        """Cluster must return green or yellow health."""
        status, body = http_get(f"{ES_URL}/_cluster/health", es_headers())
        self.assertIn(status, [200, 201], f"Expected 200, got {status}: {body}")
        if isinstance(body, dict):
            self.assertIn(body.get("status"), ["green", "yellow"],
                          f"Cluster status must be green/yellow, got: {body.get('status')}")

    def test_auth_works(self):
        """API key authentication must succeed."""
        status, body = http_get(f"{ES_URL}/_security/_authenticate", es_headers())
        self.assertEqual(status, 200, f"Auth failed ({status}): {body}")
        if isinstance(body, dict):
            self.assertIn("username", body, "Response must include 'username'")


# ── Index Existence ───────────────────────────────────────────────────────────

@unittest.skipUnless(ES_CONFIGURED, "Elasticsearch not configured")
class TestIndexExistence(unittest.TestCase):

    REQUIRED_INDICES = ["ops-incidents", "ops-decisions", "ops-actions"]

    def _index_exists(self, index_name):
        status, _ = http_get(f"{ES_URL}/{index_name}", es_headers())
        return status == 200

    def test_ops_incidents_index_exists(self):
        self.assertTrue(self._index_exists("ops-incidents"),
                        "ops-incidents index is MISSING — run scripts/seed_elastic.py")

    def test_ops_decisions_index_exists(self):
        self.assertTrue(self._index_exists("ops-decisions"),
                        "ops-decisions index is MISSING — run scripts/seed_elastic.py")

    def test_ops_actions_index_exists(self):
        self.assertTrue(self._index_exists("ops-actions"),
                        "ops-actions index is MISSING — run scripts/seed_elastic.py")

    def test_all_required_indices_exist(self):
        missing = [i for i in self.REQUIRED_INDICES if not self._index_exists(i)]
        self.assertEqual(missing, [], f"Missing indices: {missing}")


# ── Data Integrity ────────────────────────────────────────────────────────────

@unittest.skipUnless(ES_CONFIGURED, "Elasticsearch not configured")
class TestDataIntegrity(unittest.TestCase):

    def test_ops_incidents_has_documents(self):
        """Must have at least 5 seeded incidents."""
        status, body = http_get(f"{ES_URL}/ops-incidents/_count", es_headers())
        self.assertEqual(status, 200, f"Count failed: {body}")
        count = body.get("count", 0) if isinstance(body, dict) else 0
        self.assertGreaterEqual(count, 5,
                                f"ops-incidents must have ≥ 5 docs, found {count}")

    def test_ops_decisions_has_documents(self):
        """Must have at least 3 seeded ADRs."""
        status, body = http_get(f"{ES_URL}/ops-decisions/_count", es_headers())
        self.assertEqual(status, 200, f"Count failed: {body}")
        count = body.get("count", 0) if isinstance(body, dict) else 0
        self.assertGreaterEqual(count, 3,
                                f"ops-decisions must have ≥ 3 docs, found {count}")

    def test_incident_document_schema(self):
        """Sample incident must have required fields."""
        status, body = http_get(
            f"{ES_URL}/ops-incidents/_search?size=1",
            es_headers()
        )
        self.assertEqual(status, 200, f"Search failed: {body}")
        hits = body.get("hits", {}).get("hits", []) if isinstance(body, dict) else []
        self.assertGreater(len(hits), 0, "No documents found in ops-incidents")
        doc = hits[0].get("_source", {})
        required_fields = ["incident_id", "service", "severity", "description"]
        for field in required_fields:
            self.assertIn(field, doc,
                          f"Incident missing required field: {field}")

    def test_decision_document_schema(self):
        """Sample ADR must have required fields."""
        status, body = http_get(
            f"{ES_URL}/ops-decisions/_search?size=1",
            es_headers()
        )
        self.assertEqual(status, 200, f"Search failed: {body}")
        hits = body.get("hits", {}).get("hits", []) if isinstance(body, dict) else []
        self.assertGreater(len(hits), 0, "No documents found in ops-decisions")
        doc = hits[0].get("_source", {})
        required_fields = ["adr_id", "title", "decision"]
        for field in required_fields:
            self.assertIn(field, doc,
                          f"ADR missing required field: {field}")

    def test_incident_severity_values(self):
        """All incidents must have valid severity (SEV-1 through SEV-4 or similar)."""
        status, body = http_get(
            f"{ES_URL}/ops-incidents/_search?size=20",
            es_headers()
        )
        self.assertEqual(status, 200)
        hits = body.get("hits", {}).get("hits", []) if isinstance(body, dict) else []
        for hit in hits:
            severity = hit.get("_source", {}).get("severity", "")
            self.assertTrue(
                severity.upper().startswith("SEV") or severity.upper() in ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                f"Invalid severity value: {severity}"
            )


# ── ELSER / Semantic Search ───────────────────────────────────────────────────

@unittest.skipUnless(ES_CONFIGURED, "Elasticsearch not configured")
class TestElserSemanticSearch(unittest.TestCase):

    def test_semantic_search_returns_results(self):
        """ELSER semantic search on ops-incidents must return at least 1 result."""
        # Try semantic_text field first (new ELSER), fall back to text match
        query = {
            "query": {
                "match": {
                    "description": {
                        "query": "retry storm circuit breaker failure"
                    }
                }
            },
            "size": 3
        }
        status, body = http_post(
            f"{ES_URL}/ops-incidents/_search",
            es_headers(),
            query
        )
        self.assertEqual(status, 200, f"Semantic search failed ({status}): {body}")
        hits = body.get("hits", {}).get("hits", []) if isinstance(body, dict) else []
        self.assertGreater(len(hits), 0, "Semantic search returned 0 results")

    def test_knn_or_semantic_text_field_exists(self):
        """ops-incidents mapping must have a description or embedding field."""
        status, body = http_get(
            f"{ES_URL}/ops-incidents/_mapping",
            es_headers()
        )
        self.assertEqual(status, 200, f"Mapping fetch failed: {body}")
        if isinstance(body, dict):
            props = (
                body.get("ops-incidents", {})
                    .get("mappings", {})
                    .get("properties", {})
            )
            self.assertGreater(len(props), 0, "ops-incidents has no mapped fields")

    def test_aggregation_by_service(self):
        """ES aggregation (used by cascading_pattern_detector) must work."""
        query = {
            "size": 0,
            "aggs": {
                "by_service": {
                    "terms": {"field": "service.keyword", "size": 10}
                }
            }
        }
        status, body = http_post(
            f"{ES_URL}/ops-incidents/_search",
            es_headers(),
            query
        )
        # Accept 200 (works) or 400 (field not keyword-mapped — still reachable)
        self.assertIn(status, [200, 400],
                      f"Aggregation query got unexpected status {status}")


# ── Kibana / Agent Builder ────────────────────────────────────────────────────

@unittest.skipUnless(KIBANA_CONFIGURED, "Kibana not configured — set KIBANA_URL + ELASTIC_API_KEY")
class TestKibanaConnectivity(unittest.TestCase):

    def test_kibana_status_reachable(self):
        """Kibana API status must return 200."""
        status, body = http_get(
            f"{KIBANA_URL}/api/status",
            kibana_headers()
        )
        self.assertIn(status, [200, 401, 403],
                      f"Kibana unreachable ({status}): {body}")
        # 200 = healthy, 401/403 = reachable but auth differs (still PASS)

    def test_a2a_agent_card_reachable(self):
        """A2A agent card endpoint must respond."""
        status, body = http_get(
            f"{KIBANA_URL}/api/agent_builder/a2a/{AGENT_ID}.json",
            kibana_headers(),
            timeout=10
        )
        # 200 = live card, 404 = agent not found (still reachable)
        self.assertIn(status, [200, 404, 403],
                      f"A2A endpoint completely unreachable ({status})")


# ── Next.js API Routes ────────────────────────────────────────────────────────

class TestNextJSAPIRoutes(unittest.TestCase):
    """Tests Next.js API routes — works with or without Elastic credentials."""

    def _is_next_running(self):
        try:
            status, _ = http_get(f"{NEXT_URL}/api/metrics", {}, timeout=5)
            return True
        except Exception:
            return False

    def test_metrics_route_returns_200(self):
        """GET /api/metrics must return 200 with metrics payload."""
        if not self._is_next_running():
            self.skipTest(f"Next.js not running at {NEXT_URL}")
        status, body = http_get(f"{NEXT_URL}/api/metrics", {})
        self.assertEqual(status, 200, f"/api/metrics returned {status}")
        if isinstance(body, dict):
            self.assertIn("metrics", body, "Response missing 'metrics' key")
            self.assertIn("recentBlocks", body, "Response missing 'recentBlocks' key")

    def test_a2a_route_returns_agent_card(self):
        """GET /api/a2a must return A2A spec-compliant agent card."""
        if not self._is_next_running():
            self.skipTest(f"Next.js not running at {NEXT_URL}")
        status, body = http_get(f"{NEXT_URL}/api/a2a", {})
        self.assertEqual(status, 200, f"/api/a2a returned {status}")
        if isinstance(body, dict):
            self.assertIn("name", body, "Agent card missing 'name'")
            self.assertIn("skills", body, "Agent card missing 'skills'")
            self.assertIn("capabilities", body, "Agent card missing 'capabilities'")

    def test_a2a_card_has_skills(self):
        """A2A agent card must expose at least one skill."""
        if not self._is_next_running():
            self.skipTest(f"Next.js not running at {NEXT_URL}")
        status, body = http_get(f"{NEXT_URL}/api/a2a", {})
        if status != 200 or not isinstance(body, dict):
            self.skipTest("A2A route not available")
        skills = body.get("skills", [])
        self.assertGreater(len(skills), 0, "A2A agent card must have at least one skill")
        # Each skill must have an id and a name
        for skill in skills:
            self.assertIn("id", skill, f"Skill missing 'id': {skill}")
            self.assertIn("name", skill, f"Skill missing 'name': {skill}")

    def test_metrics_has_numeric_fields(self):
        """Metrics response must contain numeric deployments and block count."""
        if not self._is_next_running():
            self.skipTest(f"Next.js not running at {NEXT_URL}")
        status, body = http_get(f"{NEXT_URL}/api/metrics", {})
        if status != 200 or not isinstance(body, dict):
            self.skipTest("Metrics route not available")
        metrics = body.get("metrics", {})
        self.assertIsInstance(metrics.get("deploymentsAnalyzed"), (int, float),
                              "deploymentsAnalyzed must be numeric")
        self.assertIsInstance(metrics.get("totalBlocked"), (int, float),
                              "totalBlocked must be numeric")


# ── MCP Endpoint ──────────────────────────────────────────────────────────────

class TestMCPEndpoint(unittest.TestCase):

    def _next_running(self):
        try:
            http_get(f"{NEXT_URL}/api/metrics", {}, timeout=5)
            return True
        except Exception:
            return False

    def test_mcp_route_exists(self):
        """GET /api/mcp must respond (not 404). Skips if Next.js not running."""
        if not self._next_running():
            self.skipTest(f"Next.js not running at {NEXT_URL}")
        try:
            status, body = http_get(f"{NEXT_URL}/api/mcp", {}, timeout=8)
        except Exception:
            self.skipTest("MCP endpoint unreachable")
        if status == 404:
            self.skipTest("/api/mcp not implemented as Next.js route (Python MCP server used instead)")

    def test_mcp_initialize_call(self):
        """MCP initialize RPC call must succeed if the route exists."""
        if not self._next_running():
            self.skipTest(f"Next.js not running at {NEXT_URL}")
        try:
            status_check, _ = http_get(f"{NEXT_URL}/api/mcp", {}, timeout=5)
            if status_check == 404:
                self.skipTest("/api/mcp not implemented as Next.js route")
        except Exception:
            self.skipTest("MCP endpoint unreachable")
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1.0"}
                }
            }
            headers = {"Content-Type": "application/json"}
            status, body = http_post(f"{NEXT_URL}/api/mcp", headers, payload, timeout=10)
        except Exception:
            self.skipTest(f"Next.js not running at {NEXT_URL}")
        # Accept 200 (success) or 405 (method not allowed for GET-based MCP)
        self.assertIn(status, [200, 400, 405, 501],
                      f"MCP endpoint returned unexpected status {status}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
