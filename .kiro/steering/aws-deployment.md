---
inclusion: manual
---

# AWS Deployment Patterns

## CDK Conventions

- All infrastructure is in `infra/` — stacks in `infra/stacks/`, reusable constructs in `infra/constructs/`
- Entry point: `infra/app.py` wires all stacks together
- Sole CDK dependency: `aws-cdk-lib` (plus `constructs`). No third-party CDK libraries
- Python 3.12+ required. Virtual env in `infra/.venv/`
- Deploy: `cd infra && cdk deploy --all --profile openclaw-dev`
- Region: `us-east-1`, profile: `openclaw-dev`

## Stack Architecture

10 stacks, deployed in dependency order:

1. **OpenClawStorage** — S3 buckets (workspace, skills, artifacts) + 5 DynamoDB tables (memory, sessions, agents, dedup, connections). No dependencies
2. **OpenClawIdentity** — Cognito user pool + Secrets Manager secrets (Telegram, Slack, GitHub tokens). No dependencies
3. **OpenClawGateway** — VPC, ECS Fargate cluster, Docker image build, task role with Bedrock/S3/DynamoDB/IAM/STS/Secrets Manager permissions. ACPX enabled with MCP bridge to AgentCore Gateway. Depends on Storage
4. **OpenClawApi** — HTTP/WebSocket API Gateway + webhook Lambda + SQS. NOT in messaging path — for future webhook-based channels. Depends on Storage, Identity
5. **OpenClawMemory** — SSM parameters for AgentCore Memory store config (store ID, strategy IDs), IAM policy for memory access, ECR repository for MemoryAgent container. Memory store created by agent at first boot. Depends on Storage
6. **OpenClawScheduler** — EventBridge rules (heartbeat 30min, consolidation nightly) + Lambda handlers. Depends on Storage
7. **OpenClawBuilder** — Builder agent IAM role with permission boundary. Not yet used. Depends on Storage
8. **OpenClawAgentCoreTools** — Lambda for `deploy_static_site`. Registered as AgentCore Gateway target. MCP bridge connects to it via ACPX. No dependencies
9. **OpenClawComputeEnvironments** — 4 DynamoDB tables (environments, agent-roles, tool-registry, cost-ledger) + cleanup Lambda + EventBridge schedules (30min environments, 6hr roles) + agent-permission-boundary managed policy + SNS topic. No dependencies
10. **OpenClawHealth** — CloudWatch alarms (task-down, CPU high, memory high), ECS task-stopped EventBridge rule, SNS health alerts topic. Depends on Gateway

## Adding a New Stack

1. Create `infra/stacks/my_stack.py` with a class extending `Stack`
2. Import and instantiate in `infra/app.py`
3. Add dependencies with `my_stack.add_dependency(other_stack)`
4. Run `cdk synth --profile openclaw-dev` to validate
5. Deploy with `cdk deploy MyStackName --profile openclaw-dev`

## Docker Build (Dockerfile.gateway)

- Base image: `node:24-bookworm` (NOT slim — needs AWS CLI)
- Multi-stage: Stage 1 builds OpenClaw (`pnpm install --no-frozen-lockfile` + `pnpm build:docker`), Stage 2 copies artifacts + installs AWS CLI
- CDK builds and pushes to ECR automatically via `ecs.ContainerImage.from_asset()`
- The `exclude` list in `gateway_stack.py` prevents `infra/`, `.git/`, `node_modules/` from being copied into the Docker context (avoids ENAMETOOLONG)
- Dev dependency pruning: `CI=true pnpm prune --prod --config.frozen-lockfile=false`

## Updating the Gateway Image

Any change to application code, `Dockerfile.gateway`, `docker-entrypoint.sh`, or `scripts/configure-gateway.mjs` requires a redeploy:

```bash
cd infra
cdk deploy OpenClawGateway --profile openclaw-dev
```

CDK detects the Docker asset hash changed and rebuilds/pushes the image.

For config-only changes (e.g., new Secrets Manager value), just restart the ECS task:

```bash
CLUSTER=$(aws ecs list-clusters --profile openclaw-dev --query 'clusterArns[0]' --output text)
SERVICE=$(aws ecs list-services --cluster $CLUSTER --profile openclaw-dev --query 'serviceArns[0]' --output text)
aws ecs update-service --cluster $CLUSTER --service $SERVICE --force-new-deployment --profile openclaw-dev
```

## Environment Variable Reference

Set in `gateway_stack.py` on the container definition:

| Variable | Default | Purpose |
|----------|---------|---------|
| `WORKSPACE_BUCKET` | *(from Storage)* | S3 bucket for workspace files |
| `TENANT_ID` | `default-tenant` | S3 key prefix segment |
| `AGENT_ID` | `default-agent` | S3 key prefix segment |
| `PROVIDER` | `bedrock` | Model provider (`bedrock` or `anthropic`) |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-sonnet-4-6` | Bedrock model to use |
| `TELEGRAM_SECRET_NAME` | `openclaw/telegram-bot-token` | Secrets Manager secret name |
| `OPENCLAW_ALLOW_INSECURE_PRIVATE_WS` | `1` | Allow private workspace access |
| `AGENTCORE_GATEWAY_SECRET_NAME` | `openclaw/agentcore-gateway-credentials` | Secrets Manager secret for AgentCore Gateway MCP bridge OAuth2 credentials |

## IAM Permissions on the Task Role

The ECS task role (defined in `gateway_stack.py`) has:

### Core permissions
- S3 read/write on the workspace bucket
- Bedrock `InvokeModel`, `InvokeModelWithResponseStream`, `ListFoundationModels`, `GetFoundationModel`
- AWS Marketplace `ViewSubscriptions`, `Subscribe`, `Unsubscribe` (for third-party models)
- DynamoDB read/write on memory, sessions, agents tables
- Secrets Manager `GetSecretValue` on `openclaw/*`
- AgentCore Runtime `bedrock-agent-runtime:*`

### Role factory permissions (for task-scoped IAM roles)
- `iam:CreateRole` on `arn:aws:iam::*:role/agent-task-*` — requires `iam:PermissionsBoundary` condition set to `agent-permission-boundary`
- `iam:DeleteRole`, `iam:PutRolePolicy`, `iam:DeleteRolePolicy`, `iam:AttachRolePolicy`, `iam:DetachRolePolicy`, `iam:TagRole`, `iam:GetRole`, `iam:ListRolePolicies`, `iam:ListAttachedRolePolicies`, `iam:PassRole` on `arn:aws:iam::*:role/agent-task-*`
- `sts:AssumeRole` on `arn:aws:iam::*:role/agent-task-*`

### Compute environment permissions
- DynamoDB CRUD on `openclaw-agent-roles`, `openclaw-environments`, `openclaw-cost-ledger`, `openclaw-tool-registry` tables
