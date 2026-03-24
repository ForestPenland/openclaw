# Skill: Messaging and Channels

**Purpose:** How the AWS-enhanced OpenClaw system integrates with messaging platforms (Telegram, Slack, web chat, webhooks) — extending the OpenClaw Gateway's 20+ platform integrations to work with a cloud-hosted deployment where `localhost:18789` is replaced by API Gateway.

---

## Hybrid Approach: OpenClaw Gateway vs. AWS Ingress

OpenClaw's Gateway already handles 20+ messaging platforms natively. For a cloud deploy, the Gateway still does all the heavy lifting — you're just adding an AWS ingress layer in front of it.

| Layer | OpenClaw Native | AWS Extension | Notes |
|-------|----------------|---------------|-------|
| Platform integrations | 20+ channels built in | No change | Keep all OpenClaw channel handlers as-is |
| Ingress URL | `localhost:18789` | API Gateway HTTPS URL | Telegram webhook URL update required |
| WebSocket server | `ws://127.0.0.1:18789` | API Gateway WebSocket API | Same protocol, different host |
| Message routing | Built into Gateway | No change | OpenClaw handles routing internally |
| Auth / rate limiting | Minimal | API Gateway throttling + Lambda authorizer | Add at the API Gateway layer |
| Message buffering | Synchronous | SQS between API GW and ECS Gateway | Prevents dropped messages during scale events |

**The key change:** When you move the Gateway to ECS, Telegram needs to be told about the new webhook URL. Update the Telegram bot webhook via:
```
POST https://api.telegram.org/bot{TOKEN}/setWebhook
{"url": "https://your-api-gateway-url.execute-api.us-east-1.amazonaws.com/webhook/telegram"}
```

**What stays the same:**
- All 20+ platform integrations work without modification
- Message format and routing logic inside the Gateway is unchanged
- Users see no difference — same Telegram bot, same Slack slash commands, same behavior

**What to add (not replace):**
- SQS buffer between API Gateway and the ECS Gateway (prevents message loss on cold starts)
- Lambda authorizer for signature verification (Telegram HMAC, Slack signature)
- CloudWatch alarms for message queue depth (detect processing backlogs)

---

## AWS Services Used

- **Amazon API Gateway** — HTTP and WebSocket endpoints for all channel ingress
- **AWS Lambda** — channel-specific message parsing and routing
- **Amazon SES** — outbound email sending
- **Amazon SNS** — notification fan-out
- **Amazon DynamoDB** — WebSocket connection state management
- **Amazon EventBridge** — event routing and scheduling

---

## Key APIs and Operations

### Telegram Bot Integration

```python
import json
import os
import requests
import boto3
from functools import lru_cache

@lru_cache(maxsize=1)
def get_bot_token() -> str:
    """Get Telegram bot token from Secrets Manager."""
    return boto3.client("secretsmanager").get_secret_value(
        SecretId="openclaw/telegram/bot-token"
    )["SecretString"]


def telegram_api(method: str, params: dict) -> dict:
    """Call Telegram Bot API."""
    token = get_bot_token()
    response = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=params,
        timeout=10
    )
    return response.json()


def send_telegram_message(
    chat_id: int,
    text: str,
    parse_mode: str = "Markdown",
    reply_to_message_id: int = None
) -> dict:
    """Send a message via Telegram."""
    params = {
        "chat_id": chat_id,
        "text": text[:4096],  # Telegram limit
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id

    # Handle long messages by chunking
    if len(text) > 4096:
        chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
        results = []
        for chunk in chunks:
            params["text"] = chunk
            results.append(telegram_api("sendMessage", params))
        return results[-1]

    return telegram_api("sendMessage", params)


def send_telegram_photo(chat_id: int, photo_url: str, caption: str = "") -> dict:
    """Send a photo via Telegram."""
    return telegram_api("sendPhoto", {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption[:1024]
    })


def send_telegram_document(chat_id: int, file_path: str, caption: str = "") -> dict:
    """Send a document/file via Telegram."""
    token = get_bot_token()
    with open(file_path, "rb") as f:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data={"chat_id": chat_id, "caption": caption},
            files={"document": f}
        )
    return response.json()


def parse_telegram_webhook(event: dict) -> dict:
    """Parse an incoming Telegram webhook event."""
    body = json.loads(event.get("body", "{}"))
    message = body.get("message") or body.get("edited_message", {})

    if not message:
        return None

    return {
        "user_id": str(message.get("from", {}).get("id", "")),
        "username": message.get("from", {}).get("username", ""),
        "first_name": message.get("from", {}).get("first_name", ""),
        "chat_id": message["chat"]["id"],
        "message_id": message.get("message_id"),
        "text": message.get("text", ""),
        "voice": message.get("voice"),  # Voice note
        "photo": message.get("photo"),  # Photo
        "document": message.get("document"),  # Document
        "channel": "telegram"
    }


# Telegram Lambda handler
def telegram_handler(event, context):
    """Main Lambda for Telegram webhook."""
    parsed = parse_telegram_webhook(event)

    if not parsed:
        return {"statusCode": 200}

    user_id = parsed["user_id"]
    chat_id = parsed["chat_id"]

    # Authorization check
    from auth import auth
    if not auth.is_authorized_user(int(user_id)):
        send_telegram_message(chat_id, "🔐 You're not authorized to use this agent.")
        return {"statusCode": 200}

    # Handle voice notes
    if parsed["voice"]:
        text = transcribe_telegram_voice(parsed["voice"]["file_id"])
        parsed["text"] = f"[Voice note]: {text}"

    # Route to agent
    response = route_to_supervisor(
        message=parsed["text"],
        user_id=user_id,
        channel="telegram",
        chat_id=chat_id
    )

    # Send response back
    send_telegram_message(chat_id, response)

    return {"statusCode": 200}


def transcribe_telegram_voice(file_id: str) -> str:
    """Download and transcribe a Telegram voice note."""
    token = get_bot_token()

    # Get file info
    file_response = requests.get(
        f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
    ).json()
    file_path = file_response["result"]["file_path"]

    # Download
    audio_response = requests.get(
        f"https://api.telegram.org/file/bot{token}/{file_path}"
    )

    # Transcribe using Amazon Transcribe
    import boto3
    import tempfile
    import uuid

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp.write(audio_response.content)
        tmp_path = tmp.name

    # Use Bedrock's multimodal capability or Amazon Transcribe
    # For Telegram voice notes (OGG format), use Amazon Transcribe
    transcribe = boto3.client("transcribe")
    s3_key = f"temp-voice/{uuid.uuid4().hex}.ogg"

    # Upload to S3 temporarily
    s3_client = boto3.client("s3")
    s3_client.upload_file(tmp_path, os.environ["ARTIFACTS_BUCKET"], s3_key)

    # Transcribe
    job_name = f"voice-{uuid.uuid4().hex[:8]}"
    transcribe.start_transcription_job(
        TranscriptionJobName=job_name,
        Media={"MediaFileUri": f"s3://{os.environ['ARTIFACTS_BUCKET']}/{s3_key}"},
        MediaFormat="ogg",
        LanguageCode="en-US"
    )

    # Wait for completion (simple polling for Lambda)
    import time
    for _ in range(30):
        status = transcribe.get_transcription_job(TranscriptionJobName=job_name)
        if status["TranscriptionJob"]["TranscriptionJobStatus"] in ["COMPLETED", "FAILED"]:
            break
        time.sleep(2)

    if status["TranscriptionJob"]["TranscriptionJobStatus"] == "COMPLETED":
        transcript_url = status["TranscriptionJob"]["Transcript"]["TranscriptFileUri"]
        transcript = requests.get(transcript_url).json()
        return transcript["results"]["transcripts"][0]["transcript"]

    return "[Voice transcription failed]"
```

### Slack Integration

```python
import json
import hashlib
import hmac
import time
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def get_slack_client() -> WebClient:
    """Get authenticated Slack client."""
    token = boto3.client("secretsmanager").get_secret_value(
        SecretId="openclaw/slack/bot-token"
    )["SecretString"]
    return WebClient(token=token)


def verify_slack_request(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify that the request actually came from Slack."""
    slack_signing_secret = boto3.client("secretsmanager").get_secret_value(
        SecretId="openclaw/slack/signing-secret"
    )["SecretString"]

    # Check timestamp to prevent replay attacks
    if abs(time.time() - int(timestamp)) > 60 * 5:
        return False

    sig_basestring = f"v0:{timestamp}:{body.decode()}"
    expected_signature = "v0=" + hmac.new(
        slack_signing_secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected_signature, signature)


def send_slack_message(channel: str, text: str, blocks: list = None) -> dict:
    """Send a message to a Slack channel."""
    client = get_slack_client()
    try:
        response = client.chat_postMessage(
            channel=channel,
            text=text,
            blocks=blocks,
            mrkdwn=True
        )
        return {"success": True, "ts": response["ts"]}
    except SlackApiError as e:
        return {"success": False, "error": str(e)}


def parse_slack_event(event_body: dict) -> dict:
    """Parse a Slack event payload."""
    slack_event = event_body.get("event", {})

    return {
        "user_id": slack_event.get("user", ""),
        "channel_id": slack_event.get("channel", ""),
        "text": slack_event.get("text", ""),
        "ts": slack_event.get("ts", ""),
        "thread_ts": slack_event.get("thread_ts"),
        "channel": "slack"
    }


def slack_handler(event, context):
    """Main Lambda for Slack Events API."""
    body = json.loads(event.get("body", "{}"))

    # Handle URL verification challenge (Slack setup)
    if body.get("type") == "url_verification":
        return {
            "statusCode": 200,
            "body": json.dumps({"challenge": body["challenge"]})
        }

    # Verify signature
    headers = event.get("headers", {})
    if not verify_slack_request(
        body=event["body"].encode(),
        timestamp=headers.get("X-Slack-Request-Timestamp", "0"),
        signature=headers.get("X-Slack-Signature", "")
    ):
        return {"statusCode": 401}

    # Parse and route
    parsed = parse_slack_event(body)

    # Ignore bot messages to prevent loops
    if body.get("event", {}).get("bot_id"):
        return {"statusCode": 200}

    response = route_to_supervisor(
        message=parsed["text"],
        user_id=parsed["user_id"],
        channel="slack",
        channel_id=parsed["channel_id"]
    )

    send_slack_message(parsed["channel_id"], response)
    return {"statusCode": 200}
```

### WebSocket Chat Interface

```python
# WebSocket handler for web chat interface

dynamodb = boto3.resource("dynamodb")
connections_table = dynamodb.Table("openclaw-websocket-connections")


def ws_connect_handler(event, context):
    """Handle WebSocket $connect."""
    connection_id = event["requestContext"]["connectionId"]

    # Store connection
    connections_table.put_item(Item={
        "connection_id": connection_id,
        "user_id": "web-user",
        "connected_at": int(time.time()),
        "ttl": int(time.time()) + 3600  # 1 hour TTL
    })

    return {"statusCode": 200}


def ws_disconnect_handler(event, context):
    """Handle WebSocket $disconnect."""
    connection_id = event["requestContext"]["connectionId"]
    connections_table.delete_item(Key={"connection_id": connection_id})
    return {"statusCode": 200}


def ws_message_handler(event, context):
    """Handle incoming WebSocket messages."""
    connection_id = event["requestContext"]["connectionId"]
    domain = event["requestContext"]["domainName"]
    stage = event["requestContext"]["stage"]

    body = json.loads(event.get("body", "{}"))
    user_message = body.get("message", "")

    # Create API Gateway Management API client for sending back
    apigw_management = boto3.client(
        "apigatewaymanagementapi",
        endpoint_url=f"https://{domain}/{stage}"
    )

    def send_chunk(text: str):
        """Send a streaming chunk back to the WebSocket client."""
        try:
            apigw_management.post_to_connection(
                ConnectionId=connection_id,
                Data=json.dumps({"type": "chunk", "content": text})
            )
        except Exception:
            pass  # Connection may have closed

    # Stream response back
    response = route_to_supervisor_streaming(
        message=user_message,
        user_id=f"web-{connection_id}",
        channel="web",
        on_token=send_chunk
    )

    # Send completion signal
    apigw_management.post_to_connection(
        ConnectionId=connection_id,
        Data=json.dumps({"type": "done", "full_response": response})
    )

    return {"statusCode": 200}
```

### Outbound Notifications

```python
def notify_operator(message: str, urgent: bool = False):
    """
    Send a notification to the operator via their configured channel.
    Used for heartbeat alerts, deployment completions, errors, etc.
    """
    # Determine notification channel from config
    operator_telegram_chat_id = int(
        boto3.client("ssm").get_parameter(
            Name="/openclaw/operator/telegram-chat-id"
        )["Parameter"]["Value"]
    )

    prefix = "🚨 URGENT: " if urgent else "📬 "
    send_telegram_message(
        chat_id=operator_telegram_chat_id,
        text=f"{prefix}{message}"
    )


def send_deployment_notification(
    service_name: str,
    endpoint_url: str,
    environment: str
):
    """Notify operator when a deployment completes."""
    notify_operator(
        f"✅ Deployment complete!\n"
        f"**Service:** `{service_name}`\n"
        f"**Environment:** {environment}\n"
        f"**URL:** {endpoint_url}"
    )


def send_error_alert(service_name: str, error_message: str, log_url: str = ""):
    """Alert operator about a service error."""
    notify_operator(
        f"❌ Error detected!\n"
        f"**Service:** `{service_name}`\n"
        f"**Error:** {error_message[:500]}\n"
        f"{'**Logs:** ' + log_url if log_url else ''}",
        urgent=True
    )
```

---

## Implementation Patterns

### Pattern 1: Unified Message Router

```python
def route_to_supervisor(
    message: str,
    user_id: str,
    channel: str,
    **channel_context
) -> str:
    """
    Route a message to the supervisor agent and return the response.
    This is the core routing function called by all channel handlers.
    """
    session_id = get_or_create_session(user_id, channel)

    agentcore = boto3.client("bedrock-agentcore-runtime")

    payload = {
        "message": message,
        "user_id": user_id,
        "channel": channel,
        "channel_context": channel_context,
        "session_id": session_id
    }

    response = agentcore.invoke_agent_runtime(
        agentRuntimeArn=os.environ["SUPERVISOR_AGENT_ARN"],
        payload=json.dumps(payload),
        sessionId=session_id
    )

    full_response = ""
    for event in response.get("stream", []):
        if "payloadPart" in event:
            chunk = event["payloadPart"]["bytes"].decode()
            try:
                data = json.loads(chunk)
                full_response += data.get("content", chunk)
            except json.JSONDecodeError:
                full_response += chunk

    return full_response or "I encountered an issue processing your request."
```

### Pattern 2: Webhook Routing

```python
WEBHOOK_ROUTES = {
    "/webhooks/github": handle_github_webhook,
    "/webhooks/stripe": handle_stripe_webhook,
    "/webhooks/openclaw": handle_openclaw_internal_webhook
}


def webhook_dispatcher(event, context):
    """Route incoming webhooks to appropriate handlers."""
    path = event.get("path", "/")

    handler = WEBHOOK_ROUTES.get(path)
    if not handler:
        return {"statusCode": 404}

    return handler(event, context)


def handle_github_webhook(event, context):
    """Process GitHub webhook events."""
    body = event.get("body", "{}")
    signature = event.get("headers", {}).get("X-Hub-Signature-256", "")

    if not verify_github_webhook(body.encode(), signature):
        return {"statusCode": 401}

    payload = json.loads(body)
    event_type = event.get("headers", {}).get("X-GitHub-Event", "")

    message = format_github_event(event_type, payload)

    if message:
        response = route_to_supervisor(
            message=f"[GitHub Event: {event_type}]\n{message}",
            user_id="github-webhook",
            channel="webhook"
        )
        # Optionally notify operator
        notify_operator(f"🐙 GitHub: {message[:200]}")

    return {"statusCode": 200}
```

---

## Gotchas & Best Practices

### Telegram Rate Limits
Telegram allows 30 messages per second per bot (across all chats), or 1 message per second per individual chat. For long responses, split into chunks and add delays:
```python
import time
for chunk in chunks:
    send_telegram_message(chat_id, chunk)
    time.sleep(0.1)  # 100ms between chunks
```

### WebSocket Connection Cleanup
API Gateway WebSocket connections that close without `$disconnect` can leak in DynamoDB. Always set a TTL on connection records (3600 seconds). Add a Lambda to clean up stale connections.

### Telegram Markdown Escaping
Telegram MarkdownV2 requires escaping special characters: `.`, `!`, `(`, `)`, `-`, `+`, `=`, `{`, `}`, `#`, `>`, `|`. Use MarkdownV1 (`Markdown` not `MarkdownV2`) for simpler formatting.

### Voice Note Transcription Latency
Amazon Transcribe adds 5-30 second latency for voice transcription. Send an immediate acknowledgment to the user ("🎤 Processing your voice note...") before routing to the agent.

### Slack Events vs Slash Commands
Slack sends events for @mentions and DMs (Events API), and separately for /slash commands. Handle both patterns:
- Events API: verify signature, extract message, route
- Slash commands: immediate 200 response (Slack requires <3s), then send async response to `response_url`

### Don't Store Chat IDs in Code
Chat IDs (Telegram) and channel IDs (Slack) change per deployment. Store the operator's chat ID in SSM Parameter Store and retrieve at runtime.

### Idempotent Webhook Processing
Stripe and GitHub may send duplicate webhook events. Store processed webhook IDs in DynamoDB (with TTL) and skip duplicates:
```python
def is_webhook_duplicate(webhook_id: str) -> bool:
    """Check if this webhook was already processed."""
    table = dynamodb.Table("openclaw-processed-webhooks")
    try:
        table.put_item(
            Item={"webhook_id": webhook_id, "ttl": int(time.time()) + 86400},
            ConditionExpression="attribute_not_exists(webhook_id)"
        )
        return False  # First time seeing this webhook
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return True   # Already processed
```