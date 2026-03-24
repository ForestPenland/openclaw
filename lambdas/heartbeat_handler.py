"""Heartbeat Lambda Handler.

Triggered every 30 minutes by EventBridge Scheduler. Loads HEARTBEAT.md from
the S3 workspace, invokes the Supervisor Agent with the checklist, and routes
the response: silently drops HEARTBEAT_OK, forwards actions/alerts to the
operator via SNS.

Requirements: 7.2, 7.3, 7.4
"""

from __future__ import annotations

import json
import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

WORKSPACE_BUCKET = os.environ.get("WORKSPACE_BUCKET", "")
TENANT_ID = os.environ.get("TENANT_ID", "")
AGENT_ID = os.environ.get("AGENT_ID", "")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
SUPERVISOR_FUNCTION_ARN = os.environ.get("SUPERVISOR_FUNCTION_ARN", "")

HEARTBEAT_OK = "HEARTBEAT_OK"
SUPERVISOR_TIMEOUT_SECONDS = 60

# Module-level cache for boto3 clients
_clients: dict[str, object] = {}


def _get_client(service: str, **kwargs) -> object:
    """Return a cached boto3 client for the given service."""
    cache_key = f"{service}:{json.dumps(kwargs, sort_keys=True)}"
    if cache_key not in _clients:
        _clients[cache_key] = boto3.client(service, **kwargs)
    return _clients[cache_key]


def handler(event: dict, context: object) -> dict:
    """Heartbeat Lambda entry point.

    Receives an EventBridge scheduled event. Loads HEARTBEAT.md from S3,
    invokes the Supervisor Agent, and routes the response accordingly.
    """
    bucket = WORKSPACE_BUCKET
    tenant_id = TENANT_ID
    agent_id = AGENT_ID
    sns_topic_arn = SNS_TOPIC_ARN
    supervisor_arn = SUPERVISOR_FUNCTION_ARN

    s3 = _get_client("s3")
    sns = _get_client("sns")
    lambda_client = _get_client(
        "lambda",
        config=Config(read_timeout=SUPERVISOR_TIMEOUT_SECONDS + 10),
    )

    heartbeat_key = f"{tenant_id}/{agent_id}/HEARTBEAT.md"

    # 1. Load HEARTBEAT.md from S3
    checklist = _load_heartbeat(s3, bucket, heartbeat_key)
    if checklist is None:
        logger.error(
            "HEARTBEAT.md not found in S3 – skipping heartbeat cycle",
            extra={"bucket": bucket, "key": heartbeat_key},
        )
        _send_sns_alert(
            sns,
            sns_topic_arn,
            agent_id,
            "HEARTBEAT.md not found in S3. Heartbeat cycle skipped.",
        )
        return {"statusCode": 404, "body": "HEARTBEAT.md not found"}

    # 2. Invoke Supervisor Agent with the checklist
    response_text = _invoke_supervisor(
        lambda_client, supervisor_arn, agent_id, checklist
    )
    if response_text is None:
        # Timeout or invocation error – already logged inside helper
        _send_sns_alert(
            sns,
            sns_topic_arn,
            agent_id,
            "Supervisor Agent timed out or failed during heartbeat invocation.",
        )
        return {"statusCode": 504, "body": "Supervisor timeout"}

    # 3. Route the response
    if response_text.strip() == HEARTBEAT_OK:
        logger.info(
            "Heartbeat OK – no action required",
            extra={"agent_id": agent_id},
        )
        return {"statusCode": 200, "body": "HEARTBEAT_OK"}

    # 4. Response contains action/alert – forward to operator
    logger.info(
        "Heartbeat action required – forwarding to operator",
        extra={"agent_id": agent_id},
    )
    _forward_to_operator(sns, sns_topic_arn, agent_id, response_text)
    return {"statusCode": 200, "body": "Action forwarded"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_heartbeat(s3: object, bucket: str, key: str) -> str | None:
    """Load HEARTBEAT.md from S3. Returns None if not found."""
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        return response["Body"].read().decode("utf-8")
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("NoSuchKey", "404"):
            return None
        logger.error("Failed to load HEARTBEAT.md from S3", exc_info=True)
        return None
    except (BotoCoreError, Exception):
        logger.error("Failed to load HEARTBEAT.md from S3", exc_info=True)
        return None


def _invoke_supervisor(
    lambda_client: object,
    function_arn: str,
    agent_id: str,
    checklist: str,
) -> str | None:
    """Invoke the Supervisor Agent synchronously with a 60-second timeout.

    Returns the response text, or None on timeout/error.
    """
    payload = json.dumps({
        "channel": "heartbeat",
        "agentId": agent_id,
        "text": checklist,
    })

    try:
        response = lambda_client.invoke(
            FunctionName=function_arn,
            InvocationType="RequestResponse",
            Payload=payload.encode("utf-8"),
        )

        # Check for function error
        if response.get("FunctionError"):
            logger.error(
                "Supervisor Agent returned function error",
                extra={
                    "agent_id": agent_id,
                    "error": response.get("FunctionError"),
                },
            )
            return None

        response_payload = json.loads(response["Payload"].read().decode("utf-8"))

        # Extract text from response – support both flat string and dict formats
        if isinstance(response_payload, str):
            return response_payload
        if isinstance(response_payload, dict):
            return response_payload.get("text", response_payload.get("body", ""))
        return str(response_payload)

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if "Timeout" in error_code or "TooManyRequestsException" in error_code:
            logger.error(
                "Supervisor Agent timeout during heartbeat",
                extra={"agent_id": agent_id, "timeout_seconds": SUPERVISOR_TIMEOUT_SECONDS},
            )
        else:
            logger.error(
                "Supervisor Agent invocation failed",
                exc_info=True,
                extra={"agent_id": agent_id},
            )
        return None
    except (BotoCoreError, Exception):
        logger.error(
            "Supervisor Agent invocation failed",
            exc_info=True,
            extra={"agent_id": agent_id},
        )
        return None


def _forward_to_operator(
    sns: object,
    topic_arn: str,
    agent_id: str,
    message: str,
) -> None:
    """Forward heartbeat action/alert to operator via SNS."""
    try:
        sns.publish(
            TopicArn=topic_arn,
            Subject=f"Heartbeat Alert – {agent_id}",
            Message=(
                f"Heartbeat action required for agent {agent_id}.\n\n"
                f"{message}"
            ),
        )
        logger.info(
            "Forwarded heartbeat alert to operator via SNS",
            extra={"agent_id": agent_id},
        )
    except Exception:
        logger.error(
            "Failed to forward heartbeat alert via SNS",
            exc_info=True,
            extra={"agent_id": agent_id},
        )


def _send_sns_alert(
    sns: object,
    topic_arn: str,
    agent_id: str,
    message: str,
) -> None:
    """Send SNS alert for heartbeat errors."""
    try:
        sns.publish(
            TopicArn=topic_arn,
            Subject=f"Heartbeat Error – {agent_id}",
            Message=(
                f"Heartbeat error for agent {agent_id}.\n\n"
                f"{message}"
            ),
        )
    except Exception:
        logger.error(
            "Failed to send SNS alert for heartbeat error",
            exc_info=True,
            extra={"agent_id": agent_id},
        )
