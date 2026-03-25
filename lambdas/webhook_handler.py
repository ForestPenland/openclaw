"""Webhook Lambda Handler.

Processes incoming webhook payloads from external platforms (Telegram, Slack, GitHub).
Verifies platform-specific HMAC-SHA256 signatures, deduplicates via DynamoDB, and
forwards new events to SQS for downstream processing.

Requirements: 6.3, 6.4, 6.6, 8.4, 8.5, 20.1, 20.2, 20.3
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEDUP_TABLE_NAME = os.environ.get("DEDUP_TABLE_NAME", "openclaw-dedup")
SQS_QUEUE_URL = os.environ.get("SQS_QUEUE_URL", "")
SECRETS_PREFIX = "openclaw/"
TTL_24H_SECONDS = 86400

SUPPORTED_PLATFORMS = {"telegram", "slack", "github"}

# Module-level cache for boto3 clients and secrets
_clients: dict[str, object] = {}
_secrets_cache: dict[str, str] = {}


def _get_client(service: str) -> object:
    """Return a cached boto3 client for the given service."""
    if service not in _clients:
        _clients[service] = boto3.client(service)
    return _clients[service]


def _get_secret(secret_name: str) -> str:
    """Retrieve a secret from Secrets Manager, using a module-level cache."""
    if secret_name in _secrets_cache:
        return _secrets_cache[secret_name]
    sm = _get_client("secretsmanager")
    response = sm.get_secret_value(SecretId=secret_name)
    value = response["SecretString"]
    _secrets_cache[secret_name] = value
    return value


# ---------------------------------------------------------------------------
# Signature verification per platform
# ---------------------------------------------------------------------------


def verify_telegram_signature(body: str, headers: dict[str, str], secret: str) -> bool:
    """Verify Telegram webhook using the ``X-Telegram-Bot-Api-Secret-Token`` header.

    When a ``secret_token`` is set via ``setWebhook``, Telegram sends it in
    the ``X-Telegram-Bot-Api-Secret-Token`` header on every request. We
    compare it to a deterministic token derived from the bot token (SHA-256
    hex digest, truncated to 64 chars to stay within Telegram's 256-char
    limit).

    If no secret_token was configured on the webhook (header is absent),
    fall back to basic structural validation of the Telegram payload.
    """
    provided = headers.get("x-telegram-bot-api-secret-token", "")
    expected = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:64]

    if provided:
        return hmac.compare_digest(expected, provided)

    # Fallback: no secret_token header — validate payload looks like Telegram
    try:
        payload = json.loads(body)
        return "update_id" in payload
    except (json.JSONDecodeError, TypeError):
        return False


def verify_slack_signature(body: str, headers: dict[str, str], secret: str) -> bool:
    """Verify Slack request signature using HMAC-SHA256 with ``v0:timestamp:body``."""
    timestamp = headers.get("x-slack-request-timestamp", "")
    provided_sig = headers.get("x-slack-signature", "")
    if not timestamp or not provided_sig:
        return False
    sig_basestring = f"v0:{timestamp}:{body}"
    expected = (
        "v0="
        + hmac.new(
            secret.encode("utf-8"),
            sig_basestring.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(expected, provided_sig)


def verify_github_signature(body: str, headers: dict[str, str], secret: str) -> bool:
    """Verify GitHub webhook using HMAC-SHA256 with ``X-Hub-Signature-256``."""
    provided_sig = headers.get("x-hub-signature-256", "")
    if not provided_sig:
        return False
    expected = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(expected, provided_sig)


VERIFIERS = {
    "telegram": verify_telegram_signature,
    "slack": verify_slack_signature,
    "github": verify_github_signature,
}

SECRET_NAMES = {
    "telegram": f"{SECRETS_PREFIX}telegram-bot-token",
    "slack": f"{SECRETS_PREFIX}slack-signing-secret",
    "github": f"{SECRETS_PREFIX}github-webhook-secret",
}


# ---------------------------------------------------------------------------
# Webhook ID extraction per platform
# ---------------------------------------------------------------------------


def _extract_webhook_id(platform: str, body: str) -> str:
    """Extract a unique webhook identifier from the payload."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        # Fall back to hash of body if payload is not JSON
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    if platform == "telegram":
        return f"telegram-{payload.get('update_id', hashlib.sha256(body.encode('utf-8')).hexdigest())}"
    if platform == "slack":
        event = payload.get("event", {})
        event_id = payload.get("event_id", event.get("client_msg_id", ""))
        if event_id:
            return f"slack-{event_id}"
        return f"slack-{hashlib.sha256(body.encode('utf-8')).hexdigest()}"
    if platform == "github":
        # GitHub provides X-GitHub-Delivery header, but we extract from payload as fallback
        delivery_id = payload.get("delivery", "")
        if delivery_id:
            return f"github-{delivery_id}"
        return f"github-{hashlib.sha256(body.encode('utf-8')).hexdigest()}"

    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# DynamoDB deduplication
# ---------------------------------------------------------------------------


def _check_dedup(table: object, webhook_id: str) -> bool:
    """Return True if webhook_id already exists in the dedup table."""
    try:
        response = table.get_item(Key={"webhook_id": webhook_id})
        return "Item" in response
    except (BotoCoreError, ClientError):
        logger.warning(
            "DynamoDB dedup table unreachable – processing webhook to prefer availability",
            exc_info=True,
            extra={"webhook_id": webhook_id},
        )
        return False


def _store_dedup(table: object, webhook_id: str, platform: str) -> None:
    """Store webhook_id in the dedup table with a 24-hour TTL."""
    now = int(time.time())
    table.put_item(
        Item={
            "webhook_id": webhook_id,
            "platform": platform,
            "received_at": int(now * 1000),
            "ttl": now + TTL_24H_SECONDS,
        }
    )


# ---------------------------------------------------------------------------
# SQS forwarding
# ---------------------------------------------------------------------------


def _forward_to_sqs(sqs: object, queue_url: str, platform: str, body: str) -> None:
    """Send the webhook payload to SQS for downstream processing."""
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({"platform": platform, "payload": body}),
        MessageAttributes={
            "platform": {"DataType": "String", "StringValue": platform},
        },
    )


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def handler(event: dict, context: object) -> dict:
    """Webhook Lambda entry point.

    Receives an API Gateway HTTP API (v2) event. Extracts the platform from
    the path, verifies the signature, deduplicates, and forwards to SQS.
    """
    # 1. Extract platform from path: /webhook/{platform}
    raw_path = event.get("rawPath", "")
    path_params = event.get("pathParameters", {}) or {}
    platform = path_params.get("proxy", "").strip("/")
    if not platform:
        # Fallback: parse from rawPath
        parts = [p for p in raw_path.split("/") if p]
        platform = parts[-1] if parts else ""

    platform = platform.lower()

    if platform not in SUPPORTED_PLATFORMS:
        logger.warning("Unsupported platform: %s", platform)
        return {"statusCode": 400, "body": json.dumps({"error": f"Unsupported platform: {platform}"})}

    # 2. Normalise headers to lowercase
    raw_headers = event.get("headers", {}) or {}
    headers = {k.lower(): v for k, v in raw_headers.items()}

    # 3. Get request body
    body = event.get("body", "") or ""

    # 4. Retrieve platform secret from Secrets Manager
    secret_name = SECRET_NAMES[platform]
    try:
        secret = _get_secret(secret_name)
    except (BotoCoreError, ClientError) as exc:
        logger.error(
            "Failed to retrieve secret for platform %s",
            platform,
            exc_info=True,
        )
        return {"statusCode": 500, "body": json.dumps({"error": "Internal error"})}

    # 5. Verify platform-specific signature
    verifier = VERIFIERS[platform]
    if not verifier(body, headers, secret):
        source_ip = (
            event.get("requestContext", {}).get("http", {}).get("sourceIp", "unknown")
        )
        logger.warning(
            "Signature verification failed",
            extra={"platform": platform, "source_ip": source_ip},
        )
        return {"statusCode": 401, "body": json.dumps({"error": "Signature verification failed"})}

    # 6. Extract webhook ID and check dedup table
    webhook_id = _extract_webhook_id(platform, body)

    # Also use X-GitHub-Delivery header if available for GitHub
    if platform == "github" and headers.get("x-github-delivery"):
        webhook_id = f"github-{headers['x-github-delivery']}"

    dynamo = boto3.resource("dynamodb")
    table = dynamo.Table(DEDUP_TABLE_NAME)

    if _check_dedup(table, webhook_id):
        logger.info("Duplicate webhook – skipping", extra={"webhook_id": webhook_id})
        return {"statusCode": 200, "body": json.dumps({"status": "duplicate"})}

    # 7. Store in dedup table and forward to SQS
    _store_dedup(table, webhook_id, platform)

    sqs = _get_client("sqs")
    try:
        _forward_to_sqs(sqs, SQS_QUEUE_URL, platform, body)
    except (BotoCoreError, ClientError):
        logger.error("Failed to forward webhook to SQS", exc_info=True)
        return {"statusCode": 500, "body": json.dumps({"error": "Failed to enqueue message"})}

    logger.info(
        "Webhook processed successfully",
        extra={"platform": platform, "webhook_id": webhook_id},
    )
    return {"statusCode": 200, "body": json.dumps({"status": "processed"})}
