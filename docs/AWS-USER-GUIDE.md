# OpenClaw on AWS — User Guide

A comprehensive guide to the OpenClaw AWS deployment: architecture, capabilities, operations, and security.

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Execution Paths](#execution-paths)
- [Using the Agent via Telegram](#using-the-agent-via-telegram)
- [Skills](#skills)
- [Role Factory](#role-factory)
- [Security Model](#security-model)
- [ACPX / MCP Bridge](#acpx--mcp-bridge)
- [Adding New AgentCore Gateway Tools](#adding-new-agentcore-gateway-tools)
- [Cost Expectations](#cost-expectations)
- [Monitoring and Observability](#monitoring-and-observability)
- [Common Operations](#common-operations)

---

## Architecture Overview

OpenClaw runs as a single ECS Fargate task in `us-east-1`. The Gateway process handles messaging, agent sessions, and tool execution inside a Docker container built from `Dockerfile.gateway`.

### What's Deployed (9 CDK Stacks)

| Stack | Purpose | Status |
|-------|---------|--------|
| OpenClawStorage | S3 buckets (workspace, skills, artifacts) + 5 DynamoDB tables | Fully wired |
| OpenClawGateway | VPC, ECS Fargate, Docker image, task role with broad permissions | Fully wired |
| OpenClawIdentity | Cognito user pool + Secrets Manager secrets | Fully wired |
| OpenClawApi | HTTP/WebSocket API Gateway + webhook Lambda + SQS | Deployed, not in messaging path |
| OpenClawMemory | SSM parameter for AgentCore Memory store ID | Placeholder |
| OpenClawScheduler | EventBridge rules + Lambda handlers | Deployed, Lambda stubs |
| OpenClawBuilder | Builder agent IAM role | Deployed, not yet used |
| OpenClawAgentCoreTools | Lambda for `deploy_static_site` MCP tool | Partially wired |
| OpenClawComputeEnvironments | DynamoDB tables, cleanup Lambda, permission boundary, SNS | Fully wired (Phase 1) |

### Data Flow

1. User sends a message via Telegram
2. OpenClaw Gateway receives it natively (polling/webhook)
3. Gateway routes to the embedded agent runtime (Claude Sonnet via Bedrock)
4. Agent uses built-in tools (exec, read, write) or enters ACPX for MCP tools
5. Response flows back through the Gateway to Telegram

### Workspace

The agent's workspace is synced from S3 at container startup. Key files:

| File | Purpose |
|------|---------|
| `SOUL.md` | Persona, values, execution path decision guide |
| `AGENTS.md` | Operating instructions, memory guidance |
| `MEMORY.md` | Curated long-term memory |
| `HEARTBEAT.md` | Periodic check-in checklist |
| `skills/` | Custom SKILL.md files (aws-infrastructure, acpx-mcp-tools, role-factory) |

---

## Execution Paths

The agent has three ways to execute tasks. The choice depends on what's needed.

### Path 1: Direct Host Execution (default, fastest)

The agent uses its built-in `exec` tool to run AWS CLI commands directly on the Fargate host. No sandbox overhead.

Best for: AWS CLI queries, file operations, quick shell commands.

```
User: "List my S3 buckets"
Agent: exec → aws s3 ls
```

### Path 2: ACPX Session (for MCP Gateway tools)

The agent enters an ACPX coding session to access AgentCore Gateway MCP tools. MCP tools only work inside ACPX sessions.

Best for: Structured Gateway tools like `deploy_static_site`.

```
User: "Deploy my website to CloudFront"
Agent: enters ACPX → calls deploy_static_site MCP tool
```

### Path 3: Remote Delegation (for heavy execution)

The agent delegates heavy tasks to remote AWS compute via `exec` + AWS CLI.

Best for: CDK deployments (CodeBuild), GPU workloads (EC2), persistent services (ECS Fargate).

```
User: "Deploy this CDK stack"
Agent: exec → aws codebuild start-build ...
```

### Decision Quick Reference

| Task | Path |
|------|------|
| AWS CLI query (list, describe) | Path 1: `exec` |
| File read/write on host | Path 1: `read`/`write` |
| Gateway MCP tool call | Path 2: ACPX session |
| CDK deploy or Docker build | Path 3: CodeBuild |
| GPU or long-running task | Path 3: EC2 |
| Persistent API service | Path 3: ECS Fargate |

---

## Using the Agent via Telegram

### Setup

1. Create a bot with [@BotFather](https://t.me/botfather) (`/newbot`)
2. Store the token in Secrets Manager:
   ```bash
   aws secretsmanager put-secret-value \
     --secret-id openclaw/telegram-bot-token \
     --secret-string '<BOT_TOKEN>' \
     --profile openclaw-dev
   ```
3. Restart the ECS task to pick up the secret

### Interaction

Send messages directly to your bot on Telegram. The agent responds via Bedrock (Claude Sonnet). No webhook setup needed — OpenClaw handles Telegram natively.

### Access Control

The `configure-gateway.mjs` script sets `dmPolicy: "allowlist"` with a specific Telegram user ID. To change who can message the bot, update the `allowFrom` array in the script and redeploy the Gateway.

---

## Skills

Three custom SKILL.md files are loaded from `workspace-seeds/skills/`:

### aws-infrastructure
Teaches the agent to use AWS CLI via the built-in `exec` tool. Covers S3, CloudFormation, EC2, and general resource discovery patterns.

### acpx-mcp-tools
Teaches the agent when to enter ACPX coding sessions for AgentCore Gateway MCP tools vs. using direct CLI execution.

### role-factory
Teaches the agent to create task-scoped IAM roles with elevated permissions when the base ECS task role is insufficient.

Skills are injected into the agent's system prompt as compact XML. They provide guidance only — they don't control tool availability.

---

## Role Factory

The role factory allows the agent to create temporary IAM roles for tasks that need permissions beyond the base ECS task role.

### How It Works

1. Agent creates a role with `agent-task-` prefix + mandatory permission boundary
2. Agent attaches an inline policy with specific permissions
3. Agent assumes the role via STS for temporary credentials
4. Cleanup Lambda deletes expired roles automatically

### Constraints

- Role name prefix: `agent-task-` (enforced by IAM policy)
- Permission boundary: `agent-permission-boundary` (enforced by IAM condition on `CreateRole`)
- Maximum concurrent roles: 5
- Role lifetime: 24 hours (auto-cleanup every 6 hours)
- STS session: default 1 hour, max 4 hours

### What the Permission Boundary Denies

- `iam:*` (except scoped `agent-task-*` role operations)
- `organizations:*`
- `account:*`
- `aws-portal:*`, `budgets:*`, `ce:*`, `cur:*`

### Tracking

Roles are tracked in the `openclaw-agent-roles` DynamoDB table. The cleanup Lambda (`openclaw-environment-cleanup`) scans this table on a 6-hour schedule.

---

## Security Model

### Permission Layers

1. **ECS Task Role** — base permissions for the Gateway container (Bedrock, S3, DynamoDB, Secrets Manager, scoped IAM)
2. **Permission Boundary** — hard ceiling on what agent-created roles can do (denies IAM, Organizations, Billing)
3. **Task-Scoped Roles** — temporary elevated permissions created by the agent, always constrained by the boundary
4. **Cleanup Lambda** — automated garbage collection for expired environments (30min) and roles (6hr)

### IAM Guardrails

- `iam:CreateRole` requires the `iam:PermissionsBoundary` condition — the agent cannot create unbounded roles
- All role operations are scoped to `arn:aws:iam::*:role/agent-task-*`
- The agent cannot modify the permission boundary or its own task role
- Even with `AdministratorAccess` attached to an agent-created role, the boundary limits effective permissions

### Network

- Fargate task runs in a VPC with 2 AZs and 1 NAT Gateway
- Public IP assigned for outbound internet access
- ECS Exec enabled for debugging (`aws ecs execute-command`)

---

## ACPX / MCP Bridge

ACPX (coding agent sandbox) provides isolated MCP tool access. The MCP bridge connects to AgentCore Gateway for structured tool invocations.

### Configuration

The bridge is configured automatically by `scripts/configure-gateway.mjs`:
- Reads OAuth2 credentials from `openclaw/agentcore-gateway-credentials` in Secrets Manager
- Writes config to `plugins.entries.acpx.config.mcpServers` in `openclaw.json`
- ACPX must be explicitly enabled (`enabled: true`) — it's a bundled extension disabled by default

### Plugin Config Path

Plugin config uses `plugins.entries.<id>.config`, not `plugins.<id>`. The `entries` intermediate key is required by OpenClaw's config schema.

---

## Adding New AgentCore Gateway Tools

To add a new MCP tool accessible via the ACPX bridge:

1. Create a Lambda function in `lambdas/` with the tool logic
2. Add the Lambda to `infra/stacks/agentcore_gateway_stack.py`
3. Deploy: `cd infra && cdk deploy OpenClawAgentCoreTools --profile openclaw-dev`
4. Register the Lambda as an AgentCore Gateway target via the agentcore CLI
5. The MCP bridge automatically discovers new Gateway targets

The agent can then call the new tool from within ACPX coding sessions.

---

## Cost Expectations

Monthly estimates for a single-agent deployment in `us-east-1` (~100 conversations/day):

| Resource | Estimated Cost |
|----------|---------------|
| ECS Fargate (1024 CPU, 2048 MiB, 24/7) | ~$35/month |
| NAT Gateway | ~$32/month |
| DynamoDB (on-demand, 9 tables) | ~$3–8/month |
| S3 (workspace + skills + artifacts) | <$1/month |
| Lambda (heartbeat + consolidation + cleanup) | <$1/month |
| CloudWatch (logs, metrics) | ~$3–5/month |
| Secrets Manager | ~$1.20/month |
| Other (SNS, EventBridge, Cognito free tier) | <$2/month |
| **Infrastructure subtotal** | **~$75–85/month** |
| **+ Bedrock Claude Sonnet** (~100 conv/day) | **+$50–150/month** |

The NAT Gateway is the largest fixed cost. To reduce it, consider VPC endpoints for S3 and Bedrock, or deploy the Fargate task with a public IP in a public subnet (already configured).

---

## Monitoring and Observability

### CloudWatch Logs

The Gateway container logs to a CDK-managed CloudWatch log group. Tail logs:

```bash
LOG_GROUP=$(aws logs describe-log-groups \
  --log-group-name-prefix /aws/ecs \
  --profile openclaw-dev \
  --query 'logGroups[?contains(logGroupName,`Gateway`)].logGroupName' \
  --output text)

aws logs tail "$LOG_GROUP" --since 30m --profile openclaw-dev --follow
```

The cleanup Lambda logs to `/aws/lambda/openclaw-environment-cleanup`.
The deploy-static-site Lambda logs to `/aws/lambda/openclaw-deploy-static-site`.

### DynamoDB Tables

| Table | What It Tracks |
|-------|---------------|
| `openclaw-memory` | Agent memory entries (PK: agent_id, SK: file_key) |
| `openclaw-sessions` | Session metadata with TTL |
| `openclaw-agents` | Agent configuration |
| `openclaw-dedup` | Webhook deduplication with TTL |
| `openclaw-connections` | WebSocket connections with TTL |
| `openclaw-environments` | Active compute environments |
| `openclaw-agent-roles` | Agent-created IAM roles |
| `openclaw-tool-registry` | Registered tools |
| `openclaw-cost-ledger` | Cost tracking entries (PK: date, SK: environmentId) |

### ECS Service Health

```bash
CLUSTER=$(aws ecs list-clusters --profile openclaw-dev --query 'clusterArns[0]' --output text)
SERVICE=$(aws ecs list-services --cluster $CLUSTER --profile openclaw-dev --query 'serviceArns[0]' --output text)

aws ecs describe-services \
  --cluster $CLUSTER --service $SERVICE \
  --profile openclaw-dev \
  --query 'services[0].{desired:desiredCount,running:runningCount,status:status}'
```

---

## Common Operations

### Restart the Gateway

```bash
CLUSTER=$(aws ecs list-clusters --profile openclaw-dev --query 'clusterArns[0]' --output text)
SERVICE=$(aws ecs list-services --cluster $CLUSTER --profile openclaw-dev --query 'serviceArns[0]' --output text)
aws ecs update-service --cluster $CLUSTER --service $SERVICE --force-new-deployment --profile openclaw-dev
```

### Update Workspace Files

Edit files in `workspace-seeds/`, then re-seed S3:

```bash
python3 scripts/seed-workspace.py \
  --bucket "$BUCKET" \
  --tenant-id default-tenant \
  --agent-id default-agent \
  --profile openclaw-dev \
  --region us-east-1
```

Then restart the ECS task to pick up the new files.

### Redeploy After Code Changes

```bash
cd infra
cdk deploy OpenClawGateway --profile openclaw-dev
```

CDK detects Docker asset hash changes and rebuilds/pushes the image automatically.

### ECS Exec Into the Container

```bash
CLUSTER=$(aws ecs list-clusters --profile openclaw-dev --query 'clusterArns[0]' --output text)
TASK=$(aws ecs list-tasks --cluster $CLUSTER --profile openclaw-dev --query 'taskArns[0]' --output text)

aws ecs execute-command \
  --cluster $CLUSTER \
  --task $TASK \
  --container GatewayContainer \
  --interactive \
  --command "/bin/bash" \
  --profile openclaw-dev
```

### Check Generated Config Inside Container

```bash
# After ECS exec:
cat ~/.openclaw/openclaw.json
ls -la /home/node/.openclaw/workspace/
env | grep -E 'WORKSPACE|PROVIDER|BEDROCK|TELEGRAM|AGENTCORE'
```

### Deploy All Stacks

```bash
cd infra
cdk deploy --all --profile openclaw-dev --require-approval broadening
```

### Destroy All Resources

```bash
cd infra
cdk destroy --all --profile openclaw-dev
```
