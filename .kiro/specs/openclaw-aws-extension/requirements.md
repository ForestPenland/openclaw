# Requirements Document

## Introduction

This document defines the requirements for extending the OpenClaw open-source AI agent framework with AWS services. The approach is "fork, don't rewrite" — preserve OpenClaw's core Gateway (including its native Telegram, Slack, WebSocket, heartbeat, and cron support), workspace files (SOUL.md, MEMORY.md, etc.), skill format, and multi-agent patterns, then inject AWS services at specific adapter seams. The AWS extension focuses on three provider adapters (Bedrock model provider, S3 workspace storage, AgentCore Memory), CDK infrastructure (ECS Fargate, DynamoDB, S3, EventBridge, CloudWatch), and the Builder sub-agent for autonomous infrastructure provisioning. The OpenClaw Gateway handles all channel integrations (Telegram, Slack, WebSocket) natively — no custom webhook Lambda pipeline is needed for messaging.

## Glossary

- **Gateway**: The OpenClaw server process (default port 18789) that natively handles message routing, sessions, Telegram/Slack/WebSocket channels, cron jobs, webhooks, and the heartbeat cycle
- **Workspace_Files**: The set of markdown files (SOUL.md, MEMORY.md, AGENTS.md, TOOLS.md, USER.md, IDENTITY.md, HEARTBEAT.md) that define an agent's identity, personality, memory, and operating instructions
- **S3_Workspace_Adapter**: A TypeScript adapter that serves workspace files from an S3 bucket instead of the local filesystem, implementing the same interface OpenClaw expects
- **Bedrock_Provider_Plugin**: A TypeScript module registered as an OpenClaw provider plugin that routes model invocations through the Amazon Bedrock Converse API instead of the direct Anthropic SDK
- **AgentCore_Memory_Adapter**: A Python adapter that replaces OpenClaw's local SQLite semantic index with Amazon Bedrock AgentCore Memory for cloud-scale semantic and episodic retrieval
- **Converse_API**: Amazon Bedrock's unified model invocation interface that works consistently across all supported foundation models
- **ConverseStream_API**: The streaming variant of the Converse API for real-time progressive response delivery
- **Bedrock_Guardrails**: Amazon Bedrock content filtering policies applied to model calls to prevent unsafe or unauthorized actions
- **AgentCore_Runtime**: Amazon Bedrock AgentCore's serverless agent execution environment supporting sessions up to 8 hours
- **AgentCore_Code_Interpreter**: Amazon Bedrock AgentCore's sandboxed code execution environment for Python, JavaScript, and TypeScript
- **Strands_SDK**: AWS open-source Python agent framework (Apache 2.0) for building production-ready AI agents with Bedrock
- **SKILL.md**: OpenClaw's natural language markdown format for defining agent capabilities, loaded via selective injection
- **MEMORY.md**: The curated ~100-line long-term memory file always loaded into the agent's system prompt at session start
- **HEARTBEAT.md**: A markdown checklist the agent reads every 30 minutes to decide whether autonomous action is needed
- **Supervisor_Agent**: The primary orchestrator agent that receives all incoming messages and delegates specialist tasks to sub-agents
- **Builder_Sub_Agent**: A specialist sub-agent running on AgentCore Runtime that writes, tests, and deploys AWS infrastructure (CDK stacks, Lambda functions, ECS services)
- **EventBridge_Scheduler**: AWS EventBridge Scheduler service used for heartbeat and nightly consolidation schedules
- **Permission_Boundary**: An IAM managed policy that sets the maximum permissions an agent role can have, preventing privilege escalation
- **Tenant**: An isolated operator context with its own workspace files, memory store, secrets, and IAM boundaries
- **Memory_Consolidation**: A nightly process that uses Claude Haiku to condense accumulated memories into a fresh MEMORY.md of ~100 lines
- **openclaw_json**: The OpenClaw configuration file (`openclaw.json`) that defines channel settings (Telegram bot token, Slack credentials, WebSocket options) for the Gateway's native channel handlers

## Requirements

### Requirement 1: S3 Workspace Adapter

**User Story:** As an operator, I want my agent's workspace files stored in S3 instead of the local filesystem, so that the agent can run on ECS Fargate without depending on local disk persistence.

#### Acceptance Criteria

1. WHEN the Gateway starts on ECS, THE S3_Workspace_Adapter SHALL sync all Workspace_Files from the configured S3 bucket and agent prefix to the local filesystem path expected by OpenClaw
2. WHEN the agent updates a mutable workspace file (MEMORY.md or HEARTBEAT.md), THE S3_Workspace_Adapter SHALL write the updated content to both the local filesystem and the corresponding S3 object within 5 seconds
3. THE S3_Workspace_Adapter SHALL preserve the exact markdown format of all Workspace_Files without modification during read or write operations
4. WHEN the S3 bucket is unreachable during startup, THE S3_Workspace_Adapter SHALL retry with exponential backoff up to 3 times and log a descriptive error if all retries fail
5. THE S3_Workspace_Adapter SHALL organize files under the S3 key prefix `{tenantId}/{agentId}/` to support multi-tenant deployments

### Requirement 2: Bedrock Provider Plugin

**User Story:** As an operator, I want model invocations routed through Amazon Bedrock's Converse API as an OpenClaw provider plugin, so that the Gateway uses Bedrock natively for all model calls with centralized cost tracking, cross-region inference, and Bedrock Guardrails.

#### Acceptance Criteria

1. THE Bedrock_Provider_Plugin SHALL be registered as an OpenClaw provider plugin so the Gateway routes model invocations through the Converse_API using the configured Bedrock model ID
2. WHEN a user-facing interaction requires a response, THE Bedrock_Provider_Plugin SHALL use the ConverseStream_API to deliver progressive streaming output through the Gateway's native channel handlers
3. THE Bedrock_Provider_Plugin SHALL apply the configured Bedrock_Guardrails identifier and version to every Converse_API call
4. WHEN the Converse_API returns a ThrottlingException, THE Bedrock_Provider_Plugin SHALL retry with exponential backoff up to 3 times with jitter
5. THE Bedrock_Provider_Plugin SHALL support cross-region inference profiles (model IDs prefixed with `us.`) for production deployments
6. THE Bedrock_Provider_Plugin SHALL report input token count, output token count, and model ID for every invocation to enable CloudWatch cost tracking
7. WHILE the model provider is configured as `anthropic` (local development), THE Bedrock_Provider_Plugin SHALL remain inactive and OpenClaw's native Anthropic SDK integration SHALL function without modification

### Requirement 3: AgentCore Memory Adapter

**User Story:** As an operator, I want the agent's semantic memory index backed by AgentCore Memory instead of local SQLite, so that memory persists across container restarts and scales to multiple agent instances.

#### Acceptance Criteria

1. WHEN a conversation turn completes, THE AgentCore_Memory_Adapter SHALL ingest the user message and agent response as conversation events into the configured AgentCore Memory store
2. WHEN the agent needs contextually relevant memories, THE AgentCore_Memory_Adapter SHALL retrieve up to 10 memory records via semantic search using the current user message as the query
3. THE AgentCore_Memory_Adapter SHALL preserve MEMORY.md as the canonical Tier 1 memory file, loading it in full at every session start
4. WHEN the memory store is created for the first time, THE AgentCore_Memory_Adapter SHALL configure both semantic and episodic memory strategies
5. WHEN running locally (Phase 0), THE AgentCore_Memory_Adapter SHALL remain inactive and OpenClaw's native SQLite memory system SHALL function without modification

### Requirement 4: Memory Consolidation

**User Story:** As an operator, I want nightly memory consolidation to keep MEMORY.md under ~100 lines, so that the agent's context window is not bloated with stale or redundant information.

#### Acceptance Criteria

1. WHEN the nightly consolidation schedule fires (cron: 0 2 \* _ ? _ UTC), THE Memory_Consolidation Lambda SHALL retrieve all memories from the past 24 hours and the current MEMORY.md content
2. THE Memory_Consolidation Lambda SHALL use Claude Haiku to produce an updated MEMORY.md that retains relevant facts, adds important new facts, removes outdated entries, and stays under 100 lines
3. WHEN the consolidation completes, THE Memory_Consolidation Lambda SHALL write the updated MEMORY.md to both S3 and the DynamoDB hot cache
4. THE Memory_Consolidation Lambda SHALL archive the raw session logs for the previous day to S3 under the key `memory-archive/{agentId}/{date}/memories.json`
5. IF the consolidation Lambda fails, THEN THE Memory_Consolidation Lambda SHALL log the error to CloudWatch and send an SNS alert without modifying the existing MEMORY.md

### Requirement 5: ECS Fargate Gateway Deployment

**User Story:** As an operator, I want the OpenClaw Gateway running on ECS Fargate with its native channel support, so that the agent is always available without depending on a local machine.

#### Acceptance Criteria

1. THE Gateway_Stack SHALL deploy the OpenClaw Gateway as a single Fargate task with 1024 CPU units and 2048 MiB memory
2. THE Gateway_Stack SHALL configure the ECS task role with permissions for S3 workspace read/write, Bedrock model invocation, DynamoDB access, and AgentCore operations
3. WHEN the Fargate task starts, THE Gateway container SHALL sync workspace files from S3 before starting the OpenClaw Gateway process
4. THE Gateway_Stack SHALL configure CloudWatch log streaming for the Gateway container with the log prefix `openclaw-gateway`
5. WHEN the Fargate task crashes or becomes unhealthy, THE ECS service SHALL restart the task automatically
6. THE Gateway container SHALL start the OpenClaw Gateway with `--bind lan` and `--allow-unconfigured` flags to accept connections within the VPC

### Requirement 6: Native Channel Configuration

**User Story:** As an operator, I want Telegram, Slack, and WebSocket channels configured through OpenClaw's native channel system (openclaw.json), so that the Gateway handles all messaging without custom webhook Lambdas or SQS queues.

#### Acceptance Criteria

1. THE Gateway container SHALL configure Telegram channel support via openclaw_json with the bot token retrieved from Secrets Manager or environment configuration at startup
2. WHEN a Telegram message arrives, THE Gateway SHALL process the message using OpenClaw's built-in Telegram channel handler, including webhook registration, message parsing, and response delivery
3. THE Gateway container SHALL configure Slack channel support via openclaw_json so the Gateway handles Slack events natively using OpenClaw's built-in Slack handler
4. THE Gateway SHALL expose its native WebSocket endpoint on the configured port for web chat clients connecting through the ECS service
5. WHEN the operator needs to add or modify a channel, THE system SHALL support updating the openclaw_json configuration and restarting the Gateway container without redeploying the CDK stack
6. THE Gateway SHALL use OpenClaw's native heartbeat and cron scheduling for periodic tasks that run within the Gateway process

### Requirement 7: EventBridge Scheduling

**User Story:** As an operator, I want the nightly memory consolidation and agent-created schedules managed by EventBridge Scheduler, so that scheduled tasks that require Lambda invocations fire reliably regardless of Gateway container lifecycle.

#### Acceptance Criteria

1. THE Scheduler*Stack SHALL create a nightly EventBridge Scheduler rule (cron: 0 2 \* * ? \_ UTC) that invokes the Memory_Consolidation Lambda
2. WHEN the agent creates a new schedule via the `set_reminder` tool, THE scheduling system SHALL create a corresponding EventBridge Scheduler rule in the `openclaw-agent-schedules` group
3. WHEN the agent creates a one-time schedule, THE scheduling system SHALL configure the EventBridge rule with `ActionAfterCompletion: DELETE` so it auto-cleans after firing
4. THE Scheduler_Stack SHALL grant the EventBridge Scheduler execution role permission to invoke the target Lambda functions

### Requirement 8: Identity and Secrets Management

**User Story:** As an operator, I want credentials stored in Secrets Manager and user authentication handled by OpenClaw's native pairing system, so that no secrets are hardcoded and only authorized users can interact with the agent.

#### Acceptance Criteria

1. THE Identity_Stack SHALL create a Cognito user pool with invite-only sign-up and email-based sign-in for administrative access
2. THE identity system SHALL store all external service credentials (Telegram bot token, Slack bot token, GitHub token) in AWS Secrets Manager under the `openclaw/` namespace
3. THE identity system SHALL retrieve credentials from Secrets Manager at container startup and inject them into the Gateway's openclaw_json configuration
4. THE Gateway SHALL use OpenClaw's native user pairing and approval system for Telegram and Slack user authentication
5. THE Identity_Stack SHALL configure a Cognito app client for administrative API access with user-password authentication flow

### Requirement 9: Agent Orchestration

**User Story:** As an operator, I want a supervisor-specialist multi-agent architecture where the supervisor delegates tasks to specialist sub-agents, so that complex work is handled by purpose-built agents.

#### Acceptance Criteria

1. THE Supervisor_Agent SHALL receive all incoming user messages from the Gateway and decide whether to handle them directly or delegate to a specialist sub-agent
2. WHEN the Supervisor_Agent delegates a task, THE orchestration system SHALL invoke the appropriate specialist sub-agent (infrastructure, code, communications, or research) via AgentCore_Runtime
3. WHEN a specialist sub-agent completes its task, THE orchestration system SHALL return the result to the Supervisor_Agent for aggregation and user response
4. THE orchestration system SHALL limit delegation depth to 2 levels (supervisor → specialist) to prevent hard-to-debug chains
5. WHEN a specialist sub-agent fails after 2 retries, THE orchestration system SHALL fall back to the Supervisor_Agent handling the task directly
6. THE orchestration system SHALL use consistent session IDs within a multi-step task, appending a suffix `{root_session}-{agent_type}-{step}` for sub-agent calls to enable end-to-end tracing

### Requirement 10: Builder Capability — Infrastructure Provisioning

**User Story:** As an operator, I want the agent to write CDK code, deploy Lambda functions, and provision AWS infrastructure in response to natural language instructions, so that the agent can build products autonomously.

#### Acceptance Criteria

1. WHEN the operator instructs the agent to build infrastructure, THE Supervisor_Agent SHALL spawn a Builder_Sub_Agent via AgentCore_Runtime
2. THE Builder_Sub_Agent SHALL generate CDK Python code, test it via AgentCore_Code_Interpreter, run `cdk synth` to produce a CloudFormation template, and deploy via `cdk deploy`
3. THE Builder_Sub_Agent SHALL apply Bedrock_Guardrails to every model call that could trigger infrastructure changes
4. WHEN a CDK deployment completes, THE Builder_Sub_Agent SHALL write the stack outputs (API URLs, Lambda ARNs, resource identifiers) to MEMORY.md in S3
5. THE Builder_Sub_Agent SHALL prefix all agent-deployed resources with `agent-` to enable scoped IAM permissions and easy identification
6. THE Builder_Sub_Agent's IAM role SHALL have a Permission_Boundary that denies IAM, Organizations, Account, and Billing actions
7. WHEN a CDK deployment fails, THE Builder_Sub_Agent SHALL report the CloudFormation failure events to the Supervisor_Agent and notify the operator

### Requirement 11: Builder Capability — Code Generation and Deployment

**User Story:** As an operator, I want the agent to write, test, and deploy Lambda function code through a structured pipeline, so that code is always tested in a sandbox before reaching production.

#### Acceptance Criteria

1. WHEN the agent generates code for deployment, THE code generation pipeline SHALL write the code and unit tests, execute tests in AgentCore_Code_Interpreter, and only proceed to deployment if tests pass
2. IF tests fail in the Code Interpreter, THEN THE code generation pipeline SHALL revise the code using the error output and re-test up to 3 iterations before reporting failure
3. WHEN deploying a Lambda function, THE deployment tool SHALL package the handler code into a zip, store the artifact in S3 for audit trail, and create or update the Lambda function
4. THE deployment tool SHALL wait for the Lambda function to reach `Active` state before returning success
5. WHEN deploying a containerized service, THE deployment tool SHALL build a Docker image, push it to ECR, and create or update the ECS Fargate service
6. THE deployment tool SHALL store the previous Lambda version before deploying, enabling rollback via Lambda aliases

### Requirement 12: Storage Layer

**User Story:** As an operator, I want workspace files, skills, and session state stored in S3 and DynamoDB, so that all data persists across container restarts and supports multi-tenant isolation.

#### Acceptance Criteria

1. THE Storage_Stack SHALL create a versioned S3 bucket for workspace files with the key structure `{tenantId}/{agentId}/{filename}`
2. THE Storage_Stack SHALL create a DynamoDB table `openclaw-memory` with partition key `agent_id` and sort key `file_key` for hot-tier MEMORY.md caching with sub-10ms read latency
3. THE Storage_Stack SHALL create a DynamoDB table `openclaw-sessions` with partition key `agent_id` and sort key `session_id` with a TTL attribute for automatic session cleanup
4. THE Storage_Stack SHALL create a DynamoDB table `openclaw-agents` with partition key `agent_id` for the agent registry
5. THE Storage_Stack SHALL configure all S3 buckets with `BlockPublicAccess.BLOCK_ALL` and all DynamoDB tables with `PAY_PER_REQUEST` billing

### Requirement 13: Model Routing by Task Complexity

**User Story:** As an operator, I want the system to route model invocations to the most cost-effective Bedrock model based on task complexity, so that simple tasks use cheaper models and complex tasks use more capable ones.

#### Acceptance Criteria

1. WHEN a task matches simple patterns (summarize, classify, extract, translate, format) and is under 500 characters, THE model router SHALL select Claude Haiku
2. WHEN a task matches complex patterns (architect, design, strategy, plan infrastructure), THE model router SHALL select Claude Opus
3. WHEN a task does not match simple or complex patterns, THE model router SHALL default to Claude Sonnet
4. THE model router SHALL use the Memory_Consolidation Lambda with Claude Haiku to minimize consolidation costs

### Requirement 14: Observability and Production Hardening

**User Story:** As an operator, I want CloudWatch dashboards, alarms, and structured logging, so that I can monitor agent health, costs, and performance in production.

#### Acceptance Criteria

1. THE observability system SHALL create a CloudWatch dashboard showing messages per hour, Bedrock token usage, memory operations count, active sessions, error rate, and builder deployment count
2. WHEN the Gateway ECS task is unresponsive for more than 5 minutes, THE alarm system SHALL send an SNS notification
3. WHEN the Bedrock error rate exceeds 5% over a 5-minute window, THE alarm system SHALL send an SNS notification
4. WHEN a builder deployment fails, THE alarm system SHALL send an SNS notification with the stack name and failure reason
5. THE Gateway container SHALL emit structured JSON logs to CloudWatch including session ID, agent ID, message channel, and response latency

### Requirement 15: CDK Infrastructure as Code

**User Story:** As an operator, I want all AWS infrastructure defined in CDK Python stacks, so that the entire deployment is reproducible, version-controlled, and deployable via CI/CD.

#### Acceptance Criteria

1. THE CDK application SHALL define separate stacks for Gateway, Storage, Memory, Identity, Scheduler, and Builder capabilities
2. THE CDK application SHALL define a reusable `OpenClawAgent` construct that encapsulates one agent's ECS task, workspace bucket prefix, and memory store
3. THE CDK application SHALL define a `TenantIsolation` construct that creates per-tenant IAM boundaries and S3 prefix scoping
4. THE CDK application SHALL support deployment via `cdk deploy --all` from the `infra/` directory
5. THE CDK application SHALL use Python 3.12+ and `aws-cdk-lib` as the sole CDK dependency

### Requirement 16: Workspace File Format Preservation

**User Story:** As an operator, I want the workspace file format (SOUL.md, MEMORY.md, AGENTS.md, etc.) to remain identical to upstream OpenClaw, so that community skills, workspace templates, and ClawHub integrations continue to work.

#### Acceptance Criteria

1. THE system SHALL load and assemble workspace files into the system prompt using the same ordering and format as OpenClaw core (IDENTITY → SOUL → AGENTS → USER → MEMORY → TOOLS)
2. THE system SHALL preserve the SKILL.md natural language markdown format for all AWS-specific skills without converting to code, JSON, or any other format
3. WHEN workspace files are stored in S3, THE S3_Workspace_Adapter SHALL store them as plain UTF-8 text with the original filename preserved in the S3 key
4. THE system SHALL support the `memory/YYYY-MM-DD.md` daily log format in S3 under the agent's workspace prefix

### Requirement 17: Multi-Tenant Isolation

**User Story:** As an operator scaling to multiple users, I want per-tenant isolation of workspace files, memory stores, secrets, and IAM permissions, so that tenants cannot access each other's data.

#### Acceptance Criteria

1. THE TenantIsolation construct SHALL create per-tenant S3 key prefixes and IAM policies that restrict each tenant's agent to its own prefix
2. THE TenantIsolation construct SHALL ensure each tenant's agent uses a separate AgentCore Memory store
3. THE TenantIsolation construct SHALL scope Secrets Manager access to the `openclaw/{tenantId}/` namespace per tenant
4. WHEN a new tenant is provisioned, THE system SHALL create the tenant's workspace seed files in S3 from the default templates in `workspace-seeds/`

### Requirement 18: Local Development Compatibility

**User Story:** As a developer, I want to run OpenClaw locally with zero AWS dependencies for Phase 0 development, so that I can verify agent behavior before deploying to AWS.

#### Acceptance Criteria

1. WHILE the deployment mode is `local`, THE system SHALL use OpenClaw's native local filesystem for workspace files, SQLite for memory, direct Anthropic SDK for model calls, and Node-internal cron for scheduling
2. THE system SHALL provide a `scripts/test-local.sh` script that starts OpenClaw locally and verifies the Gateway responds on `ws://127.0.0.1:18789`
3. THE system SHALL provide a `scripts/seed-workspace.py` script that uploads default workspace file templates from `workspace-seeds/` to the configured S3 bucket and agent prefix
4. THE system SHALL provide a `scripts/migrate-memory.py` script that ingests existing local MEMORY.md and daily log files into AgentCore Memory for cloud migration
