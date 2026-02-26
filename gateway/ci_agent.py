#!/usr/bin/env python3
"""
OpsMemory AI — CI/CD Gateway Agent (Agent Builder Client)
=========================================================
Thin client that triggers the OpsMemory Enforcer agent via the
Elastic Agent Builder Converse API.  All intelligence lives in
Agent Builder; this script is just the trigger + trace renderer.

Features:
  • Calls /api/agent_builder/converse with agent_id
  • Parses the structured response (steps → tool calls → verdict)
  • Creates review tickets in ops-actions on DENY (Workflow fallback)
  • Rich CLI trace output matching Agent Builder's internal reasoning
  • Persistent response caching for repeated queries
"""

import os
import sys
import json
import time
import hashlib
import requests
from datetime import datetime, timezone
from colorama import init, Fore, Style
from dotenv import load_dotenv, find_dotenv

# Code signal extractor — reads git diff and surfaces dangerous patterns
try:
    from extract_signals import signals_from_env, format_signals_for_agent
    _SIGNALS_AVAILABLE = True
except ImportError:
    _SIGNALS_AVAILABLE = False

# ── Initialization ──────────────────────────────────────────────
init(autoreset=True)
load_dotenv(find_dotenv())

KIBANA_URL       = os.getenv("KIBANA_URL", "")
AGENT_ID         = os.getenv("AGENT_ID", "")
API_KEY          = os.getenv("ELASTIC_API_KEY", "")
ES_URL           = os.getenv("ELASTICSEARCH_URL", "")

if not all([KIBANA_URL, AGENT_ID, API_KEY]):
    print(f"{Fore.RED}❌ Missing environment variables. Set KIBANA_URL, AGENT_ID, and ELASTIC_API_KEY in .env{Style.RESET_ALL}")
    sys.exit(1)

AGENT_API_URL = f"{KIBANA_URL}/api/agent_builder/converse"
CACHE_FILE    = ".agent_cache.json"

# ── Tool Name Mapping ──────────────────────────────────────────
# Maps Agent Builder internal tool IDs to our custom tool names
# for display purposes. If the agent uses platform.core.search
# we still display the LOGICAL tool name based on reasoning context.
TOOL_DISPLAY_MAP = {
    "incident_memory_search":      ("🔍 incident_memory_search",     "Custom — Index Search",  Fore.GREEN),
    "cascading_pattern_detector":   ("📊 cascading_pattern_detector", "Custom — ES|QL",         Fore.MAGENTA),
    "policy_search":                ("📋 policy_search",              "Custom — Index Search",  Fore.YELLOW),
    "create_review_ticket":         ("📝 create_review_ticket",       "Workflow",               Fore.BLUE),
    "platform.core.search":        ("🔧 platform.core.search",       "Built-in Search",        Fore.WHITE),
}

# ── Cache helpers ───────────────────────────────────────────────
def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass

QUERY_CACHE = load_cache()

def cache_key_for(text):
    return f"v2:{hashlib.md5(text.encode()).hexdigest()}"

# ── Trace logging ───────────────────────────────────────────────
def trace(icon, detail, color=Fore.WHITE):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"{Fore.CYAN}[{ts}]{Style.RESET_ALL} {color}{icon}{Style.RESET_ALL}: {detail}")

def trace_tool(tool_id, params=None, results_summary=None):
    """Pretty-print a tool call with its parameters and results."""
    display_name, tool_type, color = TOOL_DISPLAY_MAP.get(
        tool_id,
        (f"🔧 {tool_id}", "Unknown", Fore.WHITE)
    )
    trace(display_name, f"({tool_type})", color)
    if params:
        for k, v in params.items():
            print(f"           {Fore.WHITE}  {k}: {Fore.CYAN}{v}{Style.RESET_ALL}")
    if results_summary:
        for r in results_summary[:3]:  # Show top 3 results
            print(f"           {Fore.GREEN}  → {r}{Style.RESET_ALL}")

# ── Infer custom tool from reasoning context ────────────────────
def infer_custom_tool(step):
    """
    When Agent Builder wraps our custom Index Search tools behind
    platform.core.search, we infer the ACTUAL custom tool name
    from the reasoning context and the results index.
    """
    reasoning = (step.get("reasoning") or "").lower()
    # Check results for index name
    results = step.get("results", [])
    result_indices = set()
    for r in results:
        idx = (r.get("data", {}).get("reference", {}) or {}).get("index", "")
        if idx:
            result_indices.add(idx)

    # Infer from index in results
    if "ops-decisions" in result_indices:
        return "policy_search"
    if "ops-incidents" in result_indices:
        # Distinguish between semantic search and pattern detection
        if any(kw in reasoning for kw in ["pattern", "cascading", "recurring", "quantif", "statist", "count", "how many", "frequency"]):
            return "cascading_pattern_detector"
        return "incident_memory_search"

    # Infer from reasoning keywords alone
    if any(kw in reasoning for kw in ["policy", "adr", "decision", "architectural", "runbook", "compliance"]):
        return "policy_search"
    if any(kw in reasoning for kw in ["pattern", "cascading", "recurring", "esql", "stats", "aggregate"]):
        return "cascading_pattern_detector"
    if any(kw in reasoning for kw in ["incident", "failure", "past", "history", "similar", "search"]):
        return "incident_memory_search"

    return step.get("tool_id", "unknown")

# ── Create review ticket (Fix #2 — Workflow fallback) ──────────
def create_review_ticket(service, verdict, reasoning, evidence_refs=None):
    """
    Indexes a review ticket document into ops-actions.
    This is the Python-side fallback for the Elastic Workflow tool.
    In production, this would be handled by an Agent Builder Workflow.
    """
    if not ES_URL:
        trace("⚠️  Ticket Skipped", "ELASTICSEARCH_URL not set — cannot index ticket", Fore.YELLOW)
        return None

    ticket_id = f"REVIEW-{int(time.time()) % 100000}"
    doc = {
        "action_type":   "REVIEW_TICKET",
        "ticket_id":     ticket_id,
        "service":       service,
        "verdict":       verdict,
        "reason":        reasoning[:500],  # Truncate for index
        "evidence_refs": evidence_refs or [],
        "assigned_team": f"{service.split('-')[0]}-team",
        "status":        "OPEN",
        "created_at":    datetime.now(timezone.utc).isoformat(),
        "agent_id":      AGENT_ID
    }

    headers = {
        "Authorization": f"ApiKey {API_KEY}",
        "Content-Type":  "application/json"
    }

    try:
        resp = requests.post(
            f"{ES_URL}/ops-actions/_doc",
            headers=headers,
            json=doc,
            timeout=10
        )
        if resp.status_code in (200, 201):
            trace("📋 Review Ticket Created", f"{ticket_id} → ops-actions (Team: {doc['assigned_team']})", Fore.BLUE)
            return ticket_id
        else:
            trace("⚠️  Ticket Index Error", f"Status {resp.status_code}: {resp.text[:200]}", Fore.YELLOW)
            return None
    except Exception as e:
        trace("⚠️  Ticket Index Error", str(e)[:200], Fore.YELLOW)
        return None

# ── Main analysis call ──────────────────────────────────────────
def analyze_deployment(deploy_message):
    ck = cache_key_for(deploy_message)
    if ck in QUERY_CACHE:
        trace("⚡ Cache Hit", "Serving from cache (< 1 ms)", Fore.GREEN)
        return QUERY_CACHE[ck]

    headers = {
        "Authorization": f"ApiKey {API_KEY}",
        "Content-Type":  "application/json",
        "kbn-xsrf":      "true"
    }
    payload = {"agent_id": AGENT_ID, "input": deploy_message}

    try:
        trace("📡 Calling Agent Builder", f"POST {AGENT_API_URL}  agent={AGENT_ID}", Fore.YELLOW)
        t0 = time.time()
        resp = requests.post(AGENT_API_URL, headers=headers, json=payload, timeout=120)
        duration = time.time() - t0

        if resp.status_code != 200:
            trace("❌ API Error", f"HTTP {resp.status_code}: {resp.text[:300]}", Fore.RED)
            return {"verdict": "DENY", "reasoning": f"API Error {resp.status_code}",
                    "tool_calls": [], "duration": duration, "steps": []}

        data = resp.json()

        # ── Extract reasoning steps ─────────────────────────────
        steps = data.get("steps", [])
        parsed_tools = []
        reasoning_steps = []

        for s in steps:
            stype = s.get("type")
            if stype == "reasoning":
                reasoning_steps.append(s.get("reasoning", ""))
                trace("🧠 Reasoning", s.get("reasoning", "")[:120], Fore.MAGENTA)

            elif stype == "tool_call":
                raw_tool = s.get("tool_id", "unknown")
                params   = s.get("params", {})

                # Infer custom tool name if agent used platform.core.search
                if raw_tool == "platform.core.search":
                    inferred = infer_custom_tool(s)
                else:
                    inferred = raw_tool

                # Extract result highlights
                result_highlights = []
                for r in s.get("results", []):
                    highlights = r.get("data", {}).get("content", {}).get("highlights", [])
                    result_highlights.extend(highlights[:2])

                trace_tool(inferred, params, result_highlights)

                parsed_tools.append({
                    "name":       inferred,
                    "raw_tool":   raw_tool,
                    "params":     params,
                    "highlights": result_highlights[:3]
                })

        # ── Extract final reply ─────────────────────────────────
        reply = ""
        if isinstance(data.get("response"), dict):
            reply = data["response"].get("message", "")
        if not reply:
            reply = data.get("text") or data.get("content") or ""
        if not reply:
            reply = "No response text."

        # ── Parse verdict ───────────────────────────────────────
        ru = reply.upper()
        if "VERDICT: DENY" in ru or "VERDICT:DENY" in ru:
            verdict = "DENY"
        elif "VERDICT: APPROVE" in ru or "VERDICT:APPROVE" in ru:
            verdict = "APPROVE"
        elif "NEEDS REVIEW" in ru:
            verdict = "NEEDS_REVIEW"
        elif "DENY" in ru:
            verdict = "DENY"
        elif "APPROVE" in ru:
            verdict = "APPROVE"
        else:
            verdict = "DENY" if "RISK" in ru else "APPROVE"

        # ── Build result ────────────────────────────────────────
        result = {
            "verdict":    verdict,
            "reasoning":  reply,
            "tool_calls": parsed_tools,
            "duration":   duration,
            "steps":      len(steps),
            "model":      data.get("model_usage", {}).get("model", "unknown")
        }

        QUERY_CACHE[ck] = result
        save_cache(QUERY_CACHE)
        return result

    except requests.exceptions.Timeout:
        trace("❌ Timeout", "Agent Builder did not respond within 120 s", Fore.RED)
        return {"verdict": "DENY", "reasoning": "Timeout", "tool_calls": [], "duration": 120, "steps": 0}
    except Exception as e:
        trace("❌ Connection Error", str(e)[:200], Fore.RED)
        return {"verdict": "DENY", "reasoning": f"Connection Failed: {e}",
                "tool_calls": [], "duration": 0, "steps": 0}


# ── CLI entry point ─────────────────────────────────────────────
def main():
    # Parse arguments
    if len(sys.argv) > 3:
        service, version, changes = sys.argv[1], sys.argv[2], " ".join(sys.argv[3:])
    elif len(sys.argv) > 1:
        service, version, changes = sys.argv[1], "latest", " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "unspecified"
    else:
        service  = "checkout-service"
        version  = "3.0.0"
        changes  = "Increased retry_count to 50 to fix connection issues"

    deploy_msg = f"Deployment Request for {service} ({version}). Change: {changes}"

    # ── Code signal extraction (from git diff via env var) ──────
    signals_text = ""
    signals_count = 0
    if _SIGNALS_AVAILABLE:
        signals, diff_available = signals_from_env()
        signals_text = format_signals_for_agent(signals, diff_available)
        signals_count = len(signals)
        if signals_text:
            deploy_msg += signals_text

    # ── Header ──────────────────────────────────────────────────
    print()
    print(f"{Fore.CYAN}{Style.BRIGHT}🤖 OPSMEMORY ENFORCER — AGENT BUILDER TRACE{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'━' * 55}{Style.RESET_ALL}")
    trace("📥 Deployment Received", f"{service} v{version}")
    trace("📝 Change Description", changes)
    if _SIGNALS_AVAILABLE:
        if signals_count > 0:
            trace("⚠️  Code Signals", f"{signals_count} dangerous pattern(s) extracted from diff", Fore.YELLOW)
        else:
            trace("🔬 Code Signals", "Diff analyzed — no dangerous patterns found", Fore.GREEN)
    print(f"{Fore.CYAN}{'─' * 55}{Style.RESET_ALL}")

    # ── Call Agent ──────────────────────────────────────────────
    result = analyze_deployment(deploy_msg)

    # ── Verdict ─────────────────────────────────────────────────
    print(f"{Fore.CYAN}{'─' * 55}{Style.RESET_ALL}")
    verdict = result["verdict"]

    if verdict == "DENY":
        trace("⛔ VERDICT", "DENY — DEPLOYMENT BLOCKED", Fore.RED)
    elif verdict == "APPROVE":
        trace("✅ VERDICT", "APPROVE — DEPLOYMENT CLEARED", Fore.GREEN)
    else:
        trace("⚠️  VERDICT", f"{verdict} — MANUAL REVIEW REQUIRED", Fore.YELLOW)

    # ── Workflow fallback: create ticket on DENY ────────────────
    ticket_id = None
    if verdict in ("DENY", "NEEDS_REVIEW"):
        evidence = [t["name"] for t in result.get("tool_calls", [])]
        ticket_id = create_review_ticket(service, verdict, result["reasoning"], evidence)

    # ── Final output ────────────────────────────────────────────
    print(f"{Fore.CYAN}{'━' * 55}{Style.RESET_ALL}")
    if verdict == "DENY":
        print(f"\n  {Fore.RED}{Style.BRIGHT}🚫 DEPLOYMENT BLOCKED{Style.RESET_ALL}")
    elif verdict == "APPROVE":
        print(f"\n  {Fore.GREEN}{Style.BRIGHT}✅ DEPLOYMENT APPROVED{Style.RESET_ALL}")
    else:
        print(f"\n  {Fore.YELLOW}{Style.BRIGHT}⚠️  NEEDS REVIEW{Style.RESET_ALL}")

    if ticket_id:
        print(f"  {Fore.BLUE}📋 Review ticket {ticket_id} created in ops-actions{Style.RESET_ALL}")

    # ── Reasoning ───────────────────────────────────────────────
    print(f"\n{Style.BRIGHT}📝 AGENT REASONING{Style.RESET_ALL}")
    # Clean up escaped newlines
    clean_reasoning = result["reasoning"].replace("\\n", "\n")
    for line in clean_reasoning.split("\n")[:25]:  # Cap at 25 lines
        print(f"   {line}")

    # ── Tool chain summary ──────────────────────────────────────
    if result.get("tool_calls"):
        print(f"\n{Style.BRIGHT}🔧 TOOL CHAIN{Style.RESET_ALL}")
        chain = " → ".join(t["name"] for t in result["tool_calls"])
        print(f"   {chain}")
        if ticket_id:
            print(f"   → create_review_ticket (Workflow Fallback)")

    # ── Metrics ─────────────────────────────────────────────────
    print(f"\n{Style.BRIGHT}⚡ PERFORMANCE{Style.RESET_ALL}")
    print(f"   • Latency:    {result.get('duration', 0)*1000:.0f} ms")
    print(f"   • Agent Steps: {result.get('steps', '?')}")
    print(f"   • Model:       {result.get('model', '?')}")
    print(f"   • Tools Used:  {len(result.get('tool_calls', []))}")
    print(f"{Fore.CYAN}{'━' * 55}{Style.RESET_ALL}")

    sys.exit(1 if verdict == "DENY" else 0)


if __name__ == "__main__":
    main()
