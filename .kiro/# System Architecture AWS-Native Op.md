# System Architecture: AWS-Enhanced OpenClaw

*Revised March 2026 — Hybrid approach: OpenClaw open-source as foundation, AWS services as targeted enhancements*

---

## Architectural Philosophy

This document describes an **OpenClaw-first** architecture. We begin with the OpenClaw open-source codebase and extend it with AWS services at specific seams where cloud infrastructure provides a clear advantage over the local-first defaults.

**Core principle:** If OpenClaw does it well today, keep it. If AWS can do it better at cloud scale or add a genuinely new capability, extend at that point. The agent's identity (workspace files, skills, SOUL.md) remains conceptually unchanged — we change where things *run* and *store*, not how they *work*.

### What Stays the Same
- The OpenClaw Gateway's routing and session logic
- Workspace file format (SOUL.md, MEMORY.md, AGENTS.md, etc.)
- Skill format (SKILL.md markdown files)
- Multi-agent patterns (supervisor → specialist)
- Heartbeat / cron / webhook automation model
- Claude as the primary model (via Bedrock instead of direct API)

### What Changes
- **Runtime:** Local Node.js process → ECS Fargate (cloud-hosted, always-on)
- **Storage:** `~/.openclaw/workspace/` → S3 + DynamoDB
- **Memory index:** SQLite → AgentCore Memory (managed, scalable)
- **Model API:** Anthropic SDK → Bedrock Converse API
- **Scheduling:** Node-internal cron → EventBridge Scheduler
- **Webhooks:** `localhost:18789/webhook/` → API Gateway public HTTPS
- **Sub-agents:** Local process → AgentCore Runtime (serverless, up to 8 hours)

### What's New (Not in Core OpenClaw)
- **Builder capability:** Agent can write CDK, deploy Lambda/ECS, provision infrastructure
- **Multi-tenant isolation:** Per-tenant IAM, S3 prefixes, workspace namespacing
- **Bedrock Guardrails:** Content filtering and safety layer at model call
- **Production observability:** CloudWatch metrics/logs, OpenTelemetry traces

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          USER CHANNELS (Unchanged)                       │
│   Telegram  •  Slack  •  Discord  •  WhatsApp  •  WebChat  •  Signal    │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │ HTTPS / WebSocket
┌─────────────────────────▼───────────────────────────────────────────────┐
│                    AWS INGRESS LAYER (New)                               │
│  ┌──────────────────┐  ┌───────────────────┐  ┌──────────────────────┐ │
│  │  API Gateway     │  │  API Gateway      │  │   AWS WAF +          │ │
│  │  (HTTP API)      │  │  (WebSocket API)  │  │   CloudFront CDN     │ │
│  │  /webhook/*      │  │  ws://...         │  │   (optional)         │ │
│  └────────┬─────────┘  └────────┬──────────┘  └──────────────────────┘ │
│           │                     │                                        │
│  ┌────────▼─────────────────────▼──────────────────────────────────┐    │
│  │               SQS Message Buffer (async decoupling)              │    │
│  └────────────────────────────┬─────────────────────────────────────┘   │
└───────────────────────────────│─────────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────────┐
│              OPENCLAW GATEWAY LAYER (OpenClaw Core, Cloud-Hosted)        │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │           ECS Fargate — OpenClaw Gateway Process                  │  │
│  │                                                                   │  │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────┐ │  │
│  │  │  Message Router │  │  Session Manager│  │  Skill Loader    │ │  │
│  │  │  (OpenClaw core)│  │  (OpenClaw core)│  │  (S3-backed)     │ │  │
│  │  └────────┬────────┘  └────────┬────────┘  └────────┬─────────┘ │  │
│  │           │                    │                     │           │  │
│  │  ┌────────▼────────────────────▼─────────────────────▼─────────┐ │  │
│  │  │              Workspace Assembler (OpenClaw core)             │ │  │
│  │  │  Loads: SOUL.md · MEMORY.md · AGENTS.md · TOOLS.md ·        │ │  │
│  │  │         USER.md · IDENTITY.md · HEARTBEAT.md                 │ │  │
│  │  │  Source: S3 bucket (replaces ~/.openclaw/workspace/)         │ │  │
│  │  └─────────────────────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌─────────────────────────────┐  ┌───────────────────────────────────┐ │
│  │  Heartbeat Scheduler        │  │  Webhook Dispatcher               │ │
│  │  EventBridge Scheduler rule │  │  (Lambda, replaces localhost:18789│ │
│  │  → Lambda → ECS task trigger│  │   /webhook/<path>)                │ │
│  └─────────────────────────────┘  └───────────────────────────────────┘ │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│                    AGENT EXECUTION LAYER                                  │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │             Supervisor Agent (OpenClaw Agent, Strands SDK wrapper) │ │
│  │                                                                    │ │
│  │  System prompt assembled from workspace files (unchanged format)  │ │
│  │  Model: Bedrock Converse API → Claude Sonnet 4.6 / Haiku 4.5      │ │
│  │  Tools: All OpenClaw built-in tools + new AWS builder tools        │ │
│  └───────────┬────────────────────────────────────────────┬───────────┘ │
│              │ sessions_spawn (unchanged API)              │             │
│   ┌──────────▼──────────┐                    ┌────────────▼──────────┐  │
│   │  Specialist Sub-Agent│                   │  Builder Sub-Agent     │  │
│   │  (AgentCore Runtime) │                   │  (AgentCore Runtime)   │  │
│   │  e.g., Iris (support)│                   │  CDK / Lambda deploy   │  │
│   │  e.g., Remy (sales)  │                   │  Up to 8 hours runtime │  │
│   └──────────────────────┘                   └────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│                    AWS SERVICES LAYER (Replacements + Enhancements)      │
│                                                                          │
│  ┌──────────────────────────┐  ┌─────────────────────────────────────┐  │
│  │  Amazon Bedrock          │  │  AgentCore Memory                   │  │
│  │  • Converse API          │  │  • Replaces SQLite memory index     │  │
│  │  • Claude Sonnet 4.6     │  │  • ingest_conversation_events()     │  │
│  │  • Cross-region profiles │  │  • retrieve_memory_records()        │  │
│  │  • Bedrock Guardrails    │  │  • Semantic + episodic strategies   │  │
│  │  • ConverseStream        │  │  • Compatible with MEMORY.md format │  │
│  └──────────────────────────┘  └─────────────────────────────────────┘  │
│                                                                          │
│  ┌──────────────────────────┐  ┌─────────────────────────────────────┐  │
│  │  AgentCore Gateway       │  │  AgentCore Identity                 │  │
│  │  • MCP-compatible tool   │  │  • OAuth 2.0 for Telegram/Slack     │  │
│  │    server                │  │  • Per-tenant Cognito user pools    │  │
│  │  • Lambda → MCP tool     │  │  • Credential provider for         │  │
│  │  • Cedar Policy enforce  │  │    outbound tool calls             │  │
│  │  • Replaces manual tool  │  │  • Replaces DM-pairing auth model  │  │
│  │    registration          │  └─────────────────────────────────────┘  │
│  └──────────────────────────┘                                           │
│                                                                          │
│  ┌──────────────────────────┐  ┌─────────────────────────────────────┐  │
│  │  Bedrock Code Interpreter│  │  AgentCore Runtime                  │  │
│  │  • Sandboxed Python/JS   │  │  • Serverless agent execution       │  │
│  │  • Test CDK/Lambda code  │  │  • Up to 8 hours per session        │  │
│  │    before deploying      │  │  • VPC support                      │  │
│  │  • AWS CLI in sandbox    │  │  • Fast cold starts                 │  │
│  └──────────────────────────┘  └─────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│                    PERSISTENCE LAYER                                      │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  S3 Buckets                                                       │   │
│  │  openclaw-workspaces/{tenantId}/{agentId}/                        │   │
│  │  ├── SOUL.md          ← unchanged file format                     │   │
│  │  ├── MEMORY.md        ← unchanged file format                     │   │
│  │  ├── AGENTS.md        ← unchanged file format                     │   │
│  │  ├── TOOLS.md         ← unchanged file format                     │   │
│  │  ├── USER.md          ← unchanged file format                     │   │
│  │  ├── IDENTITY.md      ← unchanged file format                     │   │
│  │  ├── HEARTBEAT.md     ← unchanged file format                     │   │
│  │  └── memory/YYYY-MM-DD.md ← unchanged file format                 │   │
│  │                                                                    │   │
│  │  openclaw-skills/{tenantId}/          ← shared skill registry     │   │
│  │  openclaw-logs/{tenantId}/{agentId}/  ← conversation logs         │   │
│  │  openclaw-code/{tenantId}/            ← agent-generated code      │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  DynamoDB Tables                                                  │   │
│  │  openclaw-agents     ← agent registry (agentId, tenantId, config)│   │
│  │  openclaw-sessions   ← active session state                       │   │
│  │  openclaw-cron       ← cron job definitions + last-run state      │   │
│  │  openclaw-memory     ← hot-tier MEMORY.md cache (< 10ms reads)   │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  Secrets Manager / SSM                                            │   │
│  │  Telegram bot tokens · Slack webhook URLs · API keys             │   │
│  │  Per-tenant credentials, not in workspace files                   │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────────────┐
│                    BUILDER LAYER (Net-New Capability)                     │
│           The agent can build, deploy, and scale its own AWS infra       │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Infrastructure Provisioning Skill                                 │ │
│  │  1. Agent writes CDK Python code                                   │ │
│  │  2. Code Interpreter tests/validates in sandbox                    │ │
│  │  3. CDK synth → CloudFormation template                            │ │
│  │  4. CDK deploy → live AWS stack                                    │ │
│  │  5. Stack ARN + outputs written to workspace (MEMORY.md)           │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Code Generation + Deployment Skill                                │ │
│  │  1. Agent writes Lambda/ECS code                                   │ │
│  │  2. Code Interpreter runs unit tests                               │ │
│  │  3. Docker build → ECR push                                        │ │
│  │  4. Lambda/ECS service update                                      │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  Guardrail: IAM permission boundary on all agent roles                  │
│  Agent can only provision resources within its CDK bootstrap boundary   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow: Incoming Message

```
User sends Telegram message
        │
        ▼
Telegram Bot API → POST to API Gateway /webhook/telegram
        │
        ▼
Lambda webhook receiver → SQS message queue
        │
        ▼
OpenClaw Gateway (ECS) receives from SQS
        │
        ├── Load workspace files from S3 (SOUL.md, MEMORY.md, etc.)
        ├── Load MEMORY.md from DynamoDB cache (hot path)
        └── Retrieve relevant memories from AgentCore Memory
        │
        ▼
Workspace Assembler builds system prompt (same format as local OpenClaw)
        │
        ▼
Bedrock Converse API → Claude Sonnet 4.6
  ├── Bedrock Guardrails applied (content filtering)
  ├── Tool calls → AgentCore Gateway (MCP tools)
  └── Streaming response → ConverseStream
        │
        ▼
Response dispatched → Telegram Bot API → User
        │
        ▼
Conversation ingested → AgentCore Memory (ingest_conversation_events)
Memory files updated → S3 + DynamoDB cache
```

---

## Data Flow: Agent-Initiated Infrastructure Build

```
User instructs agent: "Set up a new API endpoint for the orders webhook"
        │
        ▼
Supervisor agent loads infrastructure provisioning skill (SKILL.md from S3)
        │
        ▼
Supervisor spawns Builder Sub-Agent via sessions_spawn
  → AgentCore Runtime (serverless, can run for hours)
        │
        ▼
Builder agent:
  1. Generates CDK stack Python code
  2. Sends to Code Interpreter → validates syntax, runs unit tests
  3. If tests pass → runs `cdk synth` in Code Interpreter sandbox
  4. Reviews CloudFormation template (safety check)
  5. Runs `cdk deploy --require-approval never` via Code Interpreter
  6. Captures stack outputs (API URL, Lambda ARN)
        │
        ▼
Stack outputs written to MEMORY.md in S3:
  "Orders webhook API: https://xyz.execute-api.us-east-1.amazonaws.com/prod/orders"
        │
        ▼
Builder sub-agent sends completion message to Supervisor
        │
        ▼
Supervisor notifies user via Telegram: "Done. New webhook URL: https://..."
```

---

## Data Flow: Heartbeat

```
EventBridge Scheduler fires (every 30 minutes)
        │
        ▼
Lambda function triggers OpenClaw Gateway heartbeat for each active agent
        │
        ▼
Gateway loads HEARTBEAT.md from S3 (same format as local OpenClaw)
        │
        ▼
Bedrock Converse API: "Review this checklist. Act if needed or reply HEARTBEAT_OK."
        │
        ├── HEARTBEAT_OK → Gateway silently drops it (same as local behavior)
        └── Action required → Agent takes action, notifies user via Telegram
```

---

## Component Decision Matrix

For each OpenClaw component, the recommended deployment target at each scale:

| Component | Local Dev | Single-Tenant Cloud | Multi-Tenant SaaS |
|-----------|-----------|---------------------|-------------------|
| OpenClaw Gateway | Local Node.js | ECS Fargate | ECS Fargate (per-tenant task) |
| Workspace files | `~/.openclaw/workspace/` | S3 | S3 (per-tenant prefix) |
| Memory index | SQLite | AgentCore Memory | AgentCore Memory |
| Model API | Anthropic SDK direct | Bedrock Converse API | Bedrock (cross-region) |
| Cron | Node-internal | EventBridge Scheduler | EventBridge Scheduler |
| Webhooks | localhost:18789 | API Gateway | API Gateway + Lambda auth |
| Sub-agents | Local sessions | AgentCore Runtime | AgentCore Runtime |
| Skills registry | Local `.claude/skills/` | S3 | S3 (shared + per-tenant) |
| Auth | DM pairing | Cognito | Cognito + per-tenant pools |
| Observability | Console logs | CloudWatch | CloudWatch + X-Ray |

---

## Repository Structure (Fork of OpenClaw)

```
openclaw-aws/
├── packages/
│   ├── core/                      # OpenClaw core (git submodule or fork)
│   │   ├── gateway/               # OpenClaw Gateway — minimal changes
│   │   ├── workspace/             # Workspace file loader — S3 adapter added
│   │   ├── memory/                # Memory system — AgentCore adapter added
│   │   └── skills/                # Skills system — S3 adapter added
│   │
│   └── aws/                       # New: AWS extension layer
│       ├── adapters/
│       │   ├── s3-workspace.ts    # S3 adapter for workspace file I/O
│       │   ├── bedrock-model.ts   # Bedrock Converse API adapter
│       │   ├── agentcore-memory.ts # AgentCore Memory adapter
│       │   └── eventbridge-cron.ts # EventBridge Scheduler adapter
│       │
│       ├── skills/                # AWS-specific skills (SKILL.md format)
│       │   ├── aws-infrastructure-provisioning/SKILL.md
│       │   ├── code-generation-and-deployment/SKILL.md
│       │   ├── bedrock-model-invocation/SKILL.md
│       │   ├── memory-and-context-management/SKILL.md
│       │   └── ...
│       │
│       └── handlers/
│           ├── webhook-handler.ts  # Lambda: API Gateway → SQS
│           ├── heartbeat-handler.ts # Lambda: EventBridge → Gateway heartbeat
│           └── message-processor.ts # ECS: SQS → Gateway message dispatch
│
├── infra/                         # AWS CDK (Python)
│   ├── app.py
│   ├── stacks/
│   │   ├── gateway_stack.py       # ECS Fargate for OpenClaw Gateway
│   │   ├── api_stack.py           # API Gateway (HTTP + WebSocket)
│   │   ├── storage_stack.py       # S3 + DynamoDB
│   │   ├── memory_stack.py        # AgentCore Memory
│   │   ├── identity_stack.py      # Cognito + AgentCore Identity
│   │   ├── scheduler_stack.py     # EventBridge Scheduler
│   │   └── builder_stack.py       # Builder IAM + Code Interpreter config
│   └── constructs/
│       ├── openclaw_agent.py      # Reusable CDK construct for an agent
│       └── tenant_isolation.py    # Per-tenant IAM + S3 prefix construct
│
├── workspace-seeds/               # Default workspace file templates
│   ├── SOUL.md.template
│   ├── MEMORY.md.template
│   ├── AGENTS.md.template
│   ├── HEARTBEAT.md.template
│   └── USER.md.template
│
└── docs/
    ├── research_summary.md
    ├── aws_bedrock_capabilities.md
    ├── system_architecture.md     ← this file
    ├── kiro_steering_guide.md
    └── agent_skill_docs/
```

---

## Key Design Decisions

**1. Why fork rather than wrapper?**
The OpenClaw Gateway's routing logic, workspace file loading, and skill injection behavior are tightly coupled to the Node.js runtime. A wrapper would require reverse-engineering the internal API. A fork lets us add S3/Bedrock adapters at the right seams without breaking the core behavior.

**2. Why keep SOUL.md / MEMORY.md format unchanged?**
These files are the heart of what makes an OpenClaw agent feel coherent. They're also what the community understands. Changing the format would break compatibility with ClawHub skills, community workspace templates, and the existing knowledge base. S3 is just a different filesystem — the format stays the same.

**3. Why AgentCore Runtime for sub-agents rather than Lambda?**
OpenClaw sub-agents can run for up to 15 minutes by default (`runTimeoutSeconds: 900`). Builder sub-agents may run for hours (CDK deployments, multi-step code generation). Lambda's 15-minute max timeout is insufficient. AgentCore Runtime handles up to 8 hours serverlessly.

**4. Why EventBridge Scheduler rather than keeping Node-internal cron?**
The Node-internal cron runs inside the Gateway process. In a cloud deploy with auto-scaling, multiple Gateway instances would fire duplicate heartbeats. EventBridge Scheduler fires exactly once per schedule regardless of how many Gateway replicas are running.

**5. Why Bedrock Guardrails?**
The builder capability introduces new risk: the agent can write and deploy code to AWS. Guardrails provide a safety layer to prevent the agent from being socially engineered into deploying malicious infrastructure. Every Bedrock call passes through a Guardrail policy.

**6. Why keep Bedrock and direct Anthropic API as options?**
Some developers will prefer the direct Anthropic API for local development. The adapter pattern means both work. In production the Bedrock path is preferred for cross-region inference, CloudWatch visibility, and centralized credential management.

---

*See also: `kiro_steering_guide.md` for phase-by-phase build plan · `aws_bedrock_capabilities.md` for full Bedrock/AgentCore API reference · `agent_skill_docs/` for individual skill documentation*