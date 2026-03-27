# 🪨🦞 RockClaw — AI Agent on AWS

RockClaw is an autonomous AI agent running on AWS, built on the [OpenClaw](https://github.com/openclaw/openclaw) gateway framework. It connects to your messaging apps (Telegram, Slack, Discord, WhatsApp), reasons with Amazon Bedrock models, remembers conversations with AgentCore Memory, and self-extends by deploying new capabilities to AgentCore Runtime.

One CDK deployment gives you a production agent that can build its own tools.

## What Makes This Different

Most AI assistant setups give you a chatbot. RockClaw gives you an agent that:

- Runs 24/7 on ECS Fargate with automatic crash recovery and health monitoring
- Uses Amazon Bedrock for model access (no API keys to manage, IAM auth, cross-region inference)
- Remembers across sessions via AgentCore Memory (semantic search over past conversations)
- Self-extends by deploying new AI agents to AgentCore Runtime as MCP tools
- Creates its own IAM roles with permission boundaries for elevated AWS operations
- Manages its own infrastructure (the agent has deployed 3+ CloudFormation stacks autonomously)

## Architecture

```
You (Telegram / Slack / Discord / WhatsApp)
  │
  ▼
┌─────────────────────────────────────────────────────────────────┐
│  AWS Cloud (ECS Fargate)                                        │
│                                                                 │
│  OpenClaw Gateway ──── Amazon Bedrock (Claude / Nova)           │
│       │                                                         │
│       ├── AgentCore Memory (semantic + episodic recall)          │
│       │                                                         │
│       ├── AgentCore Gateway (MCP tool server)                   │
│       │     ├── MemoryAgent (persistent memory tools)           │
│       │     ├── OracleAgent (research + analysis)               │
│       │     ├── CreativeAgent (image + video generation)        │
│       │     └── ... (agent deploys new ones autonomously)       │
│       │                                                         │
│       ├── S3 (workspace files, skills, artifacts)               │
│       ├── DynamoDB (sessions, memory cache, agent roles)        │
│       ├── EventBridge (heartbeat, consolidation, cleanup)       │
│       └── CloudWatch (alarms, logs, crash detection)            │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- AWS CLI 2.x, CDK 2.150+, Python 3.12+, Node.js 22+, Docker 24+
- An AWS account with Bedrock model access enabled

### Deploy

```bash
# Clone and set up CDK
git clone https://github.com/Rock-f-me/rockclaw.git
cd rockclaw/infra
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Bootstrap CDK (first time only)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
cdk bootstrap aws://${ACCOUNT_ID}/us-east-1

# Deploy all 10 stacks
cdk deploy --all --require-approval broadening
```

First deployment takes ~10-15 minutes (Docker build is the bottleneck).

### Connect Telegram

```bash
# Store your bot token (get one from @BotFather)
aws secretsmanager put-secret-value \
  --secret-id openclaw/telegram-bot-token \
  --secret-string '<YOUR_BOT_TOKEN>'

# Restart the Gateway to pick it up
CLUSTER=$(aws ecs list-clusters --query 'clusterArns[0]' --output text)
SERVICE=$(aws ecs list-services --cluster $CLUSTER --query 'serviceArns[0]' --output text)
aws ecs update-service --cluster $CLUSTER --service $SERVICE --force-new-deployment
```

Send a message to your bot. It responds via Bedrock.

## CDK Stacks (10 total)

| Stack | What It Creates |
|-------|----------------|
| OpenClawStorage | S3 buckets (workspace, skills, artifacts), 5 DynamoDB tables |
| OpenClawGateway | VPC, ECS Fargate cluster, Gateway container (1024 CPU / 2048 MiB), task role with broad AWS permissions |
| OpenClawIdentity | Cognito user pool, Secrets Manager secrets (Telegram, Slack, GitHub) |
| OpenClawApi | HTTP/WebSocket API Gateway, webhook Lambda (future use) |
| OpenClawMemory | AgentCore Memory config (SSM params, IAM policy), MemoryAgent ECR repo |
| OpenClawScheduler | Heartbeat (30min) + consolidation (nightly) Lambdas, EventBridge rules |
| OpenClawBuilder | Builder agent IAM role with permission boundary |
| OpenClawAgentCoreTools | deploy_static_site Lambda, AgentCore Gateway target |
| OpenClawComputeEnvironments | Environment tracking tables, cleanup Lambda, permission boundary |
| OpenClawHealth | CloudWatch alarms (task-down, CPU, memory), ECS crash detection |

## Self-Extending Agent Pattern

The agent ships with three foundational skills that let it build new capabilities:

### agentcore-agent-builder
Deploys new AI agents to AgentCore Runtime and registers them as MCP tools on the Gateway. The agent writes a FastMCP server, builds a container via CodeBuild, sets up Cognito OAuth2 auth, and registers the Gateway target — all autonomously.

### role-factory
Creates task-scoped IAM roles with the `agent-permission-boundary` for elevated AWS operations. Every role is prefixed `agent-task-*`, constrained by the boundary, and auto-cleaned after 24 hours.

### aws-infrastructure
AWS CLI patterns for direct host execution. The agent runs `aws` commands directly on the Fargate container for S3, CloudFormation, ECS, and other service operations.

See [Extending Your Agent](docs/extending-your-agent.md) for examples of what the agent can build: research agents, creative agents, data analysis, email integration, cost monitoring, and more.

## Reference Implementation: MemoryAgent

`agents/memory-agent/` contains a complete working example of a FastMCP agent that runs on AgentCore Runtime:

- 4 MCP tools: `memory_store`, `memory_search`, `memory_context`, `memory_list`
- Backed by AgentCore Memory with semantic + preference strategies
- Multi-actor support (operator preferences, agent decisions, specialist memories)
- Dockerfile, buildspec.yml, and requirements.txt included

## Key Design Decisions

- **Fork, don't rewrite**: OpenClaw's core Gateway, workspace files, skill format, and channel handlers are preserved. AWS services are injected at adapter seams.
- **Native channel handling**: Telegram, Slack, and WebSocket messages flow through OpenClaw's built-in handlers. No custom webhook Lambda pipeline for messaging.
- **Workspace files are the source of truth**: SOUL.md, MEMORY.md, etc. remain plain UTF-8 markdown in S3.
- **Agent creates its own tools**: The agent-builder skill lets the agent deploy new AgentCore Runtime agents and register them as Gateway MCP tools without CDK redeployment.
- **Permission boundaries as security ceiling**: All agent-created IAM roles are constrained by `agent-permission-boundary`. The agent cannot escalate beyond this boundary.

## Cost

Monthly estimates for a single-agent deployment (~100 conversations/day):

| Component | Cost |
|-----------|------|
| ECS Fargate (1024 CPU, 2048 MiB, 24/7) | ~$35/mo |
| NAT Gateway | ~$32/mo |
| DynamoDB (on-demand, 9 tables) | ~$3-8/mo |
| Everything else (S3, Lambda, CloudWatch, Secrets Manager, SNS) | ~$5-8/mo |
| **Infrastructure total** | **~$75-80/mo** |
| + Bedrock Nova Lite | +$5-15/mo |
| + Bedrock Claude Sonnet | +$50-150/mo |

## Documentation

- [Deployment Guide](DEPLOYMENT.md) — full CDK deployment walkthrough
- [Extending Your Agent](docs/extending-your-agent.md) — what the agent can build
- [OpenClaw Docs](https://docs.openclaw.ai/) — upstream Gateway documentation

## Built On

- [OpenClaw](https://github.com/openclaw/openclaw) — open-source AI agent gateway (MIT)
- [Amazon Bedrock](https://aws.amazon.com/bedrock/) — foundation model access
- [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/) — agent runtime, memory, and gateway
- [Strands Agents SDK](https://github.com/strands-agents/sdk-python) — Python agent framework
- [AWS CDK](https://aws.amazon.com/cdk/) — infrastructure as code

## License

This project is built on OpenClaw which is [MIT licensed](LICENSE).
