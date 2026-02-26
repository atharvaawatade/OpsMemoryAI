"""
OpsMemory — Flow Tests: End-to-End Deployment Gate
===================================================
Tests the complete deployment decision pipeline:
  1. Code signal extraction from git diff
  2. Signal → Agent Builder message construction
  3. Agent Builder API call (DENY / APPROVE / NEEDS_REVIEW)
  4. Verdict parsing and structured response
  5. Dangerous diff correctly blocked
  6. Safe diff correctly approved

These tests exercise the full chain. If Agent Builder is unavailable,
CI/network tests are skipped and signal-only tests still run.

Requires (for full flow):
  KIBANA_URL         — https://PROJECT.kb.REGION.gcp.elastic.cloud
  ELASTIC_API_KEY    — base64-encoded Elastic API key
  AGENT_ID           — opsmemory-enforcer
"""

import sys
import os
import json
import time
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "gateway"))
from extract_signals import extract_signals, format_signals_for_agent, Signal

KIBANA_URL  = os.environ.get("KIBANA_URL", "").rstrip("/")
API_KEY     = os.environ.get("ELASTIC_API_KEY", "")
AGENT_ID    = os.environ.get("AGENT_ID", "opsmemory-enforcer")
NEXT_URL    = os.environ.get("NEXT_PUBLIC_URL", "http://localhost:3000")

KIBANA_CONFIGURED = bool(KIBANA_URL and API_KEY)


def http_post(url, headers, body, timeout=90):
    """POST JSON, return (status_code, response_body)."""
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


# ══════════════════════════════════════════════════════════════════════════════
# Scenario payloads
# ══════════════════════════════════════════════════════════════════════════════

DANGEROUS_DIFF = """\
--- a/config/checkout.yaml
+++ b/config/checkout.yaml
@@ -10,7 +10,7 @@
-  retry_count: 3
+  retry_count: 50
-  timeout: 30000
+  timeout: 100
-  circuit_breaker_enabled: true
+  # circuit_breaker_enabled: true
--- a/src/db/cleanup.py
+++ b/src/db/cleanup.py
@@ -1,3 +1,4 @@
+db.execute("DELETE FROM orders WHERE created_at < '2020-01-01';")
"""

SAFE_DIFF = """\
--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
-logging.basicConfig(level=logging.DEBUG)
+logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
--- a/README.md
+++ b/README.md
@@ -5,1 +5,1 @@
-## Installation
+## Quick Start
"""

RETRY_STORM_DIFF = """\
--- a/services/payment/config.yaml
+++ b/services/payment/config.yaml
@@ -3,2 +3,2 @@
-  max_retries: 3
+  max_retries: 25
-  retry_delay_ms: 1000
+  retry_delay_ms: 100
"""

SECRET_LEAK_DIFF = """\
--- a/src/integrations/stripe.py
+++ b/src/integrations/stripe.py
@@ -1,2 +1,3 @@
+api_key = "FAKE_KEY_FOR_TESTING_ONLY_abcdef"
+stripe_secret = "FAKE_SECRET_FOR_TESTING_ONLY_1234"
"""

TLS_DISABLED_DIFF = """\
--- a/src/http_client.py
+++ b/src/http_client.py
@@ -5,1 +5,2 @@
+    session.verify = False
+    ssl_verify = False
"""


# ══════════════════════════════════════════════════════════════════════════════
# Flow 1: Signal extraction from realistic diffs
# ══════════════════════════════════════════════════════════════════════════════

class TestSignalExtractionFlow(unittest.TestCase):
    """Full signal extraction pipeline — no network required."""

    def test_dangerous_diff_extracts_four_signals(self):
        signals = extract_signals(DANGEROUS_DIFF)
        types = {s.signal_type for s in signals}
        self.assertIn("RETRY_CONFIG_CHANGE", types)
        self.assertIn("CIRCUIT_BREAKER_DISABLED", types)
        self.assertIn("TIMEOUT_CHANGE", types)
        self.assertIn("DESTRUCTIVE_DB_OP", types)

    def test_safe_diff_extracts_zero_signals(self):
        signals = extract_signals(SAFE_DIFF)
        self.assertEqual(len(signals), 0,
                         f"Safe diff must produce 0 signals, got: {[s.signal_type for s in signals]}")

    def test_retry_storm_diff_extracts_signal(self):
        signals = extract_signals(RETRY_STORM_DIFF)
        types = {s.signal_type for s in signals}
        self.assertIn("RETRY_CONFIG_CHANGE", types)

    def test_secret_leak_diff_extracts_hardcoded_secret(self):
        signals = extract_signals(SECRET_LEAK_DIFF)
        types = {s.signal_type for s in signals}
        self.assertIn("HARDCODED_SECRET", types)

    def test_tls_disabled_diff_extracts_tls_signal(self):
        signals = extract_signals(TLS_DISABLED_DIFF)
        types = {s.signal_type for s in signals}
        self.assertIn("TLS_VERIFICATION_DISABLED", types)

    def test_signals_deduplicated(self):
        """Two retry lines in same diff must produce ONE RETRY_CONFIG_CHANGE signal."""
        double_retry = "+  retry_count: 50\n+  max_retries: 30\n"
        signals = extract_signals(double_retry)
        retry_signals = [s for s in signals if s.signal_type == "RETRY_CONFIG_CHANGE"]
        self.assertEqual(len(retry_signals), 1,
                         "Duplicate signal types must be deduplicated")

    def test_high_severity_signals_sorted_first(self):
        signals = extract_signals(DANGEROUS_DIFF)
        severities = [s.severity for s in signals]
        first_medium = next((i for i, s in enumerate(severities) if s == "MEDIUM"), len(signals))
        first_low = next((i for i, s in enumerate(severities) if s == "LOW"), len(signals))
        for i, sev in enumerate(severities):
            if sev == "HIGH":
                self.assertLess(i, first_medium,
                                f"HIGH signal at index {i} must come before MEDIUM at {first_medium}")
                self.assertLess(i, first_low,
                                f"HIGH signal at index {i} must come before LOW at {first_low}")

    def test_evidence_preserved_for_all_signals(self):
        signals = extract_signals(DANGEROUS_DIFF)
        for s in signals:
            self.assertTrue(s.evidence.strip(),
                            f"Signal {s.signal_type} has empty evidence")

    def test_evidence_max_120_chars(self):
        long_diff = "+  retry_count: 50  # " + "x" * 300
        signals = extract_signals(long_diff)
        for s in signals:
            self.assertLessEqual(len(s.evidence), 120,
                                 f"Evidence exceeds 120 chars for {s.signal_type}")


# ══════════════════════════════════════════════════════════════════════════════
# Flow 2: Signal → Agent message construction
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentMessageConstruction(unittest.TestCase):
    """Tests that signals are correctly composed into agent messages."""

    def test_dangerous_signals_produce_high_severity_message(self):
        signals = extract_signals(DANGEROUS_DIFF)
        msg = format_signals_for_agent(signals, diff_available=True)
        self.assertIn("[HIGH]", msg)
        self.assertIn("RETRY_CONFIG_CHANGE", msg)
        self.assertIn("CIRCUIT_BREAKER_DISABLED", msg)
        self.assertIn("DESTRUCTIVE_DB_OP", msg)

    def test_message_contains_ground_truth_instruction(self):
        """Agent message must tell the model to treat signals as ground truth."""
        signals = extract_signals(DANGEROUS_DIFF)
        msg = format_signals_for_agent(signals, diff_available=True)
        self.assertIn("ground truth", msg.lower())

    def test_message_contains_signal_count(self):
        signals = extract_signals(DANGEROUS_DIFF)
        msg = format_signals_for_agent(signals, diff_available=True)
        self.assertIn("signal(s)", msg)

    def test_safe_diff_message_indicates_no_danger(self):
        signals = extract_signals(SAFE_DIFF)
        msg = format_signals_for_agent(signals, diff_available=True)
        self.assertIn("no dangerous", msg.lower())

    def test_no_diff_message_indicates_unavailable(self):
        msg = format_signals_for_agent([], diff_available=False)
        self.assertIn("No git diff available", msg)

    def test_full_deployment_message_assembly(self):
        """Simulate what ci_agent.py does: combine PR description + signals."""
        service = "checkout-service"
        pr_desc = "minor tuning of retry config"
        signals = extract_signals(DANGEROUS_DIFF)
        signal_text = format_signals_for_agent(signals, diff_available=True)
        full_msg = f"Deployment Request for {service} (latest). Change: {pr_desc}{signal_text}"

        # Message must contain both intent (PR description) and reality (signals)
        self.assertIn("minor tuning", full_msg)
        # Description uses natural language: "retry count changed to 50"
        self.assertIn("retry count changed to 50", full_msg)
        self.assertIn("circuit breaker disabled", full_msg)
        self.assertIn("ground truth", full_msg)


# ══════════════════════════════════════════════════════════════════════════════
# Flow 3: Next.js demo API (requires running Next.js server)
# ══════════════════════════════════════════════════════════════════════════════

class TestNextJsDemoAPIFlow(unittest.TestCase):
    """End-to-end tests through the Next.js /api/demo route."""

    def _next_running(self):
        try:
            req = urllib.request.Request(f"{NEXT_URL}/api/metrics")
            with urllib.request.urlopen(req, timeout=5):
                return True
        except Exception:
            return False

    def test_demo_route_exists(self):
        """POST /api/demo must not return 404."""
        if not self._next_running():
            self.skipTest(f"Next.js not running at {NEXT_URL}")
        status, body = http_post(
            f"{NEXT_URL}/api/demo",
            {"Content-Type": "application/json"},
            {"service": "test-service", "changes": "Added logging"},
            timeout=15
        )
        self.assertNotEqual(status, 404, "/api/demo endpoint is missing (404)")

    def test_demo_returns_verdict_field(self):
        """Demo response must include a verdict field."""
        if not self._next_running():
            self.skipTest(f"Next.js not running at {NEXT_URL}")
        status, body = http_post(
            f"{NEXT_URL}/api/demo",
            {"Content-Type": "application/json"},
            {"service": "checkout-service", "changes": "Increased retry_count to 50"},
            timeout=120
        )
        if status in [500, 502, 504]:
            # Elastic/Agent Builder not configured — acceptable for offline testing
            self.skipTest(f"Agent Builder not configured on server (status {status})")
        self.assertEqual(status, 200, f"/api/demo returned {status}: {body}")
        if isinstance(body, dict):
            self.assertIn("verdict", body, "Response must include 'verdict'")
            self.assertIn(body.get("verdict"), ["DENY", "APPROVE", "NEEDS_REVIEW", "UNKNOWN"],
                          f"Unexpected verdict: {body.get('verdict')}")

    def test_demo_returns_reasoning(self):
        """Demo response must include reasoning text."""
        if not self._next_running():
            self.skipTest(f"Next.js not running at {NEXT_URL}")
        status, body = http_post(
            f"{NEXT_URL}/api/demo",
            {"Content-Type": "application/json"},
            {"service": "auth-service", "changes": "Updated JWT expiry"},
            timeout=120
        )
        if status in [500, 502, 504]:
            self.skipTest("Agent Builder not available")
        if isinstance(body, dict) and "verdict" in body:
            self.assertIn("reasoning", body, "Response must include 'reasoning'")
            self.assertGreater(len(body.get("reasoning", "")), 10,
                               "Reasoning must be non-empty")

    def test_demo_returns_tools_used(self):
        """Demo response must include tools_used list."""
        if not self._next_running():
            self.skipTest(f"Next.js not running at {NEXT_URL}")
        status, body = http_post(
            f"{NEXT_URL}/api/demo",
            {"Content-Type": "application/json"},
            {"service": "payment-gateway", "changes": "Removed circuit breaker"},
            timeout=120
        )
        if status in [500, 502, 504]:
            self.skipTest("Agent Builder not available")
        if isinstance(body, dict) and "verdict" in body:
            self.assertIn("tools_used", body, "Response must include 'tools_used'")
            self.assertIsInstance(body.get("tools_used"), list,
                                  "tools_used must be a list")


# ══════════════════════════════════════════════════════════════════════════════
# Flow 4: Agent Builder direct call (requires KIBANA_URL + API_KEY)
# ══════════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(KIBANA_CONFIGURED, "Kibana not configured — set KIBANA_URL + ELASTIC_API_KEY")
class TestAgentBuilderDirectFlow(unittest.TestCase):
    """Direct calls to Kibana Agent Builder — requires real credentials."""

    HEADERS = {
        "Content-Type": "application/json",
        "kbn-xsrf": "true",
        "Authorization": f"ApiKey {API_KEY}",
    }

    def _call_agent(self, message: str, timeout=120):
        payload = {
            "agent_id": AGENT_ID,
            "conversation_id": f"flow-test-{int(time.time())}",
            "messages": [{"role": "user", "content": message}],
        }
        return http_post(
            f"{KIBANA_URL}/api/agent_builder/converse",
            self.HEADERS,
            payload,
            timeout=timeout
        )

    def test_agent_builder_reachable(self):
        """Agent Builder converse endpoint must respond."""
        status, body = self._call_agent("Hello — ping test", timeout=30)
        # 200 = success, 4xx = auth/config issue, still reachable
        self.assertIn(status, [200, 400, 401, 403, 404],
                      f"Agent Builder completely unreachable ({status}): {body}")

    def test_dangerous_deployment_gets_deny_or_review(self):
        """
        Dangerous diff (retry_count=50 + circuit breaker disabled) must get
        DENY or NEEDS_REVIEW — never APPROVE.
        """
        signals = extract_signals(DANGEROUS_DIFF)
        signal_text = format_signals_for_agent(signals, diff_available=True)
        message = (
            f"Deployment Request for checkout-service (latest). "
            f"Change: minor config tuning.{signal_text}"
        )
        status, body = self._call_agent(message, timeout=120)
        if status != 200:
            self.skipTest(f"Agent Builder returned {status} — skipping verdict check")

        # Extract response text
        messages = body.get("messages", body.get("conversation", {}).get("messages", []))
        assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
        raw = assistant.get("content", "") if assistant else body.get("output", "")
        upper = raw.upper()

        self.assertNotIn("VERDICT: APPROVE", upper,
                         f"Dangerous deployment (retry=50, CB disabled) must NOT be APPROVED. Response: {raw[:500]}")
        self.assertTrue(
            "VERDICT: DENY" in upper or "VERDICT: NEEDS_REVIEW" in upper or "NEEDS REVIEW" in upper,
            f"Expected DENY or NEEDS_REVIEW. Got response: {raw[:500]}"
        )

    def test_safe_deployment_does_not_get_deny(self):
        """
        Safe diff (logging format change) should not get DENY verdict.
        May be APPROVE or NEEDS_REVIEW.
        """
        signals = extract_signals(SAFE_DIFF)
        signal_text = format_signals_for_agent(signals, diff_available=True)
        message = (
            f"Deployment Request for app-service (latest). "
            f"Change: update logging format for structured output.{signal_text}"
        )
        status, body = self._call_agent(message, timeout=120)
        if status != 200:
            self.skipTest(f"Agent Builder returned {status} — skipping verdict check")

        messages = body.get("messages", body.get("conversation", {}).get("messages", []))
        assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
        raw = assistant.get("content", "") if assistant else body.get("output", "")
        upper = raw.upper()

        # DENY on a logging format change would be a false positive — that's wrong
        self.assertNotIn("VERDICT: DENY", upper,
                         f"Logging format change must not be DENIED. Response: {raw[:500]}")

    def test_hardcoded_secret_triggers_deny(self):
        """Hardcoded API key in diff must trigger DENY or NEEDS_REVIEW."""
        signals = extract_signals(SECRET_LEAK_DIFF)
        signal_text = format_signals_for_agent(signals, diff_available=True)
        message = (
            f"Deployment Request for payment-service (latest). "
            f"Change: add Stripe integration.{signal_text}"
        )
        status, body = self._call_agent(message, timeout=120)
        if status != 200:
            self.skipTest(f"Agent Builder returned {status}")

        messages = body.get("messages", body.get("conversation", {}).get("messages", []))
        assistant = next((m for m in reversed(messages) if m.get("role") == "assistant"), None)
        raw = assistant.get("content", "") if assistant else body.get("output", "")
        upper = raw.upper()

        self.assertNotIn("VERDICT: APPROVE", upper,
                         f"Hardcoded secret must not be APPROVED. Response: {raw[:500]}")


# ══════════════════════════════════════════════════════════════════════════════
# Flow 5: Regression — past incident patterns
# ══════════════════════════════════════════════════════════════════════════════

class TestRegressionScenarios(unittest.TestCase):
    """
    Regression tests using real incident patterns that OpsMemory should recognize.
    No network required — tests the signal layer only.
    """

    INC_0027_DIFF = """\
--- a/services/checkout/config.yaml
+++ b/services/checkout/config.yaml
@@ -5,3 +5,3 @@
-  retry_count: 3
+  retry_count: 50
-  backoff_multiplier: 2.0
+  backoff_multiplier: 1.0
"""

    INC_0038_DIFF = """\
--- a/services/redis/pool.py
+++ b/services/redis/pool.py
@@ -12,2 +12,2 @@
-  max_connections: 10
+  max_connections: 500
-  pool_size: 5
+  pool_size: 200
"""

    def test_inc_0027_retry_storm_detected(self):
        """INC-0027: Retry storm — retry_count=50 must trigger HIGH signal."""
        signals = extract_signals(self.INC_0027_DIFF)
        types = {s.signal_type for s in signals}
        self.assertIn("RETRY_CONFIG_CHANGE", types, "Must catch INC-0027 retry storm pattern")
        retry_sig = next(s for s in signals if s.signal_type == "RETRY_CONFIG_CHANGE")
        self.assertEqual(retry_sig.severity, "HIGH")

    def test_inc_0038_redis_pool_exhaustion_detected(self):
        """INC-0038: Redis pool exhaustion — max_connections=500 must trigger signal."""
        signals = extract_signals(self.INC_0038_DIFF)
        types = {s.signal_type for s in signals}
        self.assertIn("CONNECTION_POOL_CHANGE", types, "Must catch INC-0038 pool exhaustion pattern")

    def test_combined_cascade_risk_scenario(self):
        """
        Combined: high retry + disabled circuit breaker = cascade risk.
        Both signals must be detected for full coverage.
        """
        combined = self.INC_0027_DIFF + """\
--- a/services/checkout/breakers.yaml
+++ b/services/checkout/breakers.yaml
@@ -1,1 +1,1 @@
-  circuit_breaker_enabled: true
+  # circuit_breaker_enabled: true
"""
        signals = extract_signals(combined)
        types = {s.signal_type for s in signals}
        self.assertIn("RETRY_CONFIG_CHANGE", types)
        self.assertIn("CIRCUIT_BREAKER_DISABLED", types)
        high_count = sum(1 for s in signals if s.severity == "HIGH")
        self.assertGreaterEqual(high_count, 2,
                                "Combined cascade scenario must have ≥ 2 HIGH signals")


if __name__ == "__main__":
    unittest.main(verbosity=2)
