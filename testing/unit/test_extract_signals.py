"""
OpsMemory — Unit Tests: extract_signals.py
==========================================
Tests every signal pattern, boundary condition, edge case,
and output formatter. No network calls — pure logic testing.
"""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "gateway"))
from extract_signals import extract_signals, format_signals_for_agent, Signal


# ── Retry Config ──────────────────────────────────────────────────────────────

class TestRetryConfigSignal(unittest.TestCase):

    def test_detects_retry_count_50(self):
        signals = extract_signals("+  retry_count: 50")
        types = [s.signal_type for s in signals]
        self.assertIn("RETRY_CONFIG_CHANGE", types, "retry_count=50 must trigger signal")

    def test_detects_max_retries_10(self):
        signals = extract_signals("+  max_retries: 10")
        types = [s.signal_type for s in signals]
        self.assertIn("RETRY_CONFIG_CHANGE", types, "max_retries=10 must trigger signal")

    def test_boundary_retry_5_no_signal(self):
        """retry_count=5 is exactly at the threshold — should NOT trigger (> 5 required)"""
        signals = extract_signals("+  retry_count: 5")
        types = [s.signal_type for s in signals]
        self.assertNotIn("RETRY_CONFIG_CHANGE", types, "retry_count=5 is safe — no signal")

    def test_boundary_retry_6_triggers_signal(self):
        """retry_count=6 is above threshold — MUST trigger"""
        signals = extract_signals("+  retry_count: 6")
        types = [s.signal_type for s in signals]
        self.assertIn("RETRY_CONFIG_CHANGE", types, "retry_count=6 must trigger signal")

    def test_boundary_retry_3_no_signal(self):
        signals = extract_signals("+  retry_count: 3")
        types = [s.signal_type for s in signals]
        self.assertNotIn("RETRY_CONFIG_CHANGE", types, "retry_count=3 is safe")

    def test_retry_signal_is_high_severity(self):
        signals = extract_signals("+  retry_count: 50")
        retry = next((s for s in signals if s.signal_type == "RETRY_CONFIG_CHANGE"), None)
        self.assertIsNotNone(retry)
        self.assertEqual(retry.severity, "HIGH")

    def test_detects_retries_key(self):
        signals = extract_signals("+  retries: 20")
        types = [s.signal_type for s in signals]
        self.assertIn("RETRY_CONFIG_CHANGE", types)

    def test_yaml_format_retry(self):
        diff = "--- a/config.yaml\n+++ b/config.yaml\n@@ -1,1 +1,1 @@\n-  retry_count: 3\n+  retry_count: 15"
        signals = extract_signals(diff)
        types = [s.signal_type for s in signals]
        self.assertIn("RETRY_CONFIG_CHANGE", types)


# ── Circuit Breaker ───────────────────────────────────────────────────────────

class TestCircuitBreakerSignal(unittest.TestCase):

    def test_detects_commented_out_circuit_breaker(self):
        signals = extract_signals("+  # circuit_breaker_enabled: true")
        types = [s.signal_type for s in signals]
        self.assertIn("CIRCUIT_BREAKER_DISABLED", types)

    def test_detects_circuit_breaker_false(self):
        signals = extract_signals("+  circuit_breaker_enabled: false")
        types = [s.signal_type for s in signals]
        self.assertIn("CIRCUIT_BREAKER_DISABLED", types)

    def test_circuit_breaker_signal_is_high_severity(self):
        signals = extract_signals("+  # circuit_breaker: true")
        cb = next((s for s in signals if s.signal_type == "CIRCUIT_BREAKER_DISABLED"), None)
        self.assertIsNotNone(cb)
        self.assertEqual(cb.severity, "HIGH")

    def test_active_circuit_breaker_no_signal(self):
        """Enabling circuit breaker should NOT trigger signal"""
        signals = extract_signals("+  circuit_breaker_enabled: true")
        types = [s.signal_type for s in signals]
        self.assertNotIn("CIRCUIT_BREAKER_DISABLED", types)


# ── Destructive DB Operations ─────────────────────────────────────────────────

class TestDestructiveDBSignal(unittest.TestCase):

    def test_detects_drop_table(self):
        signals = extract_signals('+db.execute("DROP TABLE orders")')
        types = [s.signal_type for s in signals]
        self.assertIn("DESTRUCTIVE_DB_OP", types)

    def test_detects_truncate(self):
        signals = extract_signals("+TRUNCATE TABLE users;")
        types = [s.signal_type for s in signals]
        self.assertIn("DESTRUCTIVE_DB_OP", types)

    def test_detects_delete_from(self):
        signals = extract_signals("+DELETE FROM orders WHERE created_at < '2020-01-01';")
        types = [s.signal_type for s in signals]
        self.assertIn("DESTRUCTIVE_DB_OP", types)

    def test_detects_drop_all(self):
        signals = extract_signals("+db.drop_all()")
        types = [s.signal_type for s in signals]
        self.assertIn("DESTRUCTIVE_DB_OP", types)

    def test_destructive_db_is_high_severity(self):
        signals = extract_signals("+DROP TABLE payments;")
        db = next((s for s in signals if s.signal_type == "DESTRUCTIVE_DB_OP"), None)
        self.assertIsNotNone(db)
        self.assertEqual(db.severity, "HIGH")


# ── Hardcoded Secrets ─────────────────────────────────────────────────────────

class TestHardcodedSecretSignal(unittest.TestCase):

    def test_detects_api_key_literal(self):
        signals = extract_signals('+api_key = "FAKE-1234567890abcdef"')
        types = [s.signal_type for s in signals]
        self.assertIn("HARDCODED_SECRET", types)

    def test_detects_password_literal(self):
        signals = extract_signals('+password = "supersecret123"')
        types = [s.signal_type for s in signals]
        self.assertIn("HARDCODED_SECRET", types)

    def test_detects_token_literal(self):
        signals = extract_signals('+token = "FAKE_abcdefghijklmnopqrstuvwxyz"')
        types = [s.signal_type for s in signals]
        self.assertIn("HARDCODED_SECRET", types)

    def test_short_value_no_signal(self):
        """Values shorter than 8 chars should not trigger — likely a placeholder"""
        signals = extract_signals('+api_key = "abc"')
        types = [s.signal_type for s in signals]
        self.assertNotIn("HARDCODED_SECRET", types)

    def test_hardcoded_secret_is_high_severity(self):
        signals = extract_signals('+secret = "my-super-secret-value"')
        sec = next((s for s in signals if s.signal_type == "HARDCODED_SECRET"), None)
        self.assertIsNotNone(sec)
        self.assertEqual(sec.severity, "HIGH")


# ── TLS Verification ──────────────────────────────────────────────────────────

class TestTLSSignal(unittest.TestCase):

    def test_detects_verify_false(self):
        signals = extract_signals("+requests.get(url, verify=False)")
        types = [s.signal_type for s in signals]
        self.assertIn("TLS_VERIFICATION_DISABLED", types)

    def test_detects_ssl_verify_false(self):
        signals = extract_signals("+ssl_verify = False")
        types = [s.signal_type for s in signals]
        self.assertIn("TLS_VERIFICATION_DISABLED", types)

    def test_tls_signal_is_high_severity(self):
        signals = extract_signals("+requests.get(url, verify=False)")
        tls = next((s for s in signals if s.signal_type == "TLS_VERIFICATION_DISABLED"), None)
        self.assertIsNotNone(tls)
        self.assertEqual(tls.severity, "HIGH")


# ── Timeout Changes ───────────────────────────────────────────────────────────

class TestTimeoutSignal(unittest.TestCase):

    def test_detects_read_timeout(self):
        signals = extract_signals("+  read_timeout: 100")
        types = [s.signal_type for s in signals]
        self.assertIn("TIMEOUT_CHANGE", types)

    def test_detects_connect_timeout(self):
        signals = extract_signals("+  connect_timeout: 50")
        types = [s.signal_type for s in signals]
        self.assertIn("TIMEOUT_CHANGE", types)

    def test_timeout_signal_is_medium_severity(self):
        signals = extract_signals("+  timeout: 200")
        t = next((s for s in signals if s.signal_type == "TIMEOUT_CHANGE"), None)
        self.assertIsNotNone(t)
        self.assertEqual(t.severity, "MEDIUM")


# ── Connection Pool ───────────────────────────────────────────────────────────

class TestConnectionPoolSignal(unittest.TestCase):

    def test_detects_pool_size(self):
        signals = extract_signals("+  pool_size: 200")
        types = [s.signal_type for s in signals]
        self.assertIn("CONNECTION_POOL_CHANGE", types)

    def test_detects_max_connections(self):
        signals = extract_signals("+  max_connections: 500")
        types = [s.signal_type for s in signals]
        self.assertIn("CONNECTION_POOL_CHANGE", types)

    def test_connection_pool_is_medium_severity(self):
        signals = extract_signals("+  pool_size: 100")
        p = next((s for s in signals if s.signal_type == "CONNECTION_POOL_CHANGE"), None)
        self.assertIsNotNone(p)
        self.assertEqual(p.severity, "MEDIUM")


# ── Multi-signal diff (the "intent gap" scenario) ────────────────────────────

class TestMultiSignalDiff(unittest.TestCase):

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

    def test_detects_all_four_signals(self):
        signals = extract_signals(self.DANGEROUS_DIFF)
        types = {s.signal_type for s in signals}
        self.assertIn("RETRY_CONFIG_CHANGE", types)
        self.assertIn("CIRCUIT_BREAKER_DISABLED", types)
        self.assertIn("TIMEOUT_CHANGE", types)
        self.assertIn("DESTRUCTIVE_DB_OP", types)

    def test_high_severity_sorted_first(self):
        signals = extract_signals(self.DANGEROUS_DIFF)
        self.assertTrue(len(signals) >= 2)
        # All HIGH severity signals must come before MEDIUM/LOW
        severities = [s.severity for s in signals]
        high_indices = [i for i, s in enumerate(severities) if s == "HIGH"]
        medium_indices = [i for i, s in enumerate(severities) if s == "MEDIUM"]
        if high_indices and medium_indices:
            self.assertLess(max(high_indices), min(medium_indices),
                            "HIGH severity signals must appear before MEDIUM")

    def test_no_duplicate_signal_types(self):
        signals = extract_signals(self.DANGEROUS_DIFF)
        types = [s.signal_type for s in signals]
        self.assertEqual(len(types), len(set(types)), "Signal types must be deduplicated")

    def test_evidence_captured(self):
        signals = extract_signals(self.DANGEROUS_DIFF)
        for s in signals:
            self.assertTrue(len(s.evidence) > 0, f"{s.signal_type} must have evidence")


# ── Edge Cases ────────────────────────────────────────────────────────────────

class TestEdgeCases(unittest.TestCase):

    def test_empty_diff_returns_empty(self):
        self.assertEqual(extract_signals(""), [])

    def test_whitespace_diff_returns_empty(self):
        self.assertEqual(extract_signals("   \n\n  "), [])

    def test_removed_lines_not_flagged(self):
        """Lines starting with - (removals) should NOT trigger signals"""
        signals = extract_signals("-  retry_count: 50")
        types = [s.signal_type for s in signals]
        self.assertNotIn("RETRY_CONFIG_CHANGE", types,
                         "Removing retry_count=50 is SAFE — should not trigger")

    def test_safe_diff_returns_empty(self):
        safe_diff = """\
--- a/app.py
+++ b/app.py
@@ -1,1 +1,1 @@
-logging.basicConfig(level=logging.DEBUG)
+logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
"""
        signals = extract_signals(safe_diff)
        self.assertEqual(len(signals), 0, "Logging format change should be safe")

    def test_signal_evidence_truncated_to_120_chars(self):
        long_line = "+  retry_count: 50  # " + "x" * 200
        signals = extract_signals(long_line)
        for s in signals:
            self.assertLessEqual(len(s.evidence), 120)


# ── Format Output ─────────────────────────────────────────────────────────────

class TestFormatOutput(unittest.TestCase):

    def test_format_no_diff_available(self):
        text = format_signals_for_agent([], diff_available=False)
        self.assertIn("No git diff available", text)

    def test_format_empty_signals_with_diff(self):
        text = format_signals_for_agent([], diff_available=True)
        self.assertIn("no dangerous", text.lower())

    def test_format_includes_severity(self):
        signals = extract_signals("+  retry_count: 50")
        text = format_signals_for_agent(signals, diff_available=True)
        self.assertIn("[HIGH]", text)
        self.assertIn("RETRY_CONFIG_CHANGE", text)

    def test_format_includes_summary_count(self):
        signals = extract_signals("+  retry_count: 50\n+  # circuit_breaker: true")
        text = format_signals_for_agent(signals, diff_available=True)
        self.assertIn("signal", text.lower())

    def test_format_includes_ground_truth_warning(self):
        signals = extract_signals("+  retry_count: 50")
        text = format_signals_for_agent(signals, diff_available=True)
        self.assertIn("ground truth", text.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
