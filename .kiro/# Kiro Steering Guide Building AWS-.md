# Kiro Steering Guide: AWS-Enhanced OpenClaw

*A comprehensive brief for Kiro AI Development Agent — your complete mandate for building this system autonomously*

**Document Version:** 2.0 (Revised — Hybrid Approach)
**Date:** March 2026
**Purpose:** Full context, constraints, goals, architecture, and step-by-step direction for extending the OpenClaw open-source project with AWS capabilities

---

## CRITICAL: Read This First

**This is NOT a from-scratch build.** The original guidance described building an AWS-native replacement for OpenClaw. That direction has been revised.

**The correct approach:**
1. **Start with the OpenClaw open-source codebase** as your foundation
2. **Fork the official OpenClaw repository** and extend it — do not rewrite what already works
3. **Add AWS services at specific extension points** where they provide clear value over the local defaults
4. **Preserve OpenClaw's existing behavior** wherever it is efficient and sufficient

The OpenClaw codebase is mature, tested, and has 328K+ GitHub stars. Respect it. Understand it before you change it. When in doubt, keep the OpenClaw behavior and wrap it with an AWS adapter rather than replacing it.

---

## Who You Are Building For

A founder/operator who wants an OpenClaw agent that:
1. **Runs reliably on AWS** — not dependent on a local Mac Mini or VPS staying online
2. **Uses Bedrock** for model access — centralized cost tracking, guardrails, cross-region inference
3. **Can build products** — not just respond to messages, but deploy Lambda functions, CDK stacks, and APIs on AWS
4. **Can scale to multiple users** — multi-tenant support when needed
5. **Keeps everything that makes OpenClaw great** — the file-based identity, skill system, messaging-native UX, autonomous heartbeat/cron/webhook model

---

## Background: OpenClaw Architecture (Know This Cold)

OpenClaw is the fastest-growing open-source project in GitHub history (328K+ stars as of March 2026), created by Peter Steinberger in November 2025. You must internalize its architecture before touching the code:

### The Gateway
A WebSocket server running at `ws://127.0.0.1:18789`. Central control plane for all operations:
- Routes incoming messages from 20+ platforms (Telegram, Slack, Discord, etc.)
- Manages sessions and routes tool execution
- Runs cron jobs (persisted under `~/.openclaw/cron/`)
- Handles webhooks: `POST http://localhost:18789/webhook/<path>`
- Manages heartbeat cycle (every 30 minutes)
- **Do not rewrite this.** Run it on ECS and put AWS services in front of it.

### Workspace Files
Every agent has a `~/.openclaw/workspace/` directory with these markdown files assembled into the system prompt each session:

| File | Purpose | AWS Equivalent |
|------|---------|---------------|
| `SOUL.md` | Personality, values, tone | S3 (same format, different storage) |
| `AGENTS.md` | Operating instructions | S3 (same format) |
| `TOOLS.md` | Tool documentation | S3 (same format) |
| `USER.md` | Context about the human | S3 (same format) |
| `IDENTITY.md` | Agent name/vibe | S3 (same format) |
| `MEMORY.md` | Long-term memory (~100 lines) | S3 + DynamoDB hot cache |
| `HEARTBEAT.md` | 30-min action checklist | S3 (same format) |
| `memory/YYYY-MM-DD.md` | Daily log | S3 (same format) |

**File format never changes.** S3 is just a different filesystem.

### Memory System
- `MEMORY.md` — always loaded, curated ~100 lines
- SQLite at `~/.openclaw/memory/{agentId}.sqlite` — BM25 + vector hybrid search
- Embedding providers: OpenAI, Gemini, Voyage, Mistral, Ollama, local GGUF
- **Replace the SQLite index with AgentCore Memory for cloud deploy.** Keep the `MEMORY.md` format.

### Skills System
- Skills live in `.claude/skills/` as `SKILL.md` markdown files
- Only relevant skills injected per turn (selective injection)
- ClawHub registry: 13,729+ community skills
- **Keep the SKILL.md format entirely.** Move skill storage to S3.

### Automation
- **Cron:** Standard cron syntax, persisted to `~/.openclaw/cron/`, runs in Gateway process
- **Heartbeat:** Reads `HEARTBEAT.md` every 30 minutes, acts or returns `HEARTBEAT_OK`
- **Webhooks:** External POSTs to `localhost:18789/webhook/<path>`
- **Replace with:** EventBridge Scheduler (cron), API Gateway (webhooks). Keep same logical behavior.

### Multi-Agent
- `sessions_spawn` tool — spawns sub-agents asynchronously
- `maxSpawnDepth: 1` (default), `maxConcurrent: 8`, `runTimeoutSeconds: 900`
- Sub-agents run in isolated sessions, announce results back to requester
- **Extend with:** AgentCore Runtime for long-running sub-agents (builder tasks)

---

## AWS Services You'll Use

### Amazon Bedrock
- **Converse API:** Unified interface across all models (replaces `@anthropic-ai/sdk` direct calls)
- **ConverseStream:** Streaming responses
- **Model IDs:** `anthropic.claude-sonnet-4-6-20251015-v1:0` / `anthropic.claude-haiku-4-5-20251001-v1:0`
- **Cross-region inference:** `us.anthropic.claude-sonnet-4-6-v1:0` (higher throughput)
- **Bedrock Guardrails:** Content filtering on all model calls — critical for builder capability

### AgentCore Memory
- `ingest_conversation_events()` — replaces manual SQLite writes
- `retrieve_memory_records()` — replaces local vector search
- Semantic + episodic memory strategies
- **Migration:** On first cloud session, ingest existing MEMORY.md into AgentCore

### AgentCore Runtime
- Serverless agent execution, up to 8 hours per session
- VPC support, fast cold starts
- **Use for:** Long-running builder sub-agents (CDK deploys can take 10–30 minutes)

### AgentCore Gateway
- MCP-compatible tool server
- Converts Lambda functions → MCP tools
- Cedar Policy enforcement on every tool call
- **Use for:** Registering the builder capability as MCP tools

### AgentCore Identity
- OAuth 2.0 (authorization code + client credentials)
- Cognito/Okta/Auth0 support
- Credential provider for outbound tool calls
- **Use for:** Telegram auth, per-tenant user isolation

### Strands Agents SDK
AWS open-source Python framework (Apache 2.0). Use this as the Python wrapper around OpenClaw agent logic:
```python
from strands import Agent, tool
from strands.models import BedrockModel

model = BedrockModel(model_id="us.anthropic.claude-sonnet-4-6-v1:0", region_name="us-east-1")
agent = Agent(model=model, system_prompt=workspace_system_prompt)
response = agent("message from user")
```

### Supporting AWS Services
- **ECS Fargate** — runs the OpenClaw Gateway process
- **API Gateway** — public HTTPS/WebSocket ingress (replaces localhost:18789)
- **SQS** — message buffer between API Gateway and Gateway process
- **S3** — workspace files + skills registry
- **DynamoDB** — agent registry, session state, MEMORY.md hot cache, cron state
- **EventBridge Scheduler** — replaces Node-internal cron
- **Lambda** — webhook handlers, heartbeat trigger, AgentCore Gateway tools
- **Secrets Manager** — Telegram tokens, Slack keys (never in workspace files)
- **ECR** — Docker images for ECS tasks
- **CloudWatch** — logs, metrics, alarms
- **CDK (Python)** — all infrastructure as code

---

## Repository Structure

```
openclaw-aws/
├── packages/
│   ├── core/                      # OpenClaw fork (git submodule)
│   │   └── [OpenClaw source tree] # Minimal modifications only
│   │
│   └── aws-extensions/            # New: AWS extension layer (TypeScript/Python)
│       ├── adapters/
│       │   ├── s3-workspace.ts    # S3 adapter implementing OpenClaw's IWorkspaceStore
│       │   ├── bedrock-model.ts   # Bedrock Converse API adapter for OpenClaw model config
│       │   ├── agentcore-memory.ts # AgentCore Memory adapter replacing SQLite
│       │   └── eventbridge-cron.ts # EventBridge Scheduler adapter replacing Node cron
│       │
│       ├── handlers/
│       │   ├── webhook-handler.ts  # Lambda: API Gateway → SQS → Gateway
│       │   ├── heartbeat-handler.ts # Lambda: EventBridge → Gateway heartbeat
│       │   └── message-processor.ts # SQS consumer for ECS Gateway
│       │
│       └── builder/               # NEW: Infrastructure builder capability
│           ├── cdk_tools.py       # CDK write/synth/deploy tools (Strands @tool)
│           ├── lambda_tools.py    # Lambda deploy/update tools
│           ├── ecs_tools.py       # ECS service tools
│           └── code_interpreter.py # Bedrock Code Interpreter wrapper
│
├── skills/                        # AWS-specific skills (SKILL.md format, unchanged)
│   ├── aws-infrastructure-provisioning/SKILL.md
│   ├── code-generation-and-deployment/SKILL.md
│   ├── bedrock-model-invocation/SKILL.md
│   ├── memory-and-context-management/SKILL.md
│   ├── messaging-and-channels/SKILL.md
│   ├── autonomous-scheduling/SKILL.md
│   ├── identity-and-authentication/SKILL.md
│   └── agent-orchestration/SKILL.md
│
├── workspace-seeds/               # Default workspace file templates (same format as OpenClaw)
│   ├── SOUL.md.template
│   ├── MEMORY.md.template
│   ├── AGENTS.md.template
│   ├── TOOLS.md.template
│   ├── HEARTBEAT.md.template
│   └── USER.md.template
│
├── infra/                         # AWS CDK (Python)
│   ├── app.py
│   ├── stacks/
│   │   ├── gateway_stack.py       # ECS Fargate for OpenClaw Gateway
│   │   ├── api_stack.py           # API Gateway (HTTP + WebSocket)
│   │   ├── storage_stack.py       # S3 + DynamoDB
│   │   ├── memory_stack.py        # AgentCore Memory
│   │   ├── identity_stack.py      # Cognito + AgentCore Identity
│   │   ├── scheduler_stack.py     # EventBridge Scheduler (replaces Node cron)
│   │   └── builder_stack.py       # Builder IAM permissions + AgentCore Gateway tools
│   └── constructs/
│       ├── openclaw_agent.py      # Reusable CDK construct: one agent = one ECS task + workspace bucket prefix
│       └── tenant_isolation.py    # Per-tenant IAM boundary construct
│
├── scripts/
│   ├── seed-workspace.py          # Upload default workspace files to S3
│   ├── migrate-memory.py          # Migrate local SQLite → AgentCore Memory
│   └── test-local.sh              # Run OpenClaw locally before AWS deploy
│
└── docs/
    └── [this documentation set]
```

---

## Build Phases (Execute in Order)

### Phase 0 — Fork and Run OpenClaw Locally (Do This First — No AWS Yet)

**Goal:** Understand OpenClaw before touching it. This phase has zero AWS resources.

**Step 0.1: Fork the repository**
```bash
# Fork openclaw/openclaw on GitHub, then:
git clone https://github.com/YOUR_ORG/openclaw.git packages/core
cd packages/core
git remote add upstream https://github.com/openclaw/openclaw.git
```

**Step 0.2: Run it locally and verify it works**
```bash
npm install -g openclaw
openclaw onboard --install-daemon
# Set up a test Telegram bot, add the token to config
# Send a message and verify the agent responds
```

**Step 0.3: Instrument the key seams**
Before adding any AWS code, identify and document exactly where to insert adapters:
- Where does the Gateway load workspace files? (this is where the S3 adapter hooks in)
- Where does it call the model API? (this is where the Bedrock adapter hooks in)
- Where does it write memory? (this is where the AgentCore Memory adapter hooks in)
- Where does cron registration happen? (this is where EventBridge adapter hooks in)

Add logging to these seams so you can verify behavior doesn't change when you swap them.

**Step 0.4: Create your workspace seed files**
Write and test your `SOUL.md`, `MEMORY.md`, `AGENTS.md`, `HEARTBEAT.md`, and `TOOLS.md` templates. These define the agent's personality and behavior. Test them locally until the agent behaves as desired.

**Checkpoint:** OpenClaw is running locally on Telegram with your workspace files. Agent responds correctly. You understand the codebase well enough to describe what happens on every message.

---

### Phase 1 — Cloud Lift: OpenClaw on ECS (No Behavior Changes)

**Goal:** Run the unmodified OpenClaw Gateway on AWS. No feature changes — just moving the runtime.

**Step 1.1: Containerize the Gateway**
Create a `Dockerfile` that runs the OpenClaw Gateway process:
```dockerfile
FROM node:24-slim
RUN npm install -g openclaw
WORKDIR /home/openclaw
COPY workspace-seeds/ /home/openclaw/.openclaw/workspace/
ENV OPENCLAW_GATEWAY_PORT=18789
CMD ["openclaw", "start", "--no-daemon"]
```

**Step 1.2: Build storage layer CDK stack (storage_stack.py)**
```python
from aws_cdk import Stack, RemovalPolicy, aws_s3 as s3, aws_dynamodb as dynamodb
from constructs import Construct

class StorageStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Workspace files — one prefix per agent
        self.workspace_bucket = s3.Bucket(
            self, "WorkspaceBucket",
            bucket_name=f"openclaw-workspaces-{self.account}",
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN
        )

        # Skills registry
        self.skills_bucket = s3.Bucket(
            self, "SkillsBucket",
            bucket_name=f"openclaw-skills-{self.account}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL
        )

        # Hot cache for MEMORY.md (< 10ms reads vs S3's ~100ms)
        self.memory_table = dynamodb.Table(
            self, "MemoryTable",
            table_name="openclaw-memory",
            partition_key=dynamodb.Attribute(name="agent_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="file_key", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN
        )

        # Agent registry + session state
        self.agents_table = dynamodb.Table(
            self, "AgentsTable",
            table_name="openclaw-agents",
            partition_key=dynamodb.Attribute(name="agent_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN
        )

        self.sessions_table = dynamodb.Table(
            self, "SessionsTable",
            table_name="openclaw-sessions",
            partition_key=dynamodb.Attribute(name="agent_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="session_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl"
        )
```

**Step 1.3: Write the S3 workspace adapter**
The OpenClaw Gateway loads workspace files from `~/.openclaw/workspace/`. Create an adapter that serves the same files from S3 by intercepting the filesystem calls and redirecting to S3. Mount the S3 files into the ECS container's filesystem at startup using an init container or by extending the Gateway entrypoint to sync S3 → local on boot.

```typescript
// packages/aws-extensions/adapters/s3-workspace.ts
import { S3Client, GetObjectCommand, PutObjectCommand, ListObjectsV2Command } from "@aws-sdk/client-s3";
import * as fs from "fs/promises";
import * as path from "path";

export class S3WorkspaceAdapter {
  private s3 = new S3Client({});
  private bucket: string;
  private agentId: string;
  private localWorkspaceDir: string;

  constructor(bucket: string, agentId: string, localDir = `${process.env.HOME}/.openclaw/workspace`) {
    this.bucket = bucket;
    this.agentId = agentId;
    this.localWorkspaceDir = localDir;
  }

  // Sync all workspace files from S3 → local filesystem on startup
  async syncFromS3(): Promise<void> {
    const prefix = `${this.agentId}/`;
    const list = await this.s3.send(new ListObjectsV2Command({ Bucket: this.bucket, Prefix: prefix }));
    await fs.mkdir(this.localWorkspaceDir, { recursive: true });

    for (const obj of list.Contents ?? []) {
      const key = obj.Key!;
      const filename = key.replace(prefix, "");
      const content = await this.s3.send(new GetObjectCommand({ Bucket: this.bucket, Key: key }));
      const body = await content.Body!.transformToString();
      const localPath = path.join(this.localWorkspaceDir, filename);
      await fs.mkdir(path.dirname(localPath), { recursive: true });
      await fs.writeFile(localPath, body, "utf8");
    }
  }

  // Write updated workspace file back to S3
  async writeFile(filename: string, content: string): Promise<void> {
    const key = `${this.agentId}/${filename}`;
    await this.s3.send(new PutObjectCommand({ Bucket: this.bucket, Key: key, Body: content }));
    const localPath = path.join(this.localWorkspaceDir, filename);
    await fs.mkdir(path.dirname(localPath), { recursive: true });
    await fs.writeFile(localPath, content, "utf8");
  }
}
```

**Step 1.4: ECS Fargate Gateway CDK stack**
```python
# infra/stacks/gateway_stack.py
from aws_cdk import (
    Stack, Duration,
    aws_ecs as ecs, aws_ec2 as ec2, aws_iam as iam, aws_ecr_assets as ecr_assets
)
from constructs import Construct

class GatewayStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, vpc, workspace_bucket, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        cluster = ecs.Cluster(self, "OpenClawCluster", vpc=vpc, cluster_name="openclaw")

        task_role = iam.Role(self, "GatewayTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )
        workspace_bucket.grant_read_write(task_role)
        task_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"]
        ))

        task_def = ecs.FargateTaskDefinition(
            self, "GatewayTask", cpu=512, memory_limit_mib=1024, task_role=task_role
        )
        task_def.add_container("Gateway",
            image=ecs.ContainerImage.from_asset("../packages/core"),
            environment={
                "OPENCLAW_S3_BUCKET": workspace_bucket.bucket_name,
                "OPENCLAW_AWS_REGION": self.region,
                "AWS_DEFAULT_REGION": self.region,
            },
            logging=ecs.LogDrivers.aws_logs(stream_prefix="openclaw-gateway")
        )

        self.service = ecs.FargateService(
            self, "GatewayService",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            assign_public_ip=False
        )
```

**Step 1.5: Upload workspace seed files to S3**
```bash
python scripts/seed-workspace.py --agent-id felix --bucket openclaw-workspaces-ACCOUNT
```

**Checkpoint:** OpenClaw Gateway is running on ECS Fargate. It loads workspace files from S3. Behavior is identical to local. Deploy and verify Telegram integration still works.

---

### Phase 2 — Bedrock Model Swap

**Goal:** Replace direct Anthropic API calls with Bedrock Converse API. No behavior change — same Claude model, different API path.

**Step 2.1: Write the Bedrock model adapter**
Find where OpenClaw configures its model provider. Add Bedrock as a provider option:

```typescript
// packages/aws-extensions/adapters/bedrock-model.ts
import { BedrockRuntimeClient, ConverseCommand, ConverseStreamCommand } from "@aws-sdk/client-bedrock-runtime";

export class BedrockModelAdapter {
  private client = new BedrockRuntimeClient({ region: process.env.AWS_DEFAULT_REGION ?? "us-east-1" });
  private modelId: string;

  constructor(modelId = "us.anthropic.claude-sonnet-4-6-v1:0") {
    this.modelId = modelId;
  }

  async converse(messages: any[], systemPrompt: string, tools?: any[]) {
    const params: any = {
      modelId: this.modelId,
      system: [{ text: systemPrompt }],
      messages,
    };
    if (tools?.length) {
      params.toolConfig = { tools };
    }
    const response = await this.client.send(new ConverseCommand(params));
    return response;
  }

  async *converseStream(messages: any[], systemPrompt: string, tools?: any[]) {
    const params: any = {
      modelId: this.modelId,
      system: [{ text: systemPrompt }],
      messages,
    };
    if (tools?.length) {
      params.toolConfig = { tools };
    }
    const response = await this.client.send(new ConverseStreamCommand(params));
    for await (const event of response.stream!) {
      yield event;
    }
  }
}
```

**Step 2.2: Set up Bedrock Guardrails**
Create a Guardrail in the Bedrock console (or CDK) for the builder capability:
- Deny prompts asking the agent to delete all resources, modify IAM permissions beyond its scope, or exfiltrate secrets
- Apply the guardrailId + guardrailVersion to every Converse API call

**Step 2.3: Update OpenClaw config**
Set the environment variable in the ECS task definition:
```
OPENCLAW_MODEL_PROVIDER=bedrock
OPENCLAW_MODEL_ID=us.anthropic.claude-sonnet-4-6-v1:0
BEDROCK_GUARDRAIL_ID=your-guardrail-id
```

**Checkpoint:** Agent is responding using Bedrock. CloudWatch shows token usage. Guardrails are applied. Cost visible in AWS Cost Explorer.

---

### Phase 3 — Memory Upgrade: SQLite → AgentCore Memory

**Goal:** Replace the local SQLite semantic index with AgentCore Memory. Keep the MEMORY.md file format unchanged.

**Step 3.1: Write the AgentCore Memory adapter**
```python
# packages/aws-extensions/adapters/agentcore_memory.py
import boto3

class AgentCoreMemoryAdapter:
    def __init__(self, memory_id: str, agent_id: str):
        self.client = boto3.client("bedrock-agentcore", region_name="us-east-1")
        self.memory_id = memory_id
        self.agent_id = agent_id

    def ingest_event(self, session_id: str, content: str, role: str):
        """Ingest a conversation event — replaces SQLite write"""
        self.client.ingest_conversation_events(
            memoryId=self.memory_id,
            sessionId=f"{self.agent_id}:{session_id}",
            conversationEvents=[{
                "role": role,
                "content": [{"text": content}],
                "timestamp": __import__("datetime").datetime.utcnow().isoformat()
            }]
        )

    def retrieve(self, query: str, top_k: int = 10) -> list[str]:
        """Retrieve relevant memories — replaces SQLite BM25+vector search"""
        response = self.client.retrieve_memory_records(
            memoryId=self.memory_id,
            searchQuery=query,
            maxResults=top_k
        )
        return [record["content"]["text"] for record in response.get("memoryRecords", [])]

    def ingest_workspace_file(self, filename: str, content: str):
        """Bootstrap: ingest a workspace file (MEMORY.md, daily logs) into AgentCore"""
        self.client.ingest_conversation_events(
            memoryId=self.memory_id,
            sessionId=f"{self.agent_id}:workspace-bootstrap",
            conversationEvents=[{
                "role": "system",
                "content": [{"text": f"[{filename}]\n{content}"}],
                "timestamp": __import__("datetime").datetime.utcnow().isoformat()
            }]
        )
```

**Step 3.2: Bootstrap migration**
Run `scripts/migrate-memory.py` to ingest existing MEMORY.md and daily log files into AgentCore Memory for the agent.

**Step 3.3: Memory consolidation Lambda**
Create a nightly Lambda (EventBridge rule: `cron(0 2 * * ? *)`) that:
1. Retrieves all memories from AgentCore for the agent
2. Condenses them into a new MEMORY.md (using Claude Haiku 4.5 — cheap and fast)
3. Writes the updated MEMORY.md back to S3 and DynamoDB cache

**Checkpoint:** Agent memories persist across sessions using AgentCore. Local SQLite no longer required. Nightly consolidation keeps MEMORY.md fresh.

---

### Phase 4 — Scheduling + Webhooks

**Goal:** Replace Node-internal cron with EventBridge Scheduler. Replace localhost webhook endpoint with API Gateway.

**Step 4.1: EventBridge Scheduler for heartbeat**
```python
# infra/stacks/scheduler_stack.py
from aws_cdk import Stack, Duration, aws_scheduler as scheduler, aws_lambda as lambda_, aws_iam as iam
from constructs import Construct

class SchedulerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, heartbeat_lambda: lambda_.Function, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        scheduler_role = iam.Role(self, "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com")
        )
        heartbeat_lambda.grant_invoke(scheduler_role)

        scheduler.CfnSchedule(self, "HeartbeatSchedule",
            schedule_expression="rate(30 minutes)",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=heartbeat_lambda.function_arn,
                role_arn=scheduler_role.role_arn,
                input='{"agentId": "felix", "type": "heartbeat"}'
            )
        )
```

**Step 4.2: Webhook API Gateway**
```python
# In api_stack.py
from aws_cdk import aws_apigatewayv2 as apigw, aws_apigatewayv2_integrations as integrations

webhook_api = apigw.HttpApi(self, "WebhookAPI",
    api_name="openclaw-webhooks",
    description="Replaces localhost:18789/webhook/"
)
webhook_api.add_routes(
    path="/webhook/{proxy+}",
    methods=[apigw.HttpMethod.POST],
    integration=integrations.HttpLambdaIntegration("WebhookLambda", webhook_handler)
)
```

**Step 4.3: Migrate cron definitions**
Read all cron jobs from `~/.openclaw/cron/` and create corresponding EventBridge Scheduler rules via CDK. Store cron state (last-run, next-run) in DynamoDB `openclaw-cron` table.

**Checkpoint:** Heartbeat fires every 30 minutes via EventBridge. Webhooks are publicly accessible via API Gateway. Agent's automation behavior is unchanged.

---

### Phase 5 — Builder Capability (Net-New Feature)

**Goal:** Give the agent the ability to write CDK, deploy Lambda/ECS, and provision AWS infrastructure. This is the key differentiator over a standard local OpenClaw deploy.

**Step 5.1: Create the builder sub-agent using Strands SDK**
```python
# packages/aws-extensions/builder/builder_agent.py
from strands import Agent, tool
from strands.models import BedrockModel
import subprocess, tempfile, os, boto3

model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-6-v1:0",
    guardrail_config={"guardrailIdentifier": os.environ["BEDROCK_GUARDRAIL_ID"]}
)

@tool
def write_and_test_code(filename: str, code: str, test_command: str) -> dict:
    """Write code to a temp file and run tests via Bedrock Code Interpreter"""
    bedrock_runtime = boto3.client("bedrock-runtime")
    response = bedrock_runtime.invoke_model(
        modelId="anthropic.claude-sonnet-4-6-20251015-v1:0",
        body=__import__("json").dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "tools": [{"type": "computer_20250124", "name": "computer", "display_width_px": 1024, "display_height_px": 768}],
            "messages": [{"role": "user", "content": f"Write this code to {filename}:\n```\n{code}\n```\nThen run: {test_command}"}]
        })
    )
    return {"status": "tested", "filename": filename}

@tool
def deploy_cdk_stack(stack_name: str, cdk_code: str) -> dict:
    """Write a CDK stack file and deploy it"""
    with tempfile.TemporaryDirectory() as tmpdir:
        stack_file = os.path.join(tmpdir, "app.py")
        with open(stack_file, "w") as f:
            f.write(cdk_code)

        # Initialize CDK app in temp dir
        subprocess.run(["cdk", "init", "app", "--language", "python"], cwd=tmpdir, check=True)
        subprocess.run(["cdk", "synth"], cwd=tmpdir, check=True)
        result = subprocess.run(
            ["cdk", "deploy", "--require-approval", "never", "--outputs-file", "outputs.json"],
            cwd=tmpdir, capture_output=True, text=True
        )

        outputs = {}
        outputs_file = os.path.join(tmpdir, "outputs.json")
        if os.path.exists(outputs_file):
            with open(outputs_file) as f:
                outputs = __import__("json").load(f)

        return {"status": "deployed", "stack_name": stack_name, "outputs": outputs}

@tool
def deploy_lambda(function_name: str, handler_code: str, runtime: str = "python3.12") -> dict:
    """Deploy a Lambda function from code"""
    import zipfile, io
    lambda_client = boto3.client("lambda")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("handler.py", handler_code)
    zip_buffer.seek(0)

    try:
        lambda_client.update_function_code(
            FunctionName=function_name, ZipFile=zip_buffer.read()
        )
    except lambda_client.exceptions.ResourceNotFoundException:
        role_arn = os.environ["LAMBDA_EXECUTION_ROLE_ARN"]
        lambda_client.create_function(
            FunctionName=function_name,
            Runtime=runtime,
            Role=role_arn,
            Handler="handler.handler",
            Code={"ZipFile": zip_buffer.read()}
        )
    return {"status": "deployed", "function_name": function_name}

builder_agent = Agent(
    model=model,
    tools=[write_and_test_code, deploy_cdk_stack, deploy_lambda],
    system_prompt="""You are a builder agent. Your job is to write, test, and deploy AWS infrastructure and code.
You have tools to: write and test code, deploy CDK stacks, and deploy Lambda functions.
Always test code before deploying. Always review CDK synth output before deploying.
Write deployment outputs to memory so the supervisor agent can access them."""
)
```

**Step 5.2: Write the builder SKILL.md**
The supervisor OpenClaw agent needs to know when and how to delegate to the builder. Create `skills/aws-infrastructure-provisioning/SKILL.md` with clear instructions on when to invoke the builder sub-agent and what information to pass.

**Step 5.3: Builder IAM permission boundary**
The builder agent must not be able to escape its defined sandbox:
```python
# infra/constructs/builder_permissions.py
from aws_cdk import aws_iam as iam

def create_builder_boundary(scope, account_id: str) -> iam.ManagedPolicy:
    """
    IAM permission boundary for builder agent.
    Builder can provision resources but cannot touch IAM, billing, or other agents.
    """
    return iam.ManagedPolicy(scope, "BuilderBoundary",
        statements=[
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "lambda:*", "ecs:*", "ecr:*",
                    "apigateway:*", "cloudformation:*",
                    "s3:*", "dynamodb:*", "sqs:*", "sns:*",
                    "logs:*", "xray:*"
                ],
                resources=["*"],
                conditions={"StringEquals": {"aws:RequestedRegion": "us-east-1"}}
            ),
            iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=["iam:*", "organizations:*", "account:*", "billing:*"],
                resources=["*"]
            )
        ]
    )
```

**Checkpoint:** Supervisor agent can say "build me a webhook endpoint" and the builder sub-agent writes CDK code, tests it, deploys it, and reports back with the endpoint URL. Stack outputs are written to MEMORY.md.

---

### Phase 6 — AgentCore Identity + Multi-Tenant Auth

**Goal:** Replace OpenClaw's DM-pairing auth with proper OAuth. Enable multi-tenant deployments.

**Step 6.1: Cognito user pool per tenant**
```python
# infra/stacks/identity_stack.py
from aws_cdk import Stack, aws_cognito as cognito
from constructs import Construct

class IdentityStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.user_pool = cognito.UserPool(self, "AgentUserPool",
            user_pool_name="openclaw-users",
            self_sign_up_enabled=False,  # Invite-only
            sign_in_aliases=cognito.SignInAliases(email=True),
            password_policy=cognito.PasswordPolicy(min_length=12)
        )

        self.telegram_client = self.user_pool.add_client("TelegramClient",
            auth_flows=cognito.AuthFlow(user_password=True),
            generate_secret=True
        )
```

**Step 6.2: Telegram authorization middleware**
Map Telegram user IDs to Cognito user pool entries. On first DM, require verification code (keep OpenClaw's pairing behavior, just back it with Cognito).

**Checkpoint:** Only authorized users can interact with the agent. Multi-tenant deployments have isolated user pools.

---

### Phase 7 — Production Hardening

**Goal:** Observability, reliability, and CI/CD.

**Step 7.1: CloudWatch dashboards**
Create a dashboard showing: messages per hour, Bedrock token usage, memory operations, active sessions, error rate, builder deployment count.

**Step 7.2: Alarms**
- Agent unresponsive for > 5 minutes → SNS alert
- Bedrock error rate > 5% → SNS alert
- Builder deployment failure → SNS alert

**Step 7.3: GitHub Actions CI/CD**
```yaml
# .github/workflows/deploy.yml
name: Deploy openclaw-aws
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive  # Pull openclaw/core submodule
      - uses: aws-actions/configure-aws-credentials@v4
      - run: cd infra && cdk deploy --all --require-approval never
```

**Step 7.4: Load testing**
Send 100 concurrent messages and verify: ECS auto-scales, SQS buffers correctly, no messages dropped, memory consistent.

---

## Key Implementation Rules

1. **Never break OpenClaw's core behavior.** If you're not sure whether a change is safe, wrap it instead of replacing it.

2. **Keep the SKILL.md format sacred.** Skills are in natural language markdown. Do not convert them to code, JSON, or any other format.

3. **Workspace files are canonical.** The agent's identity lives in S3 workspace files. DynamoDB is a cache. AgentCore Memory is a semantic index. None of these replace the files.

4. **The adapter pattern is your friend.** For every OpenClaw component you want to extend (workspace loading, model calls, memory writes), implement an adapter that satisfies the same interface and swaps in transparently.

5. **Phase 0 is not optional.** Running OpenClaw locally before touching AWS is mandatory. If you haven't verified the OpenClaw behavior is correct, you won't know whether your AWS adaptations are correct either.

6. **Test locally first.** Use `--profile localstack` or actual dev AWS account, not production.

7. **IAM permission boundaries on all agent roles.** The builder agent's IAM role must have a permission boundary that prevents it from doing things like creating admin IAM roles or touching billing.

8. **Bedrock Guardrails on every model call.** Especially the builder agent. Apply the guardrail at the adapter level so it cannot be bypassed.

---

## Success Criteria

**Phase 0:** OpenClaw running locally on Telegram with custom SOUL.md. Agent responds correctly.

**Phase 1:** Same agent running on ECS Fargate. Workspace files served from S3. Telegram integration unchanged.

**Phase 2:** Model calls going through Bedrock. CloudWatch shows token usage. Guardrails applied.

**Phase 3:** Memory persists across sessions. MEMORY.md auto-consolidates nightly. No SQLite on the container.

**Phase 4:** Heartbeat fires via EventBridge every 30 minutes. Webhooks reachable at public HTTPS URL.

**Phase 5:** Agent can write and deploy a working Lambda function in response to a natural language instruction. Stack outputs appear in MEMORY.md.

**Phase 6:** Only authorized users can reach the agent. Auth works on Telegram.

**Phase 7:** End-to-end monitoring. CI/CD deploys on push to main. Load test passes.

---

## Reference: What Not to Build

The following things sound useful but should be deferred or avoided:

- **Custom skill injection engine** — OpenClaw's selective injection already works well; keep it
- **Custom embedding pipeline** — AgentCore Memory handles this; don't build your own
- **Custom orchestration framework** — Strands SDK + OpenClaw's sessions_spawn is sufficient
- **REST API for agent management** — the Telegram channel is the management interface; don't over-engineer it
- **Web UI** — OpenClaw has a web client; use it or keep Telegram; don't build a new one
- **Agent marketplace/registry** — scope creep; one solid agent first

---

*See also: `system_architecture.md` for full architectural diagrams · `aws_bedrock_capabilities.md` for complete API reference · `agent_skill_docs/` for individual skill documentation · `research_summary.md` for OpenClaw extensibility analysis*
