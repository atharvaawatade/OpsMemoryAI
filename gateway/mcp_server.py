#!/usr/bin/env python3
"""
OpsMemory AI — MCP Action Server
=================================
Exposes create_review_ticket as an MCP tool so Elastic Agent Builder
can call it natively as a Tool Type: MCP.

This is the "action" layer — when the agent decides DENY, it calls
this MCP server which writes the review ticket to ops-actions index.

Usage:
    python3 gateway/mcp_server.py

Then expose publicly with ngrok:
    ngrok http 8000

Configure in Kibana:
    Agent Builder → Tools → Create new tool → Type: MCP
    MCP Server URL: https://<your-ngrok-id>.ngrok-free.app/sse
    Tool ID: create_review_ticket
"""

import os
import time
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv, find_dotenv
from fastmcp import FastMCP
from elasticsearch import Elasticsearch

load_dotenv(find_dotenv())

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MCP] %(message)s")
log = logging.getLogger(__name__)

ES_URL   = os.getenv("ELASTICSEARCH_URL", "")
API_KEY  = os.getenv("ELASTIC_API_KEY", "")
INDEX    = "ops-actions"

mcp = FastMCP(
    name="OpsMemory Actions",
    instructions="Creates review tickets in the ops-actions Elasticsearch index when deployments are blocked or flagged."
)

def get_es_client() -> Elasticsearch:
    return Elasticsearch(ES_URL, api_key=API_KEY)


@mcp.tool()
def create_review_ticket(
    service_name: str,
    verdict: str,
    reason: str,
) -> dict:
    """
    Creates a formal review ticket in the ops-actions index when a deployment
    is DENIED or flagged as NEEDS REVIEW. This tool takes autonomous action —
    it does not just advise, it records the decision and notifies the team.

    Args:
        service_name: The service being deployed (e.g. checkout-service)
        verdict:      DENY or NEEDS_REVIEW
        reason:       One sentence citing the specific ADR or incident ID

    Returns:
        ticket_id, status, and the ops-actions index entry confirmation
    """
    ticket_id = f"REVIEW-{int(time.time()) % 100000}"
    team      = f"{service_name.split('-')[0]}-team"

    doc = {
        "action_type":   "REVIEW_TICKET",
        "ticket_id":     ticket_id,
        "service":       service_name,
        "verdict":       verdict,
        "reason":        reason[:500],
        "assigned_team": team,
        "status":        "OPEN",
        "created_at":    datetime.now(timezone.utc).isoformat(),
        "source":        "opsmemory-mcp-server",
    }

    try:
        client = get_es_client()
        resp   = client.index(index=INDEX, document=doc, refresh=True)
        log.info(f"Ticket created: {ticket_id} for {service_name} ({verdict})")
        return {
            "ticket_id":     ticket_id,
            "status":        "created",
            "index":         INDEX,
            "assigned_team": team,
            "es_result":     resp.get("result", "unknown"),
        }
    except Exception as e:
        log.error(f"Failed to create ticket: {e}")
        # Return graceful failure — agent can still render its verdict
        return {
            "ticket_id": ticket_id,
            "status":    "failed",
            "error":     str(e)[:200],
        }


if __name__ == "__main__":
    log.info("Starting OpsMemory MCP Action Server on port 8000")
    log.info(f"Elasticsearch: {ES_URL[:50]}...")
    log.info("Expose publicly with: cloudflared tunnel --url http://localhost:8000")
    log.info("Kibana MCP connector URL: https://<tunnel>.trycloudflare.com/mcp")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
