# OpenClaw AWS Extension — Deployment Guide

This guide covers local testing and full AWS deployment of the OpenClaw AWS Extension using CDK.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Local Testing](#local-testing)
- [AWS Deployment](#aws-deployment)
- [Workspace Seeding](#workspace-seeding)
- [Memory Migration](#memory-migration)
- [Post-Deployment Verification](#post-deployment-verification)
- [Connecting Messaging Platforms](#connecting-messaging-platforms)
- [Cost Estimates](#cost-estimates)
- [Cleanup](#cleanup)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Tools

| Tool | Version | Install |
|------|---------|---------|
| Node.js | 22+ | [nvm](https://github.com/nvm-sh/nvm) or [nodejs.org](https://nodejs.org) |
| Python | 3.12+ | [python.org](https://www.python.org/downloads/) |
| AWS CLI | 2.x | [AWS CLI Install](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |
| AWS CDK | 2.150+ | `npm install -g aws-cdk` |
| Docker | 24+ | [docker.com](https://docs.docker.com/get-docker/) (required for CDK asset bundling) |

### AWS Configuration

```bash
# Configure your AWS profile
aws configure --profile openclaw-dev
# Access Key ID: <your-key>
# Secret Access Key: <your-secret>
# Region: us-east-1
# Output: json

# Verify credentials
aws sts get-caller-identity --profile openclaw-dev
```

### Required IAM Permissions

The deploying user/role needs permissions for:
- CloudFormation (full stack management)
- S3, DynamoDB, Lambda, ECS, ECR, API Gateway, EventBridge, SNS
- IAM (role/policy creation)
- Cognito, Secrets Manager, SSM Parameter Store
- CloudWatch (dashboards, alarms, log groups)
- Bedrock (model invocation)

A user with `AdministratorAccess` will work. For production, scope down to the specific services above.

---

## Local Testing

Local mode runs OpenClaw with `PROVIDER=anthropic`, bypassing all AWS adapters. This is useful for verifying the Gateway starts and responds before deploying to AWS.

### Quick Start

```bash
cd openclaw

# Install Node.js dependencies
npm install

# Run the local verification script
chmod +x scripts/test-local.sh
./scripts/test-local.sh
```

The script:
1. Starts the OpenClaw Gateway on `ws://127.0.0.1:18789`
2. Waits up to 60 seconds for the port to become reachable
3. Verifies the WebSocket handshake (expects HTTP 101)
4. Exits 0 on success

### Manual Local Run

```bash
# Start in local/anthropic mode (no AWS adapters)
PROVIDER=anthropic OPENCLAW_SKIP_CHANNELS=1 node scripts/run-node.mjs

# In another terminal, test the WebSocket
curl -s -o /dev/null -w "%{http_code}" \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: dGVzdA==" \
  http://127.0.0.1:18789/
# Expected: 101
```

---

## AWS Deployment

The extension deploys 7 CDK stacks:

| Stack | Resources |
|-------|-----------|
| **OpenClawStorage** | S3 buckets (workspace, skills, artifacts), DynamoDB tables (memory, sessions, agents, dedup, connections) |
| **OpenClawGateway** | VPC, ECS Fargate cluster, Gateway task definition and service, CloudWatch log group |
| **OpenClawIdentity** | Cognito user pool, Secrets Manager secrets (Telegram, Slack, GitHub tokens) |
| **OpenClawApi** | HTTP API Gateway, WebSocket API Gateway, webhook Lambda, SQS queue |
| **OpenClawMemory** | SSM parameter for memory store ID, IAM policy for AgentCore Memory access |
| **OpenClawScheduler** | Heartbeat Lambda (30-min), consolidation Lambda (nightly 02:00 UTC), EventBridge rules, SNS alerts topic |
| **OpenClawBuilder** | Builder agent IAM role with permission boundary (denies IAM/Organizations/Billing) |

### Step 1: Install CDK Dependencies

```bash
cd openclaw/infra

# Create a Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install CDK dependencies
pip install -r requirements.txt
```

### Step 2: Bootstrap CDK (first time only)

```bash
# Bootstrap CDK in your target account/region
cdk bootstrap aws://<ACCOUNT_ID>/us-east-1 --profile openclaw-dev
```

Replace `<ACCOUNT_ID>` with your AWS account number. You can find it with:
```bash
aws sts get-caller-identity --profile openclaw-dev --query Account --output text
```

### Step 3: Synthesize (optional, validates templates)

```bash
cd openclaw/infra
cdk synth --profile openclaw-dev
```

This generates CloudFormation templates in `cdk.out/` without deploying. Useful for reviewing what will be created.

### Step 4: Deploy All Stacks

```bash
cd openclaw/infra
cdk deploy --all --profile openclaw-dev --require-approval broadening
```

CDK deploys stacks in dependency order:
1. `OpenClawStorage` (no dependencies)
2. `OpenClawIdentity` (no dependencies)
3. `OpenClawGateway` (depends on Storage)
4. `OpenClawApi` (depends on Storage, Identity)
5. `OpenClawMemory` (depends on Storage)
6. `OpenClawScheduler` (depends on Storage)
7. `OpenClawBuilder` (depends on Storage)

Deployment takes approximately 8–12 minutes. The `--require-approval broadening` flag prompts you before creating IAM resources.

### Step 5: Note Stack Outputs

After deployment, CDK prints outputs. Save these values:

```bash
# Get the workspace bucket name
aws cloudformation describe-stacks \
  --stack-name OpenClawStorage \
  --query 'Stacks[0].Outputs' \
  --profile openclaw-dev \
  --output table

# Get the API endpoint
aws cloudformation describe-stacks \
  --stack-name OpenClawApi \
  --query 'Stacks[0].Outputs' \
  --profile openclaw-dev \
  --output table

# Get the memory store parameter
aws cloudformation describe-stacks \
  --stack-name OpenClawMemory \
  --query 'Stacks[0].Outputs' \
  --profile openclaw-dev \
  --output table

# Get the builder role ARN
aws cloudformation describe-stacks \
  --stack-name OpenClawBuilder \
  --query 'Stacks[0].Outputs' \
  --profile openclaw-dev \
  --output table
```

---

## Workspace Seeding

After deploying the Storage stack, upload the workspace seed files to S3. These templates define the agent's personality, memory structure, heartbeat behavior, and tool configuration.

```bash
cd openclaw

# Get the workspace bucket name from the Storage stack
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name OpenClawStorage \
  --query 'Stacks[0].Outputs[?contains(OutputKey,`Workspace`)].OutputValue' \
  --profile openclaw-dev \
  --output text)

# Seed workspace files
python3 scripts/seed-workspace.py \
  --bucket "$BUCKET" \
  --tenant-id default-tenant \
  --agent-id default-agent \
  --profile openclaw-dev \
  --region us-east-1
```

This uploads 7 workspace files to `s3://<bucket>/default-tenant/default-agent/`:
- `SOUL.md` — Agent personality and behavior
- `MEMORY.md` — Memory structure and retention rules
- `HEARTBEAT.md` — Heartbeat schedule and self-reflection prompts
- `AGENTS.md` — Multi-agent orchestration config
- `TOOLS.md` — Available tools and capabilities
- `USER.md` — User preferences and context
- `IDENTITY.md` — Agent identity and authentication

You can customize these files in `workspace-seeds/` before seeding.

---

## Memory Migration

If you have an existing local OpenClaw installation with memory files, migrate them to AgentCore Memory:

```bash
# First, get or create the memory store ID
# (Check the SSM parameter after deployment)
MEMORY_STORE_ID=$(aws ssm get-parameter \
  --name /openclaw/memory-store-id \
  --query Parameter.Value \
  --profile openclaw-dev \
  --output text)

# Run migration
python3 scripts/migrate-memory.py \
  --memory-store-id "$MEMORY_STORE_ID" \
  --agent-id default-agent \
  --workspace-path /path/to/local/openclaw/workspace \
  --profile openclaw-dev \
  --region us-east-1
```

The migration script ingests:
- `MEMORY.md` — Main memory file
- `memory/YYYY-MM-DD.md` — Daily log files (if any exist)

> Note: The SSM parameter `/openclaw/memory-store-id` starts as `PLACEHOLDER`. You need to create the AgentCore Memory store first (via the AgentCoreMemoryAdapter or AWS Console) and update the parameter with the actual store ID.

---

## Post-Deployment Verification

### 1. Check Stack Status

```bash
# All stacks should show CREATE_COMPLETE
for stack in OpenClawStorage OpenClawGateway OpenClawIdentity OpenClawApi OpenClawMemory OpenClawScheduler OpenClawBuilder; do
  echo -n "$stack: "
  aws cloudformation describe-stacks \
    --stack-name $stack \
    --query 'Stacks[0].StackStatus' \
    --profile openclaw-dev \
    --output text 2>/dev/null || echo "NOT_FOUND"
done
```

### 2. Verify ECS Gateway Service

```bash
# Check the Gateway service is running
aws ecs list-services \
  --cluster $(aws ecs list-clusters --profile openclaw-dev --query 'clusterArns[0]' --output text) \
  --profile openclaw-dev \
  --output table

# Check task health
aws ecs describe-services \
  --cluster $(aws ecs list-clusters --profile openclaw-dev --query 'clusterArns[0]' --output text) \
  --services $(aws ecs list-services --cluster $(aws ecs list-clusters --profile openclaw-dev --query 'clusterArns[0]' --output text) --profile openclaw-dev --query 'serviceArns[0]' --output text) \
  --profile openclaw-dev \
  --query 'services[0].{desired:desiredCount,running:runningCount,status:status}'
```

### 3. Check CloudWatch Dashboard

Open the CloudWatch console and navigate to the "OpenClaw" dashboard. It shows:
- Messages per hour
- Bedrock token usage (input/output)
- Memory operations per hour
- Active sessions
- Error rate (%)
- Builder deployments per hour

```bash
# Direct link (replace region if needed)
echo "https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=OpenClaw"
```

### 4. Verify Heartbeat

The heartbeat Lambda fires every 30 minutes. Check its execution:

```bash
# Check recent heartbeat invocations
aws logs filter-log-events \
  --log-group-name /aws/lambda/openclaw-heartbeat-handler \
  --start-time $(date -d '1 hour ago' +%s000) \
  --profile openclaw-dev \
  --query 'events[*].message' \
  --output text | head -20
```

### 5. Check CloudWatch Alarms

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix openclaw- \
  --profile openclaw-dev \
  --query 'MetricAlarms[*].{Name:AlarmName,State:StateValue}' \
  --output table
```

All alarms should be in `OK` or `INSUFFICIENT_DATA` state (INSUFFICIENT_DATA is normal before metrics start flowing).

---

## Connecting Messaging Platforms

The OpenClaw AWS Extension supports Telegram, Slack, and WebSocket channels. Platform tokens are stored in Secrets Manager.

### Telegram

1. Create a bot with [@BotFather](https://t.me/botfather) on Telegram (`/newbot`)
2. Copy the bot token
3. Store it in Secrets Manager:

```bash
aws secretsmanager put-secret-value \
  --secret-id openclaw/telegram-bot-token \
  --secret-string '{"token":"<YOUR_TELEGRAM_BOT_TOKEN>"}' \
  --profile openclaw-dev
```

4. Set the webhook URL to point to your API Gateway:

```bash
API_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name OpenClawApi \
  --query 'Stacks[0].Outputs[?contains(OutputKey,`HttpApi`)].OutputValue' \
  --profile openclaw-dev \
  --output text)

curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{\"url\": \"${API_ENDPOINT}/webhook/telegram\"}"
```

5. Send `/start` to your bot on Telegram to verify

### Slack

1. Create a Slack app at [api.slack.com/apps](https://api.slack.com/apps)
2. Add bot token scopes: `chat:write`, `channels:history`, `groups:history`, `im:history`
3. Install to your workspace and copy the Bot User OAuth Token (`xoxb-...`)
4. Store it in Secrets Manager:

```bash
aws secretsmanager put-secret-value \
  --secret-id openclaw/slack-bot-token \
  --secret-string '{"token":"xoxb-YOUR-SLACK-BOT-TOKEN"}' \
  --profile openclaw-dev
```

5. Configure the Slack Event Subscriptions URL to: `<API_ENDPOINT>/webhook/slack`
6. Invite the bot to a channel and mention it to verify

### WebSocket

The WebSocket API Gateway is available at the endpoint output from the API stack:

```bash
aws cloudformation describe-stacks \
  --stack-name OpenClawApi \
  --query 'Stacks[0].Outputs[?contains(OutputKey,`WebSocket`)].OutputValue' \
  --profile openclaw-dev \
  --output text
```

Connect using any WebSocket client:
```bash
# Using wscat (npm install -g wscat)
wscat -c "wss://<websocket-api-id>.execute-api.us-east-1.amazonaws.com/production"
```

---

## Environment Variables Reference

The Gateway bootstrap adapter (`adapters/gateway-bootstrap.ts`) reads these environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PROVIDER` | No | `bedrock` | `bedrock` for AWS mode, `anthropic` for local mode |
| `WORKSPACE_BUCKET` | Yes (bedrock) | — | S3 bucket name from Storage stack |
| `TENANT_ID` | Yes (bedrock) | — | Tenant identifier (e.g., `default-tenant`) |
| `AGENT_ID` | No | `default-agent` | Agent identifier |
| `MEMORY_STORE_ID` | No | — | AgentCore Memory store ID |
| `GUARDRAIL_ID` | No | `""` | Bedrock Guardrail identifier |
| `GUARDRAIL_VERSION` | No | `1` | Bedrock Guardrail version |
| `AWS_REGION` | No | SDK default | AWS region |

Lambda functions use additional variables set by their CDK stacks:

| Variable | Used By | Description |
|----------|---------|-------------|
| `DEDUP_TABLE_NAME` | Webhook Lambda | DynamoDB dedup table name |
| `SQS_QUEUE_URL` | Webhook Lambda | SQS queue URL for message forwarding |
| `MEMORY_TABLE_NAME` | Consolidation Lambda | DynamoDB memory table name |
| `SNS_TOPIC_ARN` | Heartbeat, Consolidation | SNS topic for alerts |

---

## Cost Estimates

Estimated monthly costs for a single-agent deployment in `us-east-1` with moderate usage (~100 conversations/day):

| Resource | Service | Estimated Cost |
|----------|---------|---------------|
| ECS Fargate (0.25 vCPU, 0.5 GB) | Gateway | ~$9/month |
| NAT Gateway | Networking | ~$32/month |
| DynamoDB (on-demand, 5 tables) | Storage | ~$2–5/month |
| S3 (workspace + artifacts) | Storage | <$1/month |
| Lambda (heartbeat + consolidation + webhook) | Compute | <$1/month |
| API Gateway (HTTP + WebSocket) | API | ~$1–3/month |
| EventBridge | Scheduling | <$1/month |
| CloudWatch (logs, dashboard, alarms) | Observability | ~$3–5/month |
| SNS | Notifications | <$1/month |
| Cognito | Identity | Free tier (first 50k MAU) |
| Secrets Manager (3 secrets) | Identity | ~$1.20/month |
| **Infrastructure subtotal** | | **~$50–60/month** |
| Bedrock — Nova 2 Lite | AI (100 conv/day) | ~$5–15/month |
| Bedrock — Claude Sonnet 4.5 | AI (100 conv/day) | ~$50–150/month |
| **Total (Nova 2 Lite)** | | **~$55–75/month** |
| **Total (Claude Sonnet 4.5)** | | **~$100–210/month** |

> The NAT Gateway is the largest fixed cost. To reduce costs, consider using VPC endpoints for S3 and Bedrock instead, or deploying the Fargate task with a public IP in a public subnet (less secure).

### Cost Optimization Tips

- Use Nova 2 Lite for everyday tasks (90% cheaper than Claude)
- Set DynamoDB TTL on sessions and dedup tables to avoid unbounded growth
- Review CloudWatch log retention (default: 2 weeks)
- Use Savings Plans for Fargate if running 24/7

---

## Cleanup

Remove all deployed resources:

```bash
cd openclaw/infra

# Destroy all stacks (reverse dependency order)
cdk destroy --all --profile openclaw-dev
```

CDK will prompt for confirmation before deleting each stack. S3 buckets and DynamoDB tables are configured with `RemovalPolicy.DESTROY` and will be deleted automatically.

To delete only specific stacks:
```bash
cdk destroy OpenClawBuilder OpenClawScheduler OpenClawMemory --profile openclaw-dev
```

---

## Troubleshooting

### CDK Synth Fails

```
Error: Cannot find module 'constructs'
```

The `infra/constructs/` directory shadows the pip `constructs` package. The `infra/app.py` has a `sys.path` workaround for this. Make sure you're running `cdk synth` from the `infra/` directory and that `aws-cdk-lib` and `constructs` are installed in your virtual environment:

```bash
cd openclaw/infra
source .venv/bin/activate
pip install -r requirements.txt
```

### ECS Task Keeps Restarting

Check the task logs:
```bash
aws logs tail /ecs/openclaw-gateway --since 30m --profile openclaw-dev
```

Common causes:
- Missing `WORKSPACE_BUCKET` environment variable
- S3 bucket is empty (run workspace seeding first)
- Bedrock model access not enabled in the target region

### Bedrock Access Denied

```
AccessDeniedException: You don't have access to the model
```

Enable model access in the Bedrock console:
1. Go to Amazon Bedrock → Model access
2. Request access to the models you want to use (Nova 2 Lite, Claude Sonnet, etc.)
3. Wait for approval (usually instant for Nova models)

### Webhook Not Receiving Messages

1. Verify the API Gateway endpoint is correct:
```bash
aws cloudformation describe-stacks \
  --stack-name OpenClawApi \
  --query 'Stacks[0].Outputs' \
  --profile openclaw-dev
```

2. Check webhook Lambda logs:
```bash
aws logs tail /aws/lambda/openclaw-webhook-handler --since 30m --profile openclaw-dev
```

3. Verify the Telegram/Slack webhook URL is set correctly

### Heartbeat Not Firing

Check the EventBridge rule:
```bash
aws events describe-rule \
  --name openclaw-heartbeat-30min \
  --profile openclaw-dev
```

Check the heartbeat Lambda logs:
```bash
aws logs tail /aws/lambda/openclaw-heartbeat-handler --since 2h --profile openclaw-dev
```

### Memory Store Not Found

The SSM parameter `/openclaw/memory-store-id` starts as `PLACEHOLDER`. Create the memory store:

1. Use the AWS Console → Amazon Bedrock → AgentCore → Memory stores
2. Create a new memory store
3. Update the SSM parameter:
```bash
aws ssm put-parameter \
  --name /openclaw/memory-store-id \
  --value "<YOUR_MEMORY_STORE_ID>" \
  --type String \
  --overwrite \
  --profile openclaw-dev
```
