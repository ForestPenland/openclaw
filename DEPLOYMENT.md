# OpenClaw on AWS — Deployment Guide

Deploy the OpenClaw AI agent framework on AWS using ECS Fargate, Amazon Bedrock, and CDK.

The OpenClaw Gateway runs as a Docker container on Fargate. It handles Telegram, Slack, and WebSocket messaging natively — no custom webhook Lambdas or SQS queues in the messaging path. Amazon Bedrock (Nova Lite by default) provides the AI model via OpenClaw's provider plugin system. Workspace files (SOUL.md, MEMORY.md, etc.) are synced from S3 at startup.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Local Testing](#local-testing)
- [AWS Deployment](#aws-deployment)
- [CDK Stacks Reference](#cdk-stacks-reference)
- [Workspace Seeding](#workspace-seeding)
- [Gateway Configuration](#gateway-configuration)
- [Connecting Telegram](#connecting-telegram)
- [Connecting Slack](#connecting-slack)
- [Changing the AI Model](#changing-the-ai-model)
- [Post-Deployment Verification](#post-deployment-verification)
- [Environment Variables Reference](#environment-variables-reference)
- [Cost Estimates](#cost-estimates)
- [Troubleshooting](#troubleshooting)
- [Cleanup](#cleanup)

---

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| AWS CLI | 2.x | [Install guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| AWS CDK | 2.150+ | `npm install -g aws-cdk` |
| Python | 3.12+ | [python.org](https://www.python.org/downloads/) |
| Node.js | 22+ | [nvm](https://github.com/nvm-sh/nvm) or [nodejs.org](https://nodejs.org) |
| Docker | 24+ | [docker.com](https://docs.docker.com/get-docker/) — required for CDK image builds |

### AWS Profile Setup

```bash
aws configure --profile openclaw-dev
# Region: us-east-1
# Output: json

# Verify
aws sts get-caller-identity --profile openclaw-dev
```

The deploying user needs broad permissions (CloudFormation, S3, DynamoDB, ECS, ECR, IAM, Lambda, Bedrock, Cognito, Secrets Manager, CloudWatch, EventBridge, SNS, API Gateway). `AdministratorAccess` works for development.

---

## Local Testing

Run OpenClaw locally with zero AWS dependencies to verify the Gateway starts:

```bash
cd openclaw
chmod +x scripts/test-local.sh
./scripts/test-local.sh
```

The script starts the Gateway on `ws://127.0.0.1:18789`, waits for the port, and verifies the WebSocket handshake (HTTP 101). Uses `PROVIDER=anthropic` so no AWS adapters are loaded.

---

## AWS Deployment

### Step 1: Install CDK Dependencies

```bash
cd openclaw/infra
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 2: Bootstrap CDK (first time only)

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --profile openclaw-dev --query Account --output text)
cdk bootstrap aws://${ACCOUNT_ID}/us-east-1 --profile openclaw-dev
```

### Step 3: Deploy All Stacks

```bash
cd openclaw/infra
cdk deploy --all --profile openclaw-dev --require-approval broadening
```

CDK builds the custom Docker image from `Dockerfile.gateway`, pushes it to ECR, and deploys all 7 stacks in dependency order. First deployment takes ~10–15 minutes (Docker build is the bottleneck). Subsequent deploys are faster due to layer caching.

### Step 4: Verify Deployment

```bash
# All stacks should show CREATE_COMPLETE or UPDATE_COMPLETE
for stack in OpenClawStorage OpenClawGateway OpenClawIdentity OpenClawApi OpenClawMemory OpenClawScheduler OpenClawBuilder; do
  echo -n "$stack: "
  aws cloudformation describe-stacks \
    --stack-name $stack \
    --query 'Stacks[0].StackStatus' \
    --profile openclaw-dev \
    --output text 2>/dev/null || echo "NOT_FOUND"
done
```

---

## CDK Stacks Reference

All stacks are defined in `infra/stacks/` and wired together in `infra/app.py`.

| Stack | What It Creates |
|-------|----------------|
| **OpenClawStorage** | S3 workspace bucket, DynamoDB tables (memory, sessions, agents, dedup, connections) |
| **OpenClawGateway** | VPC (2 AZs, 1 NAT GW), ECS Fargate cluster, task definition (1024 CPU / 2048 MiB), Gateway service, CloudWatch log group. Builds custom Docker image from `Dockerfile.gateway` |
| **OpenClawIdentity** | Cognito user pool (invite-only), Secrets Manager secrets for Telegram/Slack/GitHub tokens |
| **OpenClawApi** | HTTP API Gateway, WebSocket API Gateway, webhook Lambda, SQS queue. **Not in the messaging path** — exists for future GitHub webhooks and admin API |
| **OpenClawMemory** | SSM parameter for AgentCore Memory store ID, IAM policy for memory access |
| **OpenClawScheduler** | Heartbeat Lambda (30-min), memory consolidation Lambda (nightly 02:00 UTC), EventBridge rules, SNS alerts topic |
| **OpenClawBuilder** | Builder agent IAM role with permission boundary (denies IAM/Organizations/Billing actions) |

Dependency order: Storage → Gateway, Api, Memory, Scheduler, Builder. Identity has no dependencies.

---

## Workspace Seeding

After the Storage stack deploys, upload the agent's workspace seed files to S3:

```bash
cd openclaw

BUCKET=$(aws cloudformation describe-stacks \
  --stack-name OpenClawStorage \
  --query 'Stacks[0].Outputs[?contains(OutputKey,`Workspace`)].OutputValue' \
  --profile openclaw-dev \
  --output text)

python3 scripts/seed-workspace.py \
  --bucket "$BUCKET" \
  --tenant-id default-tenant \
  --agent-id default-agent \
  --profile openclaw-dev \
  --region us-east-1
```

This uploads 7 files from `workspace-seeds/` to `s3://<bucket>/default-tenant/default-agent/`: SOUL.md, MEMORY.md, HEARTBEAT.md, AGENTS.md, TOOLS.md, USER.md, IDENTITY.md.

Customize the seed files in `workspace-seeds/` before running if you want a different agent personality.

---

## Gateway Configuration

The Gateway's runtime configuration flows through three stages:

1. **`docker-entrypoint.sh`** — syncs workspace files from S3
2. **`scripts/configure-gateway.mjs`** — reads channel secrets from Secrets Manager, writes `~/.openclaw/openclaw.json`
3. **Gateway starts** — reads `openclaw.json`, registers channels, begins accepting messages

### How `configure-gateway.mjs` Works

The script reads these Secrets Manager secrets (if they exist):

| Secret Name | Config Field |
|-------------|-------------|
| `openclaw/telegram-bot-token` | `channels.telegram.botToken` |
| `openclaw/slack-bot-token` | `channels.slack.botToken` |
| `openclaw/slack-app-token` | `channels.slack.appToken` |
| `openclaw/slack-signing-secret` | `channels.slack.signingSecret` |

If a secret doesn't exist, that channel is omitted from the config. If Secrets Manager is unreachable, a minimal fallback config is written (WebSocket-only mode).

When `PROVIDER=bedrock`, the script also writes the model configuration:
```json
{
  "agents": {
    "defaults": {
      "model": "amazon-bedrock/amazon.nova-lite-v1:0"
    }
  }
}
```

The `dangerouslyAllowHostHeaderOriginFallback: true` setting is always included — required for non-loopback binding on Fargate.

---

## Connecting Telegram

1. Create a bot with [@BotFather](https://t.me/botfather) on Telegram (`/newbot`)
2. Copy the bot token
3. Store it in Secrets Manager:

```bash
aws secretsmanager put-secret-value \
  --secret-id openclaw/telegram-bot-token \
  --secret-string '<YOUR_TELEGRAM_BOT_TOKEN>' \
  --profile openclaw-dev
```

4. Restart the ECS task to pick up the new secret:

```bash
CLUSTER=$(aws ecs list-clusters --profile openclaw-dev --query 'clusterArns[0]' --output text)
SERVICE=$(aws ecs list-services --cluster $CLUSTER --profile openclaw-dev --query 'serviceArns[0]' --output text)
aws ecs update-service --cluster $CLUSTER --service $SERVICE --force-new-deployment --profile openclaw-dev
```

5. Send a message to your bot on Telegram — it should respond via Bedrock

The Gateway uses OpenClaw's native Telegram handler with `dmPolicy: "open"` and `allowFrom: ["*"]`, meaning any Telegram user can message the bot. To restrict access, update the `configure-gateway.mjs` script.

> **No webhook setup needed.** OpenClaw handles Telegram polling/webhook registration internally.

---

## Connecting Slack

1. Create a Slack app at [api.slack.com/apps](https://api.slack.com/apps)
2. Add bot token scopes: `chat:write`, `channels:history`, `groups:history`, `im:history`
3. Install to your workspace
4. Store the tokens in Secrets Manager:

```bash
aws secretsmanager put-secret-value \
  --secret-id openclaw/slack-bot-token \
  --secret-string 'xoxb-YOUR-SLACK-BOT-TOKEN' \
  --profile openclaw-dev
```

5. Restart the ECS task (same command as Telegram above)
6. Invite the bot to a channel and mention it

---

## Changing the AI Model

The default model is `amazon.nova-lite-v1:0`. To change it:

1. Edit `infra/stacks/gateway_stack.py` — update the `BEDROCK_MODEL_ID` environment variable:

```python
"BEDROCK_MODEL_ID": "us.anthropic.claude-sonnet-4-20250514-v1:0",
```

2. Redeploy:

```bash
cd openclaw/infra
cdk deploy OpenClawGateway --profile openclaw-dev
```

The model ID is passed to `configure-gateway.mjs`, which writes it into `openclaw.json` as `amazon-bedrock/<model-id>`.

For cross-region inference, prefix the model ID with `us.` (e.g., `us.anthropic.claude-sonnet-4-20250514-v1:0`).

> **Amazon models** (Nova Lite, Nova Pro) don't require marketplace subscriptions. **Third-party models** (Anthropic Claude) may need marketplace access enabled in the Bedrock console.

---

## Post-Deployment Verification

### Check ECS Gateway Health

```bash
CLUSTER=$(aws ecs list-clusters --profile openclaw-dev --query 'clusterArns[0]' --output text)
SERVICE=$(aws ecs list-services --cluster $CLUSTER --profile openclaw-dev --query 'serviceArns[0]' --output text)

aws ecs describe-services \
  --cluster $CLUSTER --service $SERVICE \
  --profile openclaw-dev \
  --query 'services[0].{desired:desiredCount,running:runningCount,status:status}'
```

Expected: `desired: 1, running: 1, status: ACTIVE`

### Check Gateway Logs

```bash
# Find the log group (CDK auto-names it)
LOG_GROUP=$(aws logs describe-log-groups \
  --log-group-name-prefix /aws/ecs \
  --profile openclaw-dev \
  --query 'logGroups[?contains(logGroupName,`Gateway`)].logGroupName' \
  --output text)

# Tail recent logs
aws logs tail "$LOG_GROUP" --since 30m --profile openclaw-dev --follow
```

Look for:
- `[entrypoint] Workspace sync complete` — S3 sync succeeded
- `Gateway config generated successfully` — Secrets Manager read succeeded
- `Starting OpenClaw gateway...` — Gateway process starting
- Telegram/Slack connection messages if channels are configured

### CloudWatch Dashboard

```bash
echo "https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=OpenClaw"
```

The dashboard shows: messages per hour, Bedrock token usage, memory operations, active sessions, error rate, and builder deployments.

### CloudWatch Alarms

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix openclaw- \
  --profile openclaw-dev \
  --query 'MetricAlarms[*].{Name:AlarmName,State:StateValue}' \
  --output table
```

All alarms should be `OK` or `INSUFFICIENT_DATA` (normal before metrics start flowing).

---

## Environment Variables Reference

Set on the ECS task definition in `gateway_stack.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE_BUCKET` | *(from Storage stack)* | S3 bucket for workspace files |
| `TENANT_ID` | `default-tenant` | Tenant identifier for S3 prefix |
| `AGENT_ID` | `default-agent` | Agent identifier for S3 prefix |
| `PROVIDER` | `bedrock` | Model provider (`bedrock` or `anthropic` for local) |
| `BEDROCK_MODEL_ID` | `amazon.nova-lite-v1:0` | Bedrock model ID |
| `TELEGRAM_SECRET_NAME` | `openclaw/telegram-bot-token` | Secrets Manager secret name for Telegram |
| `OPENCLAW_ALLOW_INSECURE_PRIVATE_WS` | `1` | Allow private workspace access |

Set by `docker-entrypoint.sh`:

| Variable | Description |
|----------|-------------|
| `OPENCLAW_CONFIG_DIR` | Path to config directory (`~/.openclaw`) |
| `OPENCLAW_GATEWAY_BIND` | Network bind mode (default: `lan`) |
| `OPENCLAW_GATEWAY_PORT` | Gateway port (default: `18789`) |

---

## Cost Estimates

Monthly estimates for a single-agent deployment in `us-east-1` (~100 conversations/day):

| Resource | Estimated Cost |
|----------|---------------|
| ECS Fargate (1024 CPU, 2048 MiB, 24/7) | ~$35/month |
| NAT Gateway | ~$32/month |
| DynamoDB (on-demand, 5 tables) | ~$2–5/month |
| S3 (workspace + artifacts) | <$1/month |
| Lambda (heartbeat + consolidation) | <$1/month |
| CloudWatch (logs, dashboard, alarms) | ~$3–5/month |
| Secrets Manager (3 secrets) | ~$1.20/month |
| Other (SNS, EventBridge, Cognito free tier) | <$2/month |
| **Infrastructure subtotal** | **~$75–80/month** |
| **+ Bedrock Nova Lite** (~100 conv/day) | **+$5–15/month** |
| **+ Bedrock Claude Sonnet** (~100 conv/day) | **+$50–150/month** |

> The NAT Gateway is the largest fixed cost (~$32/month). To reduce costs, consider VPC endpoints for S3 and Bedrock, or deploy the Fargate task with a public IP in a public subnet.

### Cost Optimization

- Use Nova Lite for everyday tasks (90%+ cheaper than Claude)
- Set DynamoDB TTL on sessions table to avoid unbounded growth
- Review CloudWatch log retention (default: 2 weeks)
- Consider Fargate Savings Plans if running 24/7

---

## Troubleshooting

### 1. Docker Build: `node:22-slim` Missing AWS CLI

**Problem:** The slim Node.js base image doesn't include `curl`, `unzip`, or AWS CLI — S3 workspace sync fails at startup.

**Fix:** `Dockerfile.gateway` uses `node:24-bookworm` (full image) and installs AWS CLI explicitly. Don't switch to `-slim` or `-alpine` variants.

### 2. Docker Build: No App Code in Container

**Problem:** Container starts but OpenClaw binary is missing — `exec: openclaw.mjs: not found`.

**Fix:** `Dockerfile.gateway` uses a multi-stage build. Stage 1 runs `pnpm install` + `pnpm build:docker`, Stage 2 copies the built artifacts. Ensure the `COPY --from=runtime-assets` directives include all required directories (`dist/`, `node_modules/`, `openclaw.mjs`, `extensions/`, `scripts/`, etc.).

### 3. CDK Deploy: `ENAMETOOLONG` During Asset Staging

**Problem:** `cdk deploy` fails with `ENAMETOOLONG` because CDK tries to recursively copy `cdk.out/` (which contains the Docker image layers) into itself.

**Fix:** The `gateway_stack.py` excludes `infra/` from the Docker asset:
```python
gateway_image = ecs.ContainerImage.from_asset(
    _PROJECT_ROOT,
    file="Dockerfile.gateway",
    exclude=["infra", ".git", ".kiro", "node_modules", ...],
)
```

### 4. Docker Build: `pnpm install --frozen-lockfile` Fails

**Problem:** The lockfile doesn't match `package.json` exactly (common after upstream merges), causing `--frozen-lockfile` to fail in Docker.

**Fix:** Use `--no-frozen-lockfile` in the Dockerfile:
```dockerfile
RUN pnpm install --no-frozen-lockfile
```

### 5. Docker Build: `pnpm prune` CI/TTY Catch-22

**Problem:** `pnpm prune --prod` prompts for confirmation in non-TTY environments, but setting `CI=true` enables `--frozen-lockfile` which conflicts with the modified lockfile.

**Fix:** Use both flags together:
```dockerfile
RUN CI=true pnpm prune --prod --config.frozen-lockfile=false
```

### 6. Gateway: Refuses Non-Loopback Binding

**Problem:** Gateway starts but rejects connections because it's bound to `127.0.0.1` only. On Fargate, the health check and external traffic come from the container's LAN IP.

**Fix:** The `openclaw.json` config includes:
```json
{
  "gateway": {
    "controlUi": {
      "dangerouslyAllowHostHeaderOriginFallback": true
    }
  }
}
```
And the entrypoint starts with `--bind lan`.

### 7. CloudWatch: Orphaned Log Groups Block Redeployment

**Problem:** A previous failed deployment created a CloudWatch log group with a hardcoded name. CDK tries to create the same name and fails with `ResourceAlreadyExistsException`.

**Fix:** Don't hardcode `log_group_name` in the CDK stack. Let CloudWatch auto-name it:
```python
self.log_group = logs.LogGroup(
    self, "GatewayLogGroup",
    retention=logs.RetentionDays.TWO_WEEKS,
    # No log_group_name — CDK generates a unique name
)
```
If you hit this, manually delete the orphaned log group in the CloudWatch console, then redeploy.

### 8. Telegram: Signature Verification Failing (Webhook Lambda)

**Problem:** If you try to route Telegram messages through a custom webhook Lambda, Telegram's signature verification fails because the Lambda doesn't implement Telegram's verification protocol correctly.

**Fix:** Don't use a custom webhook Lambda for Telegram. OpenClaw handles Telegram natively — it registers its own webhook (or uses polling) and verifies messages internally. Just provide the bot token in `openclaw.json` via Secrets Manager.

### 9. OpenClaw Config: `dmPolicy "allow"` Invalid

**Problem:** Setting `dmPolicy: "allow"` in the Telegram channel config causes a validation error at Gateway startup.

**Fix:** Use `"open"` (not `"allow"`):
```json
{
  "channels": {
    "telegram": {
      "botToken": "...",
      "dmPolicy": "open",
      "allowFrom": ["*"]
    }
  }
}
```

### 10. Bedrock: Model Access Denied

**Problem:** `AccessDeniedException: You don't have access to the model with the specified model ID.`

**Fix:** Two possible causes:
- **Amazon models** (Nova Lite, Nova Pro): These should work out of the box. Verify the model ID is correct and the region supports it.
- **Third-party models** (Anthropic Claude): Require marketplace access. Go to Amazon Bedrock → Model access → Request access. The ECS task role includes `aws-marketplace:ViewSubscriptions` and `aws-marketplace:Subscribe` permissions, but you still need to accept the EULA in the console.

If using Amazon Nova models, no marketplace permissions are needed.

### General Debugging

```bash
# ECS exec into the running container
CLUSTER=$(aws ecs list-clusters --profile openclaw-dev --query 'clusterArns[0]' --output text)
TASK=$(aws ecs list-tasks --cluster $CLUSTER --profile openclaw-dev --query 'taskArns[0]' --output text)

aws ecs execute-command \
  --cluster $CLUSTER \
  --task $TASK \
  --container GatewayContainer \
  --interactive \
  --command "/bin/bash" \
  --profile openclaw-dev

# Inside the container:
cat ~/.openclaw/openclaw.json          # Check generated config
ls -la /home/node/.openclaw/workspace/ # Check synced workspace files
aws s3 ls s3://$WORKSPACE_BUCKET/default-tenant/default-agent/  # Check S3 files
```

---

## Cleanup

Remove all deployed resources:

```bash
cd openclaw/infra
cdk destroy --all --profile openclaw-dev
```

CDK prompts for confirmation before deleting each stack. S3 buckets and DynamoDB tables are configured with `RemovalPolicy.DESTROY` and will be deleted automatically.

To delete specific stacks:
```bash
cdk destroy OpenClawBuilder OpenClawScheduler OpenClawMemory --profile openclaw-dev
```

> **Note:** Secrets Manager secrets have a recovery window. To delete them immediately, use `--force-delete-without-recovery` in the AWS CLI after stack deletion.
