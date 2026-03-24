"""Memory Consolidation Lambda.

Triggered nightly by EventBridge (cron: 0 2 * * ? * UTC) to consolidate
accumulated memories into a fresh MEMORY.md of ~100 lines.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MAX_MEMORY_LINES = 100

HAIKU_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

CONSOLIDATION_PROMPT = """You are a memory curator for an AI agent. Given:
1. The current MEMORY.md content
2. New memories from the past 24 hours

Produce an updated MEMORY.md that:
- Retains relevant, still-accurate facts
- Adds important new facts from today's memories
- Removes outdated or contradicted entries
- Stays under 100 lines
- Preserves markdown formatting

Current MEMORY.md:
{current_memory}

New memories (last 24 hours):
{new_memories}

Output only the updated MEMORY.md content, nothing else."""


def handler(event: dict, context: object) -> dict:
    """Memory consolidation Lambda entry point.

    Expected event keys:
        agentId         – the agent identifier
        bucket          – S3 bucket for workspace files
        tenantId        – tenant identifier for S3 key prefix
        memoryStoreId   – AgentCore Memory store ID
        snsTopicArn     – SNS topic ARN for failure alerts
        dynamoTableName – DynamoDB table name for hot cache
    """
    agent_id: str = event["agentId"]
    bucket: str = event["bucket"]
    tenant_id: str = event["tenantId"]
    memory_store_id: str = event["memoryStoreId"]
    sns_topic_arn: str = event["snsTopicArn"]
    dynamo_table_name: str = event["dynamoTableName"]

    s3 = boto3.client("s3")
    dynamo = boto3.resource("dynamodb")
    table = dynamo.Table(dynamo_table_name)
    bedrock = boto3.client("bedrock-runtime")
    memory_client = boto3.client("bedrock-agent-runtime")
    sns = boto3.client("sns")

    memory_key = f"{tenant_id}/{agent_id}/MEMORY.md"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        # 1. Retrieve memories from past 24 hours via AgentCore Memory
        new_memories = _retrieve_recent_memories(memory_client, memory_store_id, agent_id)

        # 2. Read current MEMORY.md from S3
        current_memory = _read_memory_from_s3(s3, bucket, memory_key)

        # 3. Invoke Claude Haiku to produce updated MEMORY.md
        updated_memory = _invoke_haiku(bedrock, current_memory, new_memories)

        # 4. Truncate to 100 lines if needed
        updated_memory = _truncate_to_limit(updated_memory)

        # 5. Write updated MEMORY.md to S3 and DynamoDB hot cache
        _write_memory_to_s3(s3, bucket, memory_key, updated_memory)
        _write_memory_to_dynamo(table, agent_id, memory_key, updated_memory)

        # 6. Archive raw session logs
        _archive_memories(s3, bucket, agent_id, today, new_memories)
        logger.info(
            "Memory consolidation completed successfully",
            extra={"agent_id": agent_id, "date": today},
        )
        return {"statusCode": 200, "body": "Consolidation complete"}

    except Exception as exc:
        logger.error(
            "Memory consolidation failed – leaving MEMORY.md untouched",
            exc_info=True,
            extra={"agent_id": agent_id},
        )
        _send_sns_alert(sns, sns_topic_arn, agent_id, exc)
        return {"statusCode": 500, "body": "Consolidation failed"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _retrieve_recent_memories(
    memory_client: object,
    memory_store_id: str,
    agent_id: str,
) -> list[dict]:
    """Retrieve all memories from the past 24 hours via AgentCore Memory."""
    try:
        response = memory_client.retrieve_memory(
            memoryStoreId=memory_store_id,
            agentId=agent_id,
            query={"text": "recent conversations and events from the past 24 hours"},
            maxResults=10,
        )
        return response.get("results", [])
    except (BotoCoreError, ClientError):
        logger.warning(
            "Failed to retrieve recent memories from AgentCore",
            exc_info=True,
            extra={"agent_id": agent_id, "memory_store_id": memory_store_id},
        )
        return []


def _read_memory_from_s3(s3: object, bucket: str, key: str) -> str:
    """Read current MEMORY.md content from S3."""
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        return response["Body"].read().decode("utf-8")
    except s3.exceptions.NoSuchKey:
        logger.info("No existing MEMORY.md found in S3 – starting fresh")
        return ""
    except (BotoCoreError, ClientError):
        logger.warning("Failed to read MEMORY.md from S3", exc_info=True)
        return ""


def _invoke_haiku(
    bedrock: object,
    current_memory: str,
    new_memories: list[dict],
) -> str:
    """Invoke Claude Haiku to produce an updated MEMORY.md."""
    memories_text = "\n".join(
        item.get("content", str(item)) for item in new_memories
    )

    prompt = CONSOLIDATION_PROMPT.format(
        current_memory=current_memory or "(empty)",
        new_memories=memories_text or "(no new memories)",
    )

    response = bedrock.converse(
        modelId=HAIKU_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 4096, "temperature": 0.3},
    )

    output = response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])
    return content_blocks[0].get("text", "") if content_blocks else ""


def _truncate_to_limit(content: str) -> str:
    """Truncate content to MAX_MEMORY_LINES lines, logging a warning if needed."""
    lines = content.split("\n")
    if len(lines) > MAX_MEMORY_LINES:
        logger.warning(
            "Consolidation output exceeded %d lines (%d) – truncating",
            MAX_MEMORY_LINES,
            len(lines),
        )
        return "\n".join(lines[:MAX_MEMORY_LINES])
    return content


def _write_memory_to_s3(s3: object, bucket: str, key: str, content: str) -> None:
    """Write updated MEMORY.md to S3."""
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=content.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    logger.info("Wrote updated MEMORY.md to S3", extra={"key": key})


def _write_memory_to_dynamo(
    table: object,
    agent_id: str,
    file_key: str,
    content: str,
) -> None:
    """Write updated MEMORY.md to DynamoDB hot cache."""
    table.put_item(
        Item={
            "agent_id": agent_id,
            "file_key": file_key,
            "content": content,
            "updated_at": int(time.time() * 1000),
            "version": int(time.time() * 1000),
        }
    )
    logger.info(
        "Wrote updated MEMORY.md to DynamoDB hot cache",
        extra={"agent_id": agent_id, "file_key": file_key},
    )


def _archive_memories(
    s3: object,
    bucket: str,
    agent_id: str,
    date: str,
    memories: list[dict],
) -> None:
    """Archive raw session logs to S3."""
    archive_key = f"memory-archive/{agent_id}/{date}/memories.json"
    s3.put_object(
        Bucket=bucket,
        Key=archive_key,
        Body=json.dumps(memories, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    logger.info("Archived memories to S3", extra={"key": archive_key})


def _send_sns_alert(
    sns: object,
    topic_arn: str,
    agent_id: str,
    error: Exception,
) -> None:
    """Send SNS alert on consolidation failure."""
    try:
        sns.publish(
            TopicArn=topic_arn,
            Subject=f"Memory Consolidation Failed – {agent_id}",
            Message=(
                f"Memory consolidation failed for agent {agent_id}.\n\n"
                f"Error: {error!r}\n\n"
                "The existing MEMORY.md has been left untouched."
            ),
        )
    except Exception:
        logger.error(
            "Failed to send SNS alert for consolidation failure",
            exc_info=True,
        )
