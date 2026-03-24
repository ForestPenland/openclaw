"""AgentCore Memory Adapter.

Replaces OpenClaw's local SQLite semantic index with Amazon Bedrock AgentCore
Memory for cloud-scale semantic and episodic retrieval.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class MemoryRecord:
    """A single memory record returned from semantic search."""

    record_id: str
    content: str
    score: float = 0.0
    timestamp: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class AgentCoreMemoryAdapter:
    """Adapter that routes memory operations to AgentCore Memory.

    Key behaviours
    --------------
    * **Ingest** – after each conversation turn, store user message + agent
      response as conversation events (Requirement 3.1).
    * **Retrieve** – semantic search using the current user message, return
      up to ``max_results`` records (default 10) (Requirement 3.2).
    * **MEMORY.md** remains Tier 1 – loaded in full at every session start
      from S3, *not* from AgentCore (Requirement 3.3).
    * **Local mode** – when ``active`` is ``False`` the adapter is a no-op so
      that OpenClaw's native SQLite memory works unchanged (Requirement 3.5).
    * **Graceful degradation** – all public methods catch exceptions, log a
      warning, and return a safe default so the caller is never blocked.
    """

    def __init__(
        self,
        memory_store_id: str,
        agent_id: str,
        *,
        active: bool = True,
        client: Any | None = None,
    ) -> None:
        """Initialise with AgentCore Memory store ID.

        Parameters
        ----------
        memory_store_id:
            The AgentCore Memory store identifier.
        agent_id:
            The agent identifier used to scope memory operations.
        active:
            When ``False`` (local / Phase 0 mode) every method is a no-op.
        client:
            Optional pre-configured ``bedrock-agent-runtime`` boto3 client.
            If not provided one is created automatically.
        """
        self.memory_store_id = memory_store_id
        self.agent_id = agent_id
        self.active = active
        self._client = client

    # -- lazy client --------------------------------------------------------

    @property
    def client(self) -> Any:
        """Lazily create the boto3 client so inactive adapters never connect."""
        if self._client is None:
            self._client = boto3.client("bedrock-agent-runtime")
        return self._client

    # -- public API ---------------------------------------------------------

    async def ingest(
        self,
        user_message: str,
        agent_response: str,
        session_id: str,
    ) -> None:
        """Ingest a conversation turn as events into AgentCore Memory.

        Stores both the user message and the agent response so that future
        semantic searches can surface relevant past interactions.

        Requirement 3.1: every completed conversation turn ingests both the
        user message and the agent response.
        """
        if not self.active:
            return

        try:
            events = [
                {
                    "eventId": str(uuid.uuid4()),
                    "eventTimestamp": _epoch_now(),
                    "eventType": "CONVERSATION",
                    "eventData": {
                        "role": "user",
                        "content": user_message,
                        "sessionId": session_id,
                        "agentId": self.agent_id,
                    },
                },
                {
                    "eventId": str(uuid.uuid4()),
                    "eventTimestamp": _epoch_now(),
                    "eventType": "CONVERSATION",
                    "eventData": {
                        "role": "assistant",
                        "content": agent_response,
                        "sessionId": session_id,
                        "agentId": self.agent_id,
                    },
                },
            ]

            self.client.invoke_memory(
                memoryStoreId=self.memory_store_id,
                agentId=self.agent_id,
                events=events,
            )

            logger.info(
                "Ingested conversation turn into AgentCore Memory",
                extra={
                    "session_id": session_id,
                    "agent_id": self.agent_id,
                    "memory_store_id": self.memory_store_id,
                },
            )
        except (BotoCoreError, ClientError):
            logger.warning(
                "Failed to ingest conversation turn – continuing without memory ingest",
                exc_info=True,
                extra={
                    "session_id": session_id,
                    "agent_id": self.agent_id,
                    "memory_store_id": self.memory_store_id,
                },
            )
        except Exception:
            logger.warning(
                "Unexpected error during memory ingest – continuing",
                exc_info=True,
            )

    async def retrieve(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[MemoryRecord]:
        """Semantic search for relevant memories.

        Returns at most ``max_results`` records (default 10).

        Requirement 3.2: retrieve up to 10 memory records via semantic search.
        """
        if not self.active:
            return []

        try:
            response = self.client.retrieve_memory(
                memoryStoreId=self.memory_store_id,
                agentId=self.agent_id,
                query={
                    "text": query,
                },
                maxResults=min(max_results, 10),
            )

            records: list[MemoryRecord] = []
            for item in response.get("results", []):
                records.append(
                    MemoryRecord(
                        record_id=item.get("recordId", str(uuid.uuid4())),
                        content=item.get("content", ""),
                        score=float(item.get("score", 0.0)),
                        timestamp=float(item.get("timestamp", 0.0)),
                        metadata=item.get("metadata", {}),
                    )
                )

            logger.info(
                "Retrieved %d memory records",
                len(records),
                extra={
                    "agent_id": self.agent_id,
                    "memory_store_id": self.memory_store_id,
                    "result_count": len(records),
                },
            )
            return records

        except (BotoCoreError, ClientError):
            logger.warning(
                "Failed to retrieve memories – returning empty list",
                exc_info=True,
                extra={
                    "agent_id": self.agent_id,
                    "memory_store_id": self.memory_store_id,
                },
            )
            return []
        except Exception:
            logger.warning(
                "Unexpected error during memory retrieval – returning empty list",
                exc_info=True,
            )
            return []

    async def create_store(self, namespace: str) -> str:
        """Create a new memory store with semantic + episodic strategies.

        Requirement 3.4: configure both semantic and episodic memory strategies
        when the store is created for the first time.

        Returns the memory store ID.
        """
        if not self.active:
            return ""

        try:
            # Use the bedrock-agent client for store management operations
            mgmt_client = boto3.client("bedrock-agent")

            response = mgmt_client.create_memory_store(
                name=f"openclaw-{namespace}",
                description=f"OpenClaw memory store for namespace: {namespace}",
                memoryStrategies=[
                    {
                        "strategyType": "SEMANTIC",
                        "configuration": {
                            "description": "Semantic memory for conversation context retrieval",
                        },
                    },
                    {
                        "strategyType": "EPISODIC",
                        "configuration": {
                            "description": "Episodic memory for temporal event sequences",
                        },
                    },
                ],
            )

            store_id = response.get("memoryStoreId", "")
            logger.info(
                "Created AgentCore Memory store",
                extra={
                    "memory_store_id": store_id,
                    "namespace": namespace,
                },
            )
            return store_id

        except (BotoCoreError, ClientError):
            logger.warning(
                "Failed to create memory store – continuing without store creation",
                exc_info=True,
                extra={"namespace": namespace},
            )
            return ""
        except Exception:
            logger.warning(
                "Unexpected error during memory store creation – continuing",
                exc_info=True,
            )
            return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _epoch_now() -> float:
    """Return the current time as a Unix epoch timestamp."""
    return time.time()
