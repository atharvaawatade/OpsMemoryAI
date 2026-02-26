#!/usr/bin/env python3
"""
OpsMemory — Master Test Runner
================================
Runs all test suites (unit, integration, flow) in sequence and saves
structured output to testing/logs/ for analysis, UI display, and README.

Usage:
    python3 testing/run_all_tests.py

Output:
    testing/logs/YYYY-MM-DDTHH-MM-SS_results.json   — machine-readable
    testing/logs/YYYY-MM-DDTHH-MM-SS_results.txt    — human-readable
    testing/logs/latest.json                         — always points to latest run
    testing/logs/latest.txt                          — always points to latest run

Environment (optional — enables integration/flow tests):
    ELASTICSEARCH_URL, KIBANA_URL, ELASTIC_API_KEY, AGENT_ID
"""

import sys
import os
import unittest
import json
import time
import io
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT   = Path(__file__).parent.parent
TESTING_DIR = Path(__file__).parent
LOGS_DIR    = TESTING_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# ── Test discovery ─────────────────────────────────────────────────────────────

SUITES = [
    {
        "name": "Unit Tests — extract_signals.py",
        "suite_id": "unit",
        "path": str(TESTING_DIR / "unit"),
        "pattern": "test_extract_signals.py",
    },
    {
        "name": "Integration Tests — Elasticsearch + APIs",
        "suite_id": "integration",
        "path": str(TESTING_DIR / "integration"),
        "pattern": "test_elasticsearch.py",
    },
    {
        "name": "Flow Tests — End-to-End Deployment Gate",
        "suite_id": "flow",
        "path": str(TESTING_DIR / "flow"),
        "pattern": "test_full_flow.py",
    },
]


# ── Custom result collector ────────────────────────────────────────────────────

class DetailedTestResult(unittest.TestResult):
    """Captures per-test pass/fail/skip/error with timing."""

    def __init__(self):
        super().__init__()
        self.test_details = []
        self._start_times = {}

    def startTest(self, test):
        super().startTest(test)
        self._start_times[test.id()] = time.monotonic()

    def _elapsed(self, test):
        start = self._start_times.get(test.id(), time.monotonic())
        return round(time.monotonic() - start, 4)

    def addSuccess(self, test):
        super().addSuccess(test)
        self.test_details.append({
            "id": test.id(),
            "name": test._testMethodName,
            "class": test.__class__.__name__,
            "status": "PASS",
            "elapsed_s": self._elapsed(test),
            "message": "",
        })

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.test_details.append({
            "id": test.id(),
            "name": test._testMethodName,
            "class": test.__class__.__name__,
            "status": "FAIL",
            "elapsed_s": self._elapsed(test),
            "message": self._exc_info_to_str(err, test),
        })

    def addError(self, test, err):
        super().addError(test, err)
        self.test_details.append({
            "id": test.id(),
            "name": test._testMethodName,
            "class": test.__class__.__name__,
            "status": "ERROR",
            "elapsed_s": self._elapsed(test),
            "message": self._exc_info_to_str(err, test),
        })

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.test_details.append({
            "id": test.id(),
            "name": test._testMethodName,
            "class": test.__class__.__name__,
            "status": "SKIP",
            "elapsed_s": self._elapsed(test),
            "message": reason,
        })

    def _exc_info_to_str(self, err, test):
        """Format exception info as a clean string."""
        exc_type, exc_value, exc_tb = err
        return f"{exc_type.__name__}: {exc_value}"


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_suite(suite_config: dict) -> dict:
    """Discover and run one test suite. Returns structured result dict."""
    print(f"\n{'━' * 60}")
    print(f"  {suite_config['name']}")
    print(f"{'━' * 60}")

    loader = unittest.TestLoader()
    try:
        suite = loader.discover(
            start_dir=suite_config["path"],
            pattern=suite_config["pattern"],
            top_level_dir=str(REPO_ROOT),
        )
    except Exception as e:
        print(f"  [ERROR] Failed to discover tests: {e}")
        return {
            "suite_id": suite_config["suite_id"],
            "suite_name": suite_config["name"],
            "status": "ERROR",
            "error": str(e),
            "tests": [],
            "summary": {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0},
            "elapsed_s": 0,
        }

    result = DetailedTestResult()
    t0 = time.monotonic()

    # Stream test output to console too
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2, resultclass=DetailedTestResult)
    # Actually run with our collector
    suite.run(result)

    elapsed = round(time.monotonic() - t0, 3)

    # Print live summary to console
    passed  = len([t for t in result.test_details if t["status"] == "PASS"])
    failed  = len([t for t in result.test_details if t["status"] == "FAIL"])
    errors  = len([t for t in result.test_details if t["status"] == "ERROR"])
    skipped = len([t for t in result.test_details if t["status"] == "SKIP"])
    total   = len(result.test_details)

    for detail in result.test_details:
        icon = {"PASS": "✓", "FAIL": "✗", "ERROR": "!", "SKIP": "↷"}.get(detail["status"], "?")
        msg = f"  {icon} [{detail['status']:5}] {detail['class']}.{detail['name']}"
        if detail["message"] and detail["status"] in ("FAIL", "ERROR", "SKIP"):
            msg += f"\n         → {detail['message'][:120]}"
        print(msg)

    status_icon = "✓ PASS" if failed == 0 and errors == 0 else "✗ FAIL"
    print(f"\n  {status_icon} | {total} tests | {passed} passed | {failed} failed | {errors} errors | {skipped} skipped | {elapsed}s")

    return {
        "suite_id": suite_config["suite_id"],
        "suite_name": suite_config["name"],
        "status": "PASS" if failed == 0 and errors == 0 else "FAIL",
        "tests": result.test_details,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "skipped": skipped,
        },
        "elapsed_s": elapsed,
    }


def build_report(suite_results: list, run_elapsed: float) -> dict:
    """Aggregate all suite results into a top-level report."""
    total   = sum(s["summary"]["total"]   for s in suite_results)
    passed  = sum(s["summary"]["passed"]  for s in suite_results)
    failed  = sum(s["summary"]["failed"]  for s in suite_results)
    errors  = sum(s["summary"]["errors"]  for s in suite_results)
    skipped = sum(s["summary"]["skipped"] for s in suite_results)
    overall = "PASS" if failed == 0 and errors == 0 else "FAIL"

    return {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall,
        "elapsed_s": round(run_elapsed, 3),
        "environment": {
            "python": sys.version.split()[0],
            "elasticsearch_url": os.environ.get("ELASTICSEARCH_URL", "(not set)"),
            "kibana_url": os.environ.get("KIBANA_URL", "(not set)"),
            "agent_id": os.environ.get("AGENT_ID", "opsmemory-enforcer"),
            "next_url": os.environ.get("NEXT_PUBLIC_URL", "http://localhost:3000"),
        },
        "summary": {
            "total":    total,
            "passed":   passed,
            "failed":   failed,
            "errors":   errors,
            "skipped":  skipped,
            "executed": total - skipped,
            # Pass rate = passed / executed (skipped tests don't count against us)
            "pass_rate": f"{(passed / (total - skipped) * 100):.1f}%" if (total - skipped) > 0 else "N/A",
        },
        "suites": suite_results,
    }


def format_text_report(report: dict) -> str:
    """Render the report as a clean human-readable text block."""
    lines = []
    run_at = report["run_at"]
    overall = report["overall_status"]
    s = report["summary"]

    lines.append("=" * 70)
    lines.append("  OpsMemory AI — Test Results")
    lines.append(f"  Run at : {run_at}")
    lines.append(f"  Status : {'✓ ALL PASS' if overall == 'PASS' else '✗ FAILURES DETECTED'}")
    lines.append(f"  Elapsed: {report['elapsed_s']}s")
    lines.append("=" * 70)
    lines.append("")
    lines.append("OVERALL SUMMARY")
    lines.append(f"  Total    : {s['total']}")
    lines.append(f"  Passed   : {s['passed']}")
    lines.append(f"  Failed   : {s['failed']}")
    lines.append(f"  Errors   : {s['errors']}")
    lines.append(f"  Skipped  : {s['skipped']}")
    lines.append(f"  Pass Rate: {s['pass_rate']}")
    lines.append("")

    for suite in report["suites"]:
        ss = suite["summary"]
        icon = "✓" if suite["status"] == "PASS" else "✗"
        lines.append(f"{icon} {suite['suite_name']}")
        lines.append(f"    {ss['total']} tests | {ss['passed']} passed | {ss['failed']} failed | {ss['skipped']} skipped | {suite['elapsed_s']}s")

        fail_or_error = [t for t in suite["tests"] if t["status"] in ("FAIL", "ERROR")]
        if fail_or_error:
            lines.append("    FAILURES:")
            for t in fail_or_error:
                lines.append(f"      ✗ {t['class']}.{t['name']}")
                lines.append(f"        {t['message'][:150]}")

        lines.append("")

    lines.append("=" * 70)
    lines.append("  Generated by OpsMemory AI test suite")
    lines.append("  https://github.com/atharvaawatade/opsmemory")
    lines.append("=" * 70)

    return "\n".join(lines)


def save_logs(report: dict, text: str):
    """Write JSON and text logs with timestamp + latest symlinks."""
    ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

    json_path = LOGS_DIR / f"{ts}_results.json"
    txt_path  = LOGS_DIR / f"{ts}_results.txt"

    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    txt_path.write_text(text, encoding="utf-8")

    # Always overwrite latest.* (not symlinks — for cross-platform compatibility)
    (LOGS_DIR / "latest.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (LOGS_DIR / "latest.txt").write_text(text, encoding="utf-8")

    print(f"\n  Logs saved:")
    print(f"    JSON : {json_path}")
    print(f"    Text : {txt_path}")
    print(f"    Also : {LOGS_DIR}/latest.json + latest.txt")

    return json_path, txt_path


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  OpsMemory AI — Full Test Suite")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 60)

    env_status = []
    for var in ["ELASTICSEARCH_URL", "KIBANA_URL", "ELASTIC_API_KEY"]:
        val = os.environ.get(var, "")
        env_status.append(f"  {var}: {'✓ SET' if val else '✗ not set (integration/flow tests will skip)'}")
    print("\nEnvironment:")
    print("\n".join(env_status))

    t_start = time.monotonic()
    suite_results = []

    for suite_cfg in SUITES:
        result = run_suite(suite_cfg)
        suite_results.append(result)

    run_elapsed = time.monotonic() - t_start
    report = build_report(suite_results, run_elapsed)
    text   = format_text_report(report)

    print("\n" + text)
    save_logs(report, text)

    # Exit code: 0 = all pass, 1 = failures
    failed_total = report["summary"]["failed"] + report["summary"]["errors"]
    return 0 if failed_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
