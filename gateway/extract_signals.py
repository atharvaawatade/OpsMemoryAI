#!/usr/bin/env python3
"""
OpsMemory — Code Signal Extractor
===================================
Analyzes git diffs with lightweight regex to surface dangerous config changes
that developers hide behind vague PR descriptions like "minor fix" or "tuning".

No AI tokens consumed — pure pattern matching.
Called before the agent so the agent gets both the intent (PR description)
AND the actual code reality (signals extracted from diff).
"""

import re
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class Signal:
    signal_type: str   # e.g. RETRY_CONFIG_CHANGE
    severity: str      # HIGH / MEDIUM / LOW
    description: str   # human-readable what was found
    evidence: str      # the actual line from the diff (truncated)


# ── Pattern definitions ───────────────────────────────────────────────────────
# Each pattern has:
#   type            — signal identifier
#   severity        — HIGH / MEDIUM / LOW
#   regex           — applied to the full diff (multiline, ignorecase)
#   describe(m)     — lambda that returns human-readable description
#   threshold_check — optional lambda; if provided and returns False, skip signal

PATTERNS = [
    # Retry count changes — #1 cause of retry storms
    {
        "type": "RETRY_CONFIG_CHANGE",
        "severity": "HIGH",
        "regex": r"^\+.*(?:retry[_\-]?count|max[_\-]?retries|retries)\s*[=:]\s*([0-9]+)",
        "describe": lambda m: f"retry count changed to {m.group(1)} (values > 5 trigger retry storms)",
        "threshold_check": lambda m: int(m.group(1)) > 5,
    },
    # Circuit breaker disabled or commented out
    {
        "type": "CIRCUIT_BREAKER_DISABLED",
        "severity": "HIGH",
        "regex": (
            r"^\+.*(?:#|//|--|rem\s).*(?:circuit.?breaker|CircuitBreaker)"
            r"|^\+.*circuit.?breaker.*(?:enabled|active)\s*[=:]\s*(?:false|False|FALSE|0|no)"
        ),
        "describe": lambda m: "circuit breaker disabled or commented out",
        "threshold_check": None,
    },
    # Connection pool size changes
    {
        "type": "CONNECTION_POOL_CHANGE",
        "severity": "MEDIUM",
        "regex": r"^\+.*(?:pool[_\-]?size|max[_\-]?connections?|connection[_\-]?pool)\s*[=:]\s*([0-9]+)",
        "describe": lambda m: f"connection pool size changed to {m.group(1)}",
        "threshold_check": None,
    },
    # Timeout changes — very low values cause cascades
    {
        "type": "TIMEOUT_CHANGE",
        "severity": "MEDIUM",
        "regex": r"^\+.*(?:connect[_\-]?timeout|read[_\-]?timeout|write[_\-]?timeout|request[_\-]?timeout|timeout)\s*[=:]\s*([0-9]+)",
        "describe": lambda m: f"timeout changed to {m.group(1)} — very low values cause cascading failures",
        "threshold_check": None,
    },
    # Rate limiter removed or set to zero
    {
        "type": "RATE_LIMIT_CHANGE",
        "severity": "HIGH",
        "regex": r"^\+.*(?:rate[_\-]?limit(?:er)?|throttle|requests[_\-]?per[_\-]?second|rps)\s*[=:]\s*([0-9]+|false|disabled|none|null)",
        "describe": lambda m: f"rate limiter changed to '{m.group(1)}'",
        "threshold_check": None,
    },
    # Destructive database operations
    {
        "type": "DESTRUCTIVE_DB_OP",
        "severity": "HIGH",
        "regex": (
            r"^\+.*(?:DROP\s+TABLE|TRUNCATE\s+TABLE?|DELETE\s+FROM\s+\w+\s*;?"
            r"|ALTER\s+TABLE.*DROP\s+COLUMN"
            r"|db\.drop_all\(\)|\.execute\(.*DELETE|migrate.*--fake)"
        ),
        "describe": lambda m: "destructive database operation in diff",
        "threshold_check": None,
    },
    # Error handling removed — bare except/pass
    {
        "type": "ERROR_HANDLING_WEAKENED",
        "severity": "MEDIUM",
        "regex": r"^\+\s*except\s*(?:Exception|BaseException)?\s*:\s*$|^\+\s*except.*:\s*\n\+\s*pass\s*$",
        "describe": lambda m: "bare except/pass — exceptions silently swallowed",
        "threshold_check": None,
    },
    # Hardcoded credentials / secrets
    {
        "type": "HARDCODED_SECRET",
        "severity": "HIGH",
        "regex": r'^\+.*(?:password|secret|api.?key|token)\s*[=:]\s*["\'][^"\']{8,}["\']',
        "describe": lambda m: "potential hardcoded secret or credential detected",
        "threshold_check": None,
    },
    # TLS/SSL verification disabled
    {
        "type": "TLS_VERIFICATION_DISABLED",
        "severity": "HIGH",
        "regex": r"^\+.*(?:verify\s*=\s*False|ssl[_\-]?verify\s*=\s*(?:false|0)|InsecureRequestWarning|REQUESTS_CA_BUNDLE\s*=\s*[\"']\s*[\"'])",
        "describe": lambda m: "TLS/SSL certificate verification disabled",
        "threshold_check": None,
    },
    # Memory / heap size changes
    {
        "type": "MEMORY_CONFIG_CHANGE",
        "severity": "LOW",
        "regex": r"^\+.*(?:heap[_\-]?size|memory[_\-]?limit|max[_\-]?memory|-Xmx|-Xms)\s*[=:\s]+([0-9]+[mMgGkK]?)",
        "describe": lambda m: f"memory limit changed to {m.group(1)}",
        "threshold_check": None,
    },
    # Cache TTL set to 0 or very large values
    {
        "type": "CACHE_CONFIG_CHANGE",
        "severity": "LOW",
        "regex": r"^\+.*(?:cache[_\-]?ttl|ttl|cache[_\-]?expiry|expiration)\s*[=:]\s*([0-9]+)",
        "describe": lambda m: f"cache TTL changed to {m.group(1)} seconds",
        "threshold_check": None,
    },
]


def extract_signals(diff: str) -> List[Signal]:
    """
    Run all patterns against the unified diff text.
    Returns a deduplicated list of Signal objects, HIGH severity first.
    """
    if not diff or not diff.strip():
        return []

    signals: List[Signal] = []
    seen_types: set = set()

    for pattern in PATTERNS:
        regex = re.compile(pattern["regex"], re.MULTILINE | re.IGNORECASE)
        for match in regex.finditer(diff):
            signal_type = pattern["type"]

            # Apply numeric threshold check if defined (e.g. retry count > 5)
            if pattern["threshold_check"] is not None:
                try:
                    if not pattern["threshold_check"](match):
                        continue
                except (IndexError, ValueError):
                    continue

            description = pattern["describe"](match)
            evidence = match.group(0)[:120].replace("\n", " ↵ ").strip()

            # Deduplicate by type — keep first occurrence
            if signal_type not in seen_types:
                seen_types.add(signal_type)
                signals.append(Signal(
                    signal_type=signal_type,
                    severity=pattern["severity"],
                    description=description,
                    evidence=evidence,
                ))

    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    signals.sort(key=lambda s: severity_order.get(s.severity, 9))
    return signals


def format_signals_for_agent(signals: List[Signal], diff_available: bool = True) -> str:
    """
    Formats signals as structured text to append to the deployment message
    so the agent can reason about actual code changes, not just PR descriptions.
    """
    if not diff_available:
        return "\n\nCode Signals: No git diff available (first push — PR description is the only signal)."

    if not signals:
        return "\n\nCode Signals: Diff analyzed — no dangerous config patterns detected in code changes."

    high_count = sum(1 for s in signals if s.severity == "HIGH")

    lines = [
        "",
        "Code Signals Detected (extracted from actual git diff — not PR description):",
    ]
    for s in signals:
        lines.append(f"  [{s.severity}] {s.signal_type}: {s.description}")
        lines.append(f"    Evidence: {s.evidence}")

    lines.append("")
    lines.append(
        f"Summary: {len(signals)} signal(s) found, {high_count} HIGH severity. "
        "These signals reflect what the code ACTUALLY changes — treat them as ground truth "
        "even if the PR description says 'minor fix' or 'tuning'."
    )

    return "\n".join(lines)


def signals_from_env() -> Tuple[List[Signal], bool]:
    """
    Reads diff from OPSMEMORY_DIFF env var or OPSMEMORY_DIFF_FILE path.
    Returns (signals, diff_available).
    """
    diff = os.environ.get("OPSMEMORY_DIFF", "")
    if not diff:
        diff_file = os.environ.get("OPSMEMORY_DIFF_FILE", "")
        if diff_file and os.path.exists(diff_file):
            try:
                with open(diff_file, "r", encoding="utf-8", errors="ignore") as f:
                    diff = f.read()
            except OSError:
                pass

    if not diff or not diff.strip():
        return [], False

    return extract_signals(diff), True


if __name__ == "__main__":
    """
    Standalone test — pipe a git diff or set env vars.

    Usage:
        git diff HEAD~1 HEAD | python3 gateway/extract_signals.py
        OPSMEMORY_DIFF_FILE=/tmp/pr.diff python3 gateway/extract_signals.py

    Test with a synthetic dangerous diff:
        python3 gateway/extract_signals.py --demo
    """
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo_diff = """\
--- a/config/checkout.yaml
+++ b/config/checkout.yaml
@@ -10,7 +10,7 @@
-  retry_count: 3
+  retry_count: 50
-  timeout: 30000
+  timeout: 100
-  circuit_breaker_enabled: true
+  # circuit_breaker_enabled: true
--- a/src/db/migrations.py
+++ b/src/db/migrations.py
@@ -1,3 +1,4 @@
+db.execute("DELETE FROM orders WHERE created_at < '2020-01-01';")
"""
        signals = extract_signals(demo_diff)
        diff_available = True
    elif not sys.stdin.isatty():
        diff = sys.stdin.read()
        signals = extract_signals(diff)
        diff_available = bool(diff.strip())
    else:
        signals, diff_available = signals_from_env()

    print(format_signals_for_agent(signals, diff_available))
    if signals:
        print(f"\nDetailed breakdown ({len(signals)} signals):")
        for s in signals:
            print(f"  {s.severity:6} | {s.signal_type:30} | {s.description}")
