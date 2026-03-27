"""
MemoryAgent — AgentCore Memory MCP Server

Exposes 4 tools via FastMCP / streamable-HTTP:
  - memory_store      : write a conversation event to short-term memory
  - memory_search     : semantic search across long-term memory records
  - memory_context    : retrieve last-k short-term turns for a session
  - memory_list       : list all long-term records for an actor

Actor IDs are user-defined. Convention:
  operator            - Operator's personal preferences and context
  agent-main          - Main agent decisions and lessons
  agent-infra         - Infrastructure specialist memories
  agent-code          - Code specialist memories

Environment variables:
  MEMORY_ID           - AgentCore Memory store ID (required)
  AWS_REGION          - AWS region (default: us-east-1)
  SEMANTIC_STRATEGY   - Semantic strategy ID (auto-discovered if not set)
  PREFS_STRATEGY      - Preferences strategy ID (auto-discovered if not set)
"""

import json
import os
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from mcp.server.fastmcp import FastMCP

# ── Config ────────────────────────────────────────────────────────────
MEMORY_ID = os.environ["MEMORY_ID"]
REGION = os.environ.get("AWS_REGION", "us-east-1")

SEMANTIC_STRATEGY_ID = os.environ.get("SEMANTIC_STRATEGY", "")
PREFS_STRATEGY_ID = os.environ.get("PREFS_STRATEGY", "")

SEMANTIC_NS_TEMPLATE = "/strategies/{strategy}/actors/{actor}/"
TOP_K_DEFAULT = 5

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("memory-agent")

# ── AWS clients ───────────────────────────────────────────────────────
agentcore = boto3.client("bedrock-agentcore", region_name=REGION)

# ── FastMCP server ────────────────────────────────────────────────────
mcp = FastMCP(name="MemoryAgent", host="0.0.0.0", stateless_http=True)


# ── Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def memory_store(
    actor_id: str,
    session_id: str,
    user_message: str,
    assistant_message: str,
) -> str:
    """
    Write a conversation turn to AgentCore Memory (short-term storage).
    The memory service asynchronously extracts facts and preferences
    into long-term memory records (~60 seconds).

    Args:
        actor_id:          Who this memory belongs to (e.g. 'operator', 'agent-main')
        session_id:        Unique session identifier (e.g. 'session-2026-03-26')
        user_message:      The user's message text
        assistant_message: The assistant's reply text

    Returns:
        JSON with eventId on success, or error message.
    """
    try:
        resp = agentcore.create_event(
            memoryId=MEMORY_ID,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(timezone.utc).isoformat(),
            payload=[
                {"conversational": {"role": "USER", "content": {"text": user_message}}},
                {"conversational": {"role": "ASSISTANT", "content": {"text": assistant_message}}},
            ],
        )
        event_id = resp.get("event", {}).get("eventId", "unknown")
        log.info("Stored event %s for actor=%s session=%s", event_id, actor_id, session_id)
        return json.dumps({"status": "stored", "eventId": event_id})
    except ClientError as e:
        log.error("memory_store failed: %s", e)
        return json.dumps({"error": str(e)})


@mcp.tool()
def memory_search(
    actor_id: str,
    query: str,
    top_k: int = TOP_K_DEFAULT,
    strategy: str = "semantic",
) -> str:
    """
    Semantic search across long-term memory records for an actor.
    Use at session startup to inject relevant past context.

    Args:
        actor_id: Who to search memory for (e.g. 'operator', 'agent-main')
        query:    Natural language query
        top_k:    Max results to return (default 5)
        strategy: 'semantic' (facts/decisions) or 'preferences' (user preferences)

    Returns:
        JSON array of {content, score} objects, ranked by relevance.
    """
    strategy_id = SEMANTIC_STRATEGY_ID if strategy == "semantic" else PREFS_STRATEGY_ID
    if not strategy_id:
        return json.dumps({"error": f"Strategy ID not configured for '{strategy}'"})

    namespace = SEMANTIC_NS_TEMPLATE.format(strategy=strategy_id, actor=actor_id)

    try:
        resp = agentcore.retrieve_memory_records(
            memoryId=MEMORY_ID,
            namespace=namespace,
            searchCriteria={"searchQuery": query, "topK": top_k},
        )
        records = resp.get("memoryRecordSummaries", [])
        results = [
            {"content": r.get("content", {}).get("text", ""), "score": r.get("score")}
            for r in records
        ]
        log.info("memory_search actor=%s strategy=%s query=%r -> %d results", actor_id, strategy, query, len(results))
        return json.dumps(results)
    except ClientError as e:
        log.error("memory_search failed: %s", e)
        return json.dumps({"error": str(e)})


@mcp.tool()
def memory_context(
    actor_id: str,
    session_id: str,
    last_k: int = 10,
) -> str:
    """
    Retrieve the last K conversation turns from short-term memory for a session.
    Use to restore immediate session context (e.g. after a container restart).

    Args:
        actor_id:   Actor whose session to retrieve
        session_id: Session identifier to look up
        last_k:     Number of most-recent turns to return (default 10)

    Returns:
        JSON array of {role, text} conversation turns, oldest first.
    """
    try:
        resp = agentcore.list_events(
            memoryId=MEMORY_ID,
            actorId=actor_id,
            sessionId=session_id,
        )
        events = resp.get("events", [])
        turns = []
        for event in sorted(events, key=lambda e: e.get("eventTimestamp", "")):
            for item in event.get("payload", []):
                conv = item.get("conversational", {})
                turns.append({
                    "role": conv.get("role", ""),
                    "text": conv.get("content", {}).get("text", ""),
                })
        result = turns[-last_k:] if len(turns) > last_k else turns
        log.info("memory_context actor=%s session=%s -> %d turns", actor_id, session_id, len(result))
        return json.dumps(result)
    except ClientError as e:
        log.error("memory_context failed: %s", e)
        return json.dumps({"error": str(e)})


@mcp.tool()
def memory_list(
    actor_id: str,
    strategy: str = "semantic",
) -> str:
    """
    List all long-term memory records for an actor.
    Useful for auditing what the agent currently knows about someone.

    Args:
        actor_id: Actor to list records for
        strategy: 'semantic' (facts) or 'preferences' (user preferences)

    Returns:
        JSON array of {id, content} objects.
    """
    strategy_id = SEMANTIC_STRATEGY_ID if strategy == "semantic" else PREFS_STRATEGY_ID
    if not strategy_id:
        return json.dumps({"error": f"Strategy ID not configured for '{strategy}'"})

    namespace = SEMANTIC_NS_TEMPLATE.format(strategy=strategy_id, actor=actor_id)

    try:
        resp = agentcore.list_memory_records(
            memoryId=MEMORY_ID,
            namespace=namespace,
        )
        records = resp.get("memoryRecordSummaries", [])
        results = [
            {"id": r.get("memoryRecordId"), "content": r.get("content", {}).get("text", "")}
            for r in records
        ]
        log.info("memory_list actor=%s strategy=%s -> %d records", actor_id, strategy, len(results))
        return json.dumps(results)
    except ClientError as e:
        log.error("memory_list failed: %s", e)
        return json.dumps({"error": str(e)})


# ── Entrypoint ────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("MemoryAgent starting — memoryId=%s region=%s", MEMORY_ID, REGION)
    mcp.run(transport="streamable-http")
