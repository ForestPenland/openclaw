#!/usr/bin/env python3
"""Migrate local memory files to AgentCore Memory.

Reads MEMORY.md and memory/YYYY-MM-DD.md daily logs from the local
workspace path and ingests them into AgentCore Memory via boto3.

Requirements: 19.4
"""

import argparse
import os
import re
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

DAILY_LOG_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate local MEMORY.md and daily logs to AgentCore Memory"
    )
    parser.add_argument(
        "--memory-store-id", required=True, help="AgentCore Memory store ID"
    )
    parser.add_argument("--agent-id", required=True, help="Agent identifier")
    parser.add_argument(
        "--workspace-path",
        required=True,
        help="Path to the local workspace directory containing MEMORY.md and memory/",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS CLI profile (default: use default profile)",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region (default: us-east-1)",
    )
    return parser.parse_args()


def create_memory_client(
    profile: str | None, region: str
) -> "boto3.client":
    """Create a bedrock-agent-runtime client for AgentCore Memory operations."""
    session_kwargs: dict = {}
    if profile:
        session_kwargs["profile_name"] = profile
    session_kwargs["region_name"] = region
    session = boto3.Session(**session_kwargs)
    return session.client("bedrock-agent-runtime")


def read_file_content(filepath: str) -> str | None:
    """Read UTF-8 content from a file, returning None if not found."""
    if not os.path.isfile(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def find_daily_logs(workspace_path: str) -> list[str]:
    """Find all memory/YYYY-MM-DD.md daily log files in the workspace."""
    memory_dir = os.path.join(workspace_path, "memory")
    if not os.path.isdir(memory_dir):
        return []

    logs: list[str] = []
    for entry in sorted(os.listdir(memory_dir)):
        if DAILY_LOG_PATTERN.match(entry):
            logs.append(os.path.join(memory_dir, entry))
    return logs


def ingest_memory(
    client: "boto3.client",
    memory_store_id: str,
    agent_id: str,
    source_name: str,
    content: str,
) -> None:
    """Ingest a single memory document into AgentCore Memory.

    Uses the bedrock-agent-runtime client to ingest content.
    The exact API method may need adjustment as AgentCore Memory
    API stabilises.
    """
    client.ingest_knowledge(
        memoryStoreId=memory_store_id,
        agentId=agent_id,
        content={
            "text": content,
        },
        metadata={
            "source": source_name,
            "type": "migration",
        },
    )


def migrate(
    client: "boto3.client",
    memory_store_id: str,
    agent_id: str,
    workspace_path: str,
) -> None:
    """Migrate MEMORY.md and daily logs into AgentCore Memory."""
    workspace = os.path.normpath(workspace_path)
    if not os.path.isdir(workspace):
        print(
            f"ERROR: workspace path not found: {workspace}",
            file=sys.stderr,
        )
        sys.exit(1)

    ingested = 0

    # Ingest MEMORY.md
    memory_path = os.path.join(workspace, "MEMORY.md")
    content = read_file_content(memory_path)
    if content is not None:
        print(f"Ingesting MEMORY.md ({len(content)} bytes)")
        ingest_memory(client, memory_store_id, agent_id, "MEMORY.md", content)
        ingested += 1
    else:
        print("WARNING: MEMORY.md not found, skipping", file=sys.stderr)

    # Ingest daily logs
    daily_logs = find_daily_logs(workspace)
    if not daily_logs:
        print("No daily log files found in memory/ — skipping daily logs")
    else:
        for log_path in daily_logs:
            filename = os.path.basename(log_path)
            log_content = read_file_content(log_path)
            if log_content is not None:
                source = f"memory/{filename}"
                print(f"Ingesting {source} ({len(log_content)} bytes)")
                ingest_memory(
                    client, memory_store_id, agent_id, source, log_content
                )
                ingested += 1

    print(f"Done — {ingested} file(s) ingested into AgentCore Memory store {memory_store_id}")


def main() -> None:
    args = parse_args()
    try:
        client = create_memory_client(args.profile, args.region)
        migrate(client, args.memory_store_id, args.agent_id, args.workspace_path)
    except (BotoCoreError, ClientError) as exc:
        print(f"ERROR: AWS operation failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
