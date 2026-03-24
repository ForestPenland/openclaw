# Skill: Memory and Context Management

**Purpose:** How the AWS-enhanced OpenClaw system stores, retrieves, and manages agent memory — building on OpenClaw's native file-based memory system and extending it with AgentCore Memory for cloud-scale semantic search and persistence.

---

## Hybrid Approach: What OpenClaw Does vs. What AWS Adds

OpenClaw has a mature, well-designed memory system. **Do not replace what works.** Extend it at the right seams.

| Layer | OpenClaw Native | AWS Extension | When to Use AWS |
|-------|----------------|---------------|-----------------|
| Long-term memory | `MEMORY.md` (~100 lines) | DynamoDB hot cache + S3 | Always (cloud deploy) |
| Daily logs | `memory/YYYY-MM-DD.md` | S3 per-agent prefix | Always (cloud deploy) |
| Semantic index | SQLite + BM25/vector | AgentCore Memory | Cloud deploy; multi-instance |
| Embedding provider | OpenAI/Gemini/Voyage/Ollama | AgentCore managed | When you want zero-ops embeddings |
| Nightly consolidation | Runs manually or via cron | Lambda + EventBridge | Production; cloud deploy |

**Key rule:** `MEMORY.md` is still the canonical file. It is loaded in full on every session start. AgentCore Memory is the semantic retrieval layer on top of it — for retrieving *specific relevant* memories from a large history, not replacing the MEMORY.md injection.

**Local dev:** Keep SQLite entirely. No AWS needed.
**Cloud deploy:** Add S3 for workspace files, DynamoDB cache for MEMORY.md, AgentCore Memory for semantic search.

---

## AWS Services Used

- **Amazon Bedrock AgentCore Memory** — managed semantic and episodic memory
- **Amazon DynamoDB** — workspace files (SOUL.md, AGENTS.md, MEMORY.md, etc.)
- **Amazon S3** — full-content workspace files and memory snapshots
- **Amazon EventBridge** — nightly memory consolidation scheduling
- **AWS Lambda** — memory consolidation worker

---

## Key APIs and Operations

### AgentCore Memory Client

```python
import boto3

memory_client = boto3.client(
    "bedrock-agentcore-memory",
    region_name="us-east-1"
)

MEMORY_STORE_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789:memory-store/openclaw-primary"
```

### Ingest Conversation Events (Short-Term Memory)

```python
from datetime import datetime, timezone

def store_conversation_turn(
    session_id: str,
    user_message: str,
    agent_response: str
):
    """Store a conversation turn in AgentCore Memory."""
    now = datetime.now(timezone.utc).isoformat()

    memory_client.ingest_conversation_events(
        memoryStoreArn=MEMORY_STORE_ARN,
        sessionId=session_id,
        conversationEvents=[
            {
                "conversationEvent": {
                    "content": [{"text": user_message}],
                    "role": "USER",
                    "timestamp": now
                }
            },
            {
                "conversationEvent": {
                    "content": [{"text": agent_response}],
                    "role": "ASSISTANT",
                    "timestamp": now
                }
            }
        ]
    )
```

### Retrieve Memories (Semantic Search)

```python
def retrieve_relevant_memories(
    query: str,
    top_k: int = 10,
    memory_store_arn: str = None
) -> list:
    """
    Retrieve memories relevant to the current query via semantic search.
    This is the AgentCore equivalent of OpenClaw's MEMORY.md + SQLite lookup.

    Args:
        query: Current user message or task description
        top_k: Number of memories to retrieve

    Returns:
        List of memory record dicts with 'content', 'type', 'timestamp'
    """
    response = memory_client.retrieve_memory_records(
        memoryStoreArn=memory_store_arn or MEMORY_STORE_ARN,
        query=query,
        maxResults=top_k
    )

    records = response.get("memoryRecords", [])
    return [
        {
            "content": r["memoryContent"]["text"],
            "type": r.get("memoryType", "unknown"),
            "created_at": r.get("createdAt", ""),
            "id": r.get("memoryRecordId", "")
        }
        for r in records
    ]
```

### Get/List Memory Records (Direct Access)

```python
# Get a specific memory record
def get_memory_record(record_id: str) -> dict:
    response = memory_client.get_memory_record(
        memoryStoreArn=MEMORY_STORE_ARN,
        memoryRecordId=record_id
    )
    return response.get("memoryRecord", {})

# List all memory records
def list_all_memories(max_results: int = 100) -> list:
    response = memory_client.list_memory_records(
        memoryStoreArn=MEMORY_STORE_ARN,
        maxResults=max_results
    )
    return response.get("memoryRecords", [])

# Delete a specific memory (for privacy or correction)
def delete_memory_record(record_id: str):
    memory_client.delete_memory_record(
        memoryStoreArn=MEMORY_STORE_ARN,
        memoryRecordId=record_id
    )
```

### Creating the Memory Store (One-Time Setup)

```python
def create_memory_store() -> str:
    """Create the AgentCore Memory Store for OpenClaw. Run once during setup."""
    response = memory_client.create_memory_store(
        name="openclaw-primary-memory",
        description="Long-term memory for OpenClaw AWS agents",
        memoryStrategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "facts-and-knowledge",
                    "description": "Extracts facts, preferences, and durable knowledge"
                }
            },
            {
                "episodicMemoryStrategy": {
                    "name": "experiences-and-events",
                    "description": "Captures meaningful past experiences and decisions"
                }
            }
        ]
    )
    return response["memoryStore"]["memoryStoreArn"]
```

---

## Implementation Patterns

### Pattern 1: The OpenClaw Memory Tier System (AWS Version)

OpenClaw uses a tiered markdown file system. The AWS-native equivalent:

```
Tier 1 — ALWAYS LOADED (DynamoDB/S3: MEMORY.md equivalent)
  → Curated ~100 lines of highest-priority facts
  → Loaded at session start, injected into system prompt
  → Updated by memory consolidation job
  → Stored in DynamoDB: {agent_id, "MEMORY"} item

Tier 2 — ON-DEMAND SEMANTIC RETRIEVAL (AgentCore Memory)
  → All conversation history, extracted facts, episodic memories
  → Retrieved with RetrieveMemoryRecords(query=current_message)
  → Top-K relevant memories injected into context
  → Continuously updated by ingest_conversation_events()

Tier 3 — DAILY LOGS (S3)
  → Full session transcripts stored in S3
  → s3://openclaw-artifacts/memories/{agent_id}/{date}/session-{id}.json
  → Source material for consolidation job
```

### Pattern 2: Memory-Augmented Agent Invocation

The full pattern for building a memory-aware agent invocation:

```python
def invoke_memory_aware_agent(
    agent_id: str,
    user_message: str,
    session_id: str
) -> str:
    """Complete memory-aware agent invocation pattern."""

    # Step 1: Load Tier 1 memory (workspace MEMORY.md)
    workspace = load_workspace_files(agent_id)  # From DynamoDB
    tier1_memory = workspace.get("MEMORY", "")

    # Step 2: Retrieve Tier 2 memories (semantic search)
    tier2_memories = retrieve_relevant_memories(
        query=user_message,
        top_k=8
    )

    # Step 3: Format memories for context
    memory_context = format_memory_context(tier1_memory, tier2_memories)

    # Step 4: Build system prompt with memory
    system_prompt = f"""
{workspace.get('SOUL', '')}

{workspace.get('AGENTS', '')}

# Current Memory Context
{memory_context}

# Today's Date
{datetime.now().strftime('%A, %B %d, %Y')}
"""

    # Step 5: Invoke model/agent
    response = invoke_strands_agent(
        system_prompt=system_prompt,
        user_message=user_message,
        session_id=session_id
    )

    # Step 6: Store this turn in memory
    store_conversation_turn(
        session_id=session_id,
        user_message=user_message,
        agent_response=response
    )

    return response


def format_memory_context(tier1: str, tier2_records: list) -> str:
    """Format memories for injection into system prompt."""
    parts = []

    if tier1:
        parts.append(f"## Long-Term Memory (Key Facts)\n{tier1}")

    if tier2_records:
        memory_lines = "\n".join([
            f"- [{r['type']}] {r['content']}"
            for r in tier2_records[:8]
        ])
        parts.append(f"## Relevant Memories\n{memory_lines}")

    return "\n\n".join(parts) if parts else "No specific memories for this context."
```

### Pattern 3: Memory Consolidation (Nightly Job)

Equivalent to OpenClaw's nightly consolidation to prevent memory bloat:

```python
# memory/consolidator.py
import boto3
import json
import os
from datetime import datetime, timezone, timedelta
from strands import Agent
from strands.models.bedrock import BedrockModel

def consolidate_memories(agent_id: str):
    """
    Nightly memory consolidation:
    1. Retrieve all memories from the past 24 hours
    2. Use Claude to identify the most important facts
    3. Update the MEMORY.md (DynamoDB) with a curated summary
    4. Archive the full session logs to S3
    """
    dynamodb = boto3.resource("dynamodb")
    workspace_table = dynamodb.Table("openclaw-workspace")
    s3_client = boto3.client("s3")
    memory_client = boto3.client("bedrock-agentcore-memory")

    # Get all recent memories
    recent_memories = list_all_memories(max_results=200)

    # Get current MEMORY.md
    current_memory_item = workspace_table.get_item(
        Key={"agent_id": agent_id, "file_type": "MEMORY"}
    )
    current_memory = current_memory_item.get("Item", {}).get("content", "")

    # Use Claude to consolidate
    model = BedrockModel(
        model_id="anthropic.claude-haiku-4-5-20251001-v1:0",  # Use Haiku for cost
        region_name="us-east-1"
    )

    consolidation_prompt = f"""
You are consolidating an AI agent's memory. Your goal is to maintain a curated,
~100-line summary of the most important facts for the agent to always know.

CURRENT MEMORY.MD:
{current_memory}

NEW MEMORIES FROM LAST 24 HOURS:
{json.dumps([m['content'] for m in recent_memories[:50]], indent=2)}

Task: Create an updated MEMORY.md that:
1. Keeps all still-relevant facts from current memory
2. Adds the most important new facts from today
3. Removes outdated or superseded facts
4. Stays under 100 lines
5. Prioritizes: deployed services, user preferences, ongoing projects, key decisions

Return ONLY the new MEMORY.md content, no explanation.
"""

    agent = Agent(model=model)
    new_memory = str(agent(consolidation_prompt))

    # Update MEMORY.md in DynamoDB
    workspace_table.put_item(Item={
        "agent_id": agent_id,
        "file_type": "MEMORY",
        "content": new_memory.strip(),
        "version": int(datetime.now().timestamp()),
        "last_consolidated": datetime.now(timezone.utc).isoformat()
    })

    # Archive session logs to S3
    date_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    s3_client.put_object(
        Bucket=os.environ["ARTIFACTS_BUCKET"],
        Key=f"memory-archive/{agent_id}/{date_str}/memories.json",
        Body=json.dumps(recent_memories, default=str)
    )

    print(f"✓ Memory consolidated for agent {agent_id}. Lines: {len(new_memory.splitlines())}")
```

### Pattern 4: Storing Specific Important Facts

For the agent to explicitly remember something (like a deployment or preference):

```python
@tool
def remember_this(fact: str, category: str = "general") -> str:
    """
    Explicitly store an important fact in long-term memory.
    Use this when you've done something important the operator should know about later,
    or when you learn a key preference or fact about the user.

    Categories: deployment, preference, decision, user_info, business_rule
    """
    import uuid
    session_id = f"explicit-memory-{uuid.uuid4().hex[:8]}"

    store_conversation_turn(
        session_id=session_id,
        user_message=f"[MEMORY REQUEST] Remember this {category}: {fact}",
        agent_response=f"[CONFIRMED] Stored in memory: {fact}"
    )

    return f"✓ Stored in memory: {fact}"
```

---

## Workspace Files Management

### Loading Workspace Files from DynamoDB

```python
def load_workspace_files(agent_id: str) -> dict:
    """Load all workspace files for an agent from DynamoDB."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table("openclaw-workspace")

    file_types = ["SOUL", "AGENTS", "TOOLS", "USER", "IDENTITY", "MEMORY", "HEARTBEAT"]
    workspace = {}

    for file_type in file_types:
        response = table.get_item(
            Key={"agent_id": agent_id, "file_type": file_type}
        )
        if "Item" in response:
            workspace[file_type] = response["Item"]["content"]

    return workspace


def update_workspace_file(agent_id: str, file_type: str, new_content: str):
    """Update a specific workspace file (agent can update its own MEMORY.md)."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table("openclaw-workspace")

    # Only allow agent to update MEMORY and HEARTBEAT
    # SOUL, AGENTS, TOOLS, USER, IDENTITY should be human-controlled
    AGENT_MUTABLE = {"MEMORY", "HEARTBEAT"}
    if file_type not in AGENT_MUTABLE:
        raise PermissionError(f"Agent cannot modify {file_type} — human update required")

    table.put_item(Item={
        "agent_id": agent_id,
        "file_type": file_type,
        "content": new_content,
        "version": int(datetime.now().timestamp()),
        "last_updated_by": "agent",
        "last_updated_at": datetime.now(timezone.utc).isoformat()
    })
```

---

## Example Code Snippets

### Building Complete Memory-Enriched System Prompt

```python
def build_complete_system_prompt(agent_id: str, current_task: str) -> str:
    """
    Build the complete system prompt for an agent invocation.
    Combines: workspace files + retrieved memories + context.
    """
    workspace = load_workspace_files(agent_id)
    memories = retrieve_relevant_memories(query=current_task, top_k=8)

    sections = []

    # Identity and personality
    if "IDENTITY" in workspace:
        sections.append(workspace["IDENTITY"])
    if "SOUL" in workspace:
        sections.append(workspace["SOUL"])

    # Operating instructions
    if "AGENTS" in workspace:
        sections.append(workspace["AGENTS"])

    # User context
    if "USER" in workspace:
        sections.append(f"## About Your Operator\n{workspace['USER']}")

    # Curated long-term memory (Tier 1)
    if "MEMORY" in workspace and workspace["MEMORY"].strip():
        sections.append(f"## Long-Term Memory\n{workspace['MEMORY']}")

    # Semantically relevant memories (Tier 2)
    if memories:
        memory_lines = "\n".join([f"- {m['content']}" for m in memories])
        sections.append(f"## Contextually Relevant Memories\n{memory_lines}")

    # Available tools
    if "TOOLS" in workspace:
        sections.append(workspace["TOOLS"])

    # Current context
    sections.append(f"## Current Date & Time\n{datetime.now().strftime('%A, %B %d, %Y at %H:%M UTC')}")

    return "\n\n---\n\n".join(sections)
```

### Memory Health Check

```python
def check_memory_health(agent_id: str) -> dict:
    """Check the health of an agent's memory system."""
    workspace = load_workspace_files(agent_id)
    all_memories = list_all_memories(max_results=1000)

    memory_md = workspace.get("MEMORY", "")
    memory_lines = len(memory_md.splitlines())

    return {
        "total_memory_records": len(all_memories),
        "memory_md_lines": memory_lines,
        "memory_md_healthy": 50 <= memory_lines <= 150,
        "warning": "Memory.md is getting long, consolidation needed" if memory_lines > 120 else None,
        "workspace_files_present": list(workspace.keys())
    }
```

---

## Gotchas & Best Practices

### Memory Consolidation is Critical
Without nightly consolidation, the MEMORY.md equivalent will grow unbounded, causing context window bloat. Schedule the consolidation Lambda to run at 2 AM daily. The Felix use case (Nat Eliason) found this was the #1 operational issue with production OpenClaw deployments.

### Semantic Retrieval Isn't Perfect
AgentCore Memory uses semantic search — it finds conceptually similar memories, not exact matches. For exact lookups (e.g., "what's the ARN of function X?"), use a direct DynamoDB query against a deployments table, not memory retrieval.

### Session IDs Are Critical for Memory Coherence
Use consistent session IDs within a conversation. If session IDs change mid-conversation, memory context will fragment. Pattern: `{channel}:{user_id}:{date}:{sequence}`.

### Don't Store Secrets in Memory
Never store API keys, tokens, or passwords in AgentCore Memory. These go in Secrets Manager. Memory is for facts, preferences, and context — not credentials.

### Memory Records Are Eventually Consistent
After `ingest_conversation_events()`, memories are not immediately available for `retrieve_memory_records()`. There's a short extraction/indexing delay (seconds to minutes). Don't query memory immediately after ingesting within the same session — rely on the current conversation context instead.

### MEMORY.md Should Be Human-Readable
Even though MEMORY.md is updated by the consolidation job, it should be written in plain English that a human (the operator) can review and edit. This is the OpenClaw philosophy: files are the source of truth, humans can always inspect them.

### Memory Is Per-Agent
Each agent (supervisor, infra, code, comms) has its own memory space. The supervisor's memories are broader; specialist agents have narrower, domain-specific memories. This prevents cross-contamination and keeps specialist contexts focused.