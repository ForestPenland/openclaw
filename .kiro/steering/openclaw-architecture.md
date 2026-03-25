---
inclusion: auto
---

# OpenClaw AWS Architecture

## Core Principle

OpenClaw Gateway is the runtime. AWS services are injected via adapters and configuration — not by rewriting the Gateway. The approach is "fork, don't rewrite."

## How It Works

The OpenClaw Gateway runs as a Docker container on ECS Fargate. At startup:

1. `docker-entrypoint.sh` syncs workspace files from S3 (`aws s3 sync`)
2. `scripts/configure-gateway.mjs` reads channel secrets from Secrets Manager, writes `~/.openclaw/openclaw.json`
3. Gateway starts with `--bind lan --allow-unconfigured`

## Native Channel Handling

Telegram, Slack, and WebSocket messages flow directly through the OpenClaw Gateway's built-in handlers. There is NO custom webhook Lambda or SQS queue in the messaging path.

- Telegram: Gateway registers its own webhook/polling, parses updates, delivers responses
- Slack: Gateway handles Slack Events API natively
- WebSocket: Gateway exposes ws://host:18789 for web clients

Channel config is in `openclaw.json`, generated from Secrets Manager at startup.

## Bedrock as Provider Plugin

The AI model is configured via `openclaw.json`:
```json
{ "agents": { "defaults": { "model": "amazon-bedrock/amazon.nova-lite-v1:0" } } }
```

OpenClaw routes model calls through Bedrock's Converse API. The default model is Nova Lite. Change it by updating `BEDROCK_MODEL_ID` in `gateway_stack.py`.

## S3 Workspace Sync

Workspace files (SOUL.md, MEMORY.md, HEARTBEAT.md, AGENTS.md, TOOLS.md, USER.md, IDENTITY.md) are stored in S3 under `{tenantId}/{agentId}/`. At container startup, they're synced to the local filesystem. The Gateway reads them as if they were local files.

## Key Directories

```
adapters/          — TypeScript adapters (S3 workspace, Bedrock model, circuit breaker, etc.)
agents/            — Supervisor agent, Builder sub-agent
lambdas/           — Python Lambda handlers (heartbeat, memory consolidation)
infra/             — CDK Python infrastructure
  stacks/          — One stack per concern (gateway, storage, identity, api, memory, scheduler, builder)
  constructs/      — Reusable CDK constructs (tenant isolation, openclaw agent)
scripts/           — Operational scripts (seed workspace, configure gateway, test local, migrate memory)
workspace-seeds/   — Default workspace file templates
```

## What the Api Stack Is (and Isn't)

The `OpenClawApi` stack deploys an HTTP API Gateway and webhook Lambda. It is NOT in the messaging path. It exists for future use: GitHub webhook ingestion, admin API, external integrations. All Telegram/Slack/WebSocket messaging goes through the Gateway directly.

## Configuration Flow

```
Secrets Manager (openclaw/telegram-bot-token, etc.)
    ↓
configure-gateway.mjs (reads secrets, writes config)
    ↓
~/.openclaw/openclaw.json (channel config + model config)
    ↓
OpenClaw Gateway (reads config, starts channels)
```

## ECS Task Specs

- CPU: 1024 units (1 vCPU)
- Memory: 2048 MiB
- Base image: node:24-bookworm
- Health check: `node fetch('http://127.0.0.1:18789/healthz')`
- Desired count: 1
- Auto-restart on crash/unhealthy
- ECS Exec enabled for debugging
