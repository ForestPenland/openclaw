# Implementation Plan: OpenClaw AWS Extension

## Overview

This plan follows the 8 build phases (Phase 0–7) from the design document. Each phase produces a functional system boundary. TypeScript adapters use `vitest` + `fast-check`; Python components use `pytest` + `hypothesis`. CDK stacks use `aws_cdk.assertions`. Git commits at each checkpoint.

## Tasks

- [x] 1. Phase 0 — Local OpenClaw Verification & Project Scaffolding
  - [x] 1.1 Create directory structure for AWS extension code
    - Create `adapters/`, `agents/`, `lambdas/`, `infra/stacks/`, `infra/constructs/`, `tests/unit/adapters/`, `tests/unit/lambdas/`, `tests/unit/agents/`, `tests/unit/messaging/`, `tests/property/`, `workspace-seeds/`, `scripts/`
    - Create placeholder `workspace-seeds/SOUL.md`, `MEMORY.md`, `HEARTBEAT.md`, `AGENTS.md`, `TOOLS.md`, `USER.md`, `IDENTITY.md` with minimal markdown content
    - _Requirements: 17.1, 17.2, 17.3, 19.1_

  - [x] 1.2 Create `scripts/test-local.sh` for Phase 0 local verification
    - Script starts OpenClaw locally and verifies the Gateway responds on `ws://127.0.0.1:18789`
    - Exit 0 on success, exit 1 on failure with descriptive error
    - _Requirements: 19.1, 19.2_

  - [x] 1.3 Create `scripts/seed-workspace.py` for S3 workspace seeding
    - Reads templates from `workspace-seeds/`, uploads to configured S3 bucket under `{tenantId}/{agentId}/` prefix
    - Accepts `--bucket`, `--tenant-id`, `--agent-id` CLI arguments
    - _Requirements: 18.4, 19.3_

  - [x] 1.4 Create `scripts/migrate-memory.py` for local-to-cloud memory migration
    - Reads local MEMORY.md and `memory/YYYY-MM-DD.md` daily logs
    - Ingests into AgentCore Memory via boto3
    - Accepts `--memory-store-id`, `--agent-id`, `--workspace-path` CLI arguments
    - _Requirements: 19.4_

- [x] 2. Checkpoint — Verify Phase 0 scaffolding
  - Ensure directory structure is correct, scripts are executable, workspace seed files exist. Commit: `feat: phase 0 scaffolding and local verification scripts`. Ask the user if questions arise.

- [x] 3. Phase 1 — S3 Workspace Adapter
  - [x] 3.1 Implement S3 Workspace Adapter (`adapters/s3-workspace.ts`)
    - Implement `WorkspaceAdapter` interface: `syncFromS3`, `readFile`, `writeFile`, `listFiles`
    - On startup: `GetObject` for each file in `{tenantId}/{agentId}/` prefix → write to local path
    - On write (MEMORY.md, HEARTBEAT.md): write local first, then `PutObject` to S3 (fire-and-forget with error logging)
    - Retry with exponential backoff (base 1s, max 3 retries) on S3 errors during startup sync
    - Files stored as plain UTF-8 with original filename in S3 key
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 3.2 Write property tests for S3 Workspace Adapter (`tests/property/s3-workspace.prop.ts`)
    - **Property 1: S3 Workspace File Round-Trip** — writing files and reading them back produces byte-identical content
    - **Validates: Requirements 1.1, 1.3, 17.1, 17.3**

  - [ ]* 3.3 Write property test for dual-write consistency (`tests/property/s3-workspace.prop.ts`)
    - **Property 2: Dual-Write Consistency** — after write completes, local FS and S3 content are identical
    - **Validates: Requirements 1.2, 4.3**

  - [ ]* 3.4 Write property test for S3 key structure (`tests/property/s3-workspace.prop.ts`)
    - **Property 3: S3 Key Structure** — for any tenantId, agentId, filename, the key equals `{tenantId}/{agentId}/{filename}`; daily logs match `{tenantId}/{agentId}/memory/YYYY-MM-DD.md`
    - **Validates: Requirements 1.5, 16.3, 16.4**

  - [ ]* 3.5 Write unit tests for S3 Workspace Adapter (`tests/unit/adapters/s3-workspace.test.ts`)
    - Test startup sync with mocked S3 client
    - Test exponential backoff retry on S3 errors
    - Test write failure logging (fire-and-forget behavior)
    - Test empty workspace (no files in prefix)
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 4. Phase 1 — Bedrock Model Adapter
  - [x] 4.1 Implement Bedrock Model Adapter (`adapters/bedrock-model.ts`)
    - Implement `ModelAdapter` interface: `invoke`, `invokeStream`
    - Every call includes `guardrailConfig` from environment config
    - Cross-region inference: model IDs prefixed with `us.` passed through as-is
    - ThrottlingException: retry with exponential backoff + jitter (base 500ms, max 3 retries)
    - Emit token metrics after every call: `inputTokens`, `outputTokens`, `modelId`
    - When `provider=anthropic`: adapter not instantiated, OpenClaw native SDK path used
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

  - [ ]* 4.2 Write property tests for Bedrock Model Adapter (`tests/property/bedrock-model.prop.ts`)
    - **Property 4: Converse API Request Formation** — every bedrock invocation produces valid Converse API request with model ID (including `us.` prefix) and guardrailConfig
    - **Validates: Requirements 2.1, 2.3, 2.5, 10.3**

  - [ ]* 4.3 Write property test for token metrics emission (`tests/property/bedrock-model.prop.ts`)
    - **Property 5: Token Metrics Emission** — every response emits metrics with non-negative `inputTokens`, non-negative `outputTokens`, non-empty `modelId`
    - **Validates: Requirements 2.6**

  - [ ]* 4.4 Write unit tests for Bedrock Model Adapter (`tests/unit/adapters/bedrock-model.test.ts`)
    - Test ThrottlingException retry with jitter
    - Test ModelNotReadyException retry
    - Test ValidationException immediate failure
    - Test streaming response assembly
    - Test provider=anthropic bypass
    - _Requirements: 2.1, 2.2, 2.4, 2.7_

- [x] 5. Phase 1 — Model Router
  - [x] 5.1 Implement Model Router (`adapters/model-router.ts`)
    - Implement `ModelRouter` interface: `selectModel`
    - Routing rules: consolidation → Haiku; simple patterns + <500 chars → Haiku; complex patterns → Opus; default → Sonnet
    - Use regex patterns from design: `SIMPLE_PATTERNS`, `COMPLEX_PATTERNS`, `CHAR_THRESHOLD`, `MODEL_MAP`
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

  - [ ]* 5.2 Write property tests for Model Router (`tests/property/model-router.prop.ts`)
    - **Property 20: Model Router Selection** — deterministic selection: Haiku for simple+short or consolidation; Opus for complex; Sonnet for default
    - **Validates: Requirements 14.1, 14.2, 14.3, 14.4**

  - [ ]* 5.3 Write unit tests for Model Router (`tests/unit/adapters/model-router.test.ts`)
    - Test each routing rule with specific examples
    - Test boundary at 500 characters
    - Test pattern matching edge cases
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

- [-] 6. Checkpoint — Verify Phase 1 adapters
  - Ensure all tests pass for S3 Workspace Adapter, Bedrock Model Adapter, and Model Router. Commit: `feat: phase 1 S3 workspace, Bedrock model, and model router adapters`. Ask the user if questions arise.

- [ ] 7. Phase 2 — AgentCore Memory Adapter
  - [~] 7.1 Implement AgentCore Memory Adapter (`adapters/agentcore_memory.py`)
    - Implement `AgentCoreMemoryAdapter` class: `__init__`, `ingest`, `retrieve`, `create_store`
    - Ingest: store user message + agent response as conversation events after each turn
    - Retrieve: semantic search using current user message, return up to 10 records
    - MEMORY.md remains Tier 1: loaded in full at session start from S3, not from AgentCore
    - When running locally (Phase 0): adapter inactive, SQLite memory works unchanged
    - Graceful degradation: log warning and continue if memory store unreachable
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 7.2 Write property tests for AgentCore Memory (`tests/property/memory.prop.py`)
    - **Property 6: Memory Retrieval Bound** — semantic search returns at most 10 records
    - **Validates: Requirements 3.2**

  - [ ]* 7.3 Write property test for memory ingest completeness (`tests/property/memory.prop.py`)
    - **Property 27: Memory Ingest Completeness** — every completed conversation turn ingests both user message and agent response
    - **Validates: Requirements 3.1**

  - [ ]* 7.4 Write unit tests for AgentCore Memory Adapter (`tests/unit/adapters/agentcore_memory_test.py`)
    - Test ingest with mocked AgentCore client
    - Test retrieve returns correct format
    - Test create_store with semantic + episodic strategies
    - Test graceful degradation when store unreachable
    - Test inactive state in local mode
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [ ] 8. Phase 2 — Memory Consolidation Lambda
  - [~] 8.1 Implement Memory Consolidation Lambda (`lambdas/memory_consolidation.py`)
    - Retrieve all memories from past 24 hours via AgentCore Memory
    - Read current MEMORY.md from S3
    - Invoke Claude Haiku to produce updated MEMORY.md (≤100 lines)
    - Write updated MEMORY.md to S3 and DynamoDB hot cache
    - Archive raw session logs to S3: `memory-archive/{agentId}/{date}/memories.json`
    - On any error: log to CloudWatch, send SNS alert, leave existing MEMORY.md untouched
    - Truncate to 100 lines if output exceeds limit, log warning
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [ ]* 8.2 Write property test for consolidation line limit (`tests/property/memory.prop.py`)
    - **Property 7: Memory Consolidation Line Limit** — output MEMORY.md contains at most 100 lines
    - **Validates: Requirements 4.2**

  - [ ]* 8.3 Write property test for archive key format (`tests/property/memory.prop.py`)
    - **Property 8: Memory Archive Key Format** — archive key matches `memory-archive/{agentId}/{YYYY-MM-DD}/memories.json`
    - **Validates: Requirements 4.4**

  - [ ]* 8.4 Write unit tests for Memory Consolidation Lambda (`tests/unit/lambdas/consolidation_test.py`)
    - Test happy path: retrieve → consolidate → write → archive
    - Test failure mode: leave MEMORY.md untouched on error
    - Test truncation when output exceeds 100 lines
    - Test SNS alert on failure
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [ ] 9. Checkpoint — Verify Phase 2 memory components
  - Ensure all tests pass for AgentCore Memory Adapter and Memory Consolidation Lambda. Commit: `feat: phase 2 AgentCore memory adapter and consolidation lambda`. Ask the user if questions arise.

- [ ] 10. Phase 3 — CDK Storage Stack
  - [~] 10.1 Implement CDK Storage Stack (`infra/stacks/storage_stack.py`)
    - Create versioned S3 bucket for workspace files with key structure `{tenantId}/{agentId}/{filename}`
    - Create separate S3 bucket for skills registry with `BlockPublicAccess.BLOCK_ALL`
    - Create S3 bucket for artifacts
    - Create DynamoDB table `openclaw-memory` (PK: `agent_id`, SK: `file_key`, PAY_PER_REQUEST)
    - Create DynamoDB table `openclaw-sessions` (PK: `agent_id`, SK: `session_id`, TTL attribute, PAY_PER_REQUEST)
    - Create DynamoDB table `openclaw-agents` (PK: `agent_id`, PAY_PER_REQUEST)
    - Create DynamoDB table `openclaw-dedup` (PK: `webhook_id`, TTL attribute, PAY_PER_REQUEST)
    - Create DynamoDB table `openclaw-connections` (PK: `connection_id`, TTL attribute, PAY_PER_REQUEST)
    - All S3 buckets with `BlockPublicAccess.BLOCK_ALL`, all DynamoDB tables with `PAY_PER_REQUEST`
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

  - [ ]* 10.2 Write CDK assertion tests for Storage Stack (`tests/unit/infra/storage_stack_test.py`)
    - Assert S3 buckets have versioning enabled and public access blocked
    - Assert DynamoDB tables have correct key schemas and billing mode
    - Assert TTL attributes configured on sessions, dedup, and connections tables
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

- [ ] 11. Phase 3 — CDK Gateway Stack & ECS Fargate
  - [~] 11.1 Implement CDK Gateway Stack (`infra/stacks/gateway_stack.py`)
    - Deploy OpenClaw Gateway as single Fargate task: 512 CPU units, 1024 MiB memory
    - Configure ECS task role with permissions for S3, Bedrock, DynamoDB, AgentCore
    - Configure CloudWatch log streaming with `openclaw-gateway` log prefix
    - Configure ECS service with automatic task restart on crash/unhealthy
    - Container syncs workspace files from S3 before starting Gateway process
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 11.2 Write CDK assertion tests for Gateway Stack (`tests/unit/infra/gateway_stack_test.py`)
    - Assert Fargate task has 512 CPU and 1024 MiB memory
    - Assert task role has S3, Bedrock, DynamoDB, AgentCore permissions
    - Assert CloudWatch log group with `openclaw-gateway` prefix
    - Assert ECS service has desired count and health check
    - _Requirements: 5.1, 5.2, 5.4, 5.5_

- [ ] 12. Phase 3 — CDK Identity Stack
  - [~] 12.1 Implement CDK Identity Stack (`infra/stacks/identity_stack.py`)
    - Create Cognito user pool with invite-only sign-up and email-based sign-in
    - Create Cognito app client for Telegram channel with user-password auth flow
    - Create Secrets Manager secrets under `openclaw/` namespace for external service credentials
    - _Requirements: 8.1, 8.3, 8.6_

  - [ ]* 12.2 Write property test for secrets namespace scoping (`tests/property/secrets.prop.ts`)
    - **Property 12: Secrets Manager Namespace Scoping** — every secret name begins with `openclaw/`
    - **Validates: Requirements 8.3**

  - [ ]* 12.3 Write CDK assertion tests for Identity Stack (`tests/unit/infra/identity_stack_test.py`)
    - Assert Cognito user pool with invite-only sign-up
    - Assert app client with user-password auth flow
    - Assert Secrets Manager secrets under `openclaw/` namespace
    - _Requirements: 8.1, 8.3, 8.6_

- [ ] 13. Phase 3 — Tenant Isolation Construct & CDK App Entry Point
  - [~] 13.1 Implement Tenant Isolation construct (`infra/constructs/tenant_isolation.py`)
    - Create per-tenant S3 key prefixes and IAM policies restricting each tenant to its own prefix
    - Ensure each tenant uses a separate AgentCore Memory store
    - Scope Secrets Manager access to `openclaw/{tenantId}/` namespace per tenant
    - _Requirements: 18.1, 18.2, 18.3_

  - [~] 13.2 Implement OpenClawAgent reusable construct (`infra/constructs/openclaw_agent.py`)
    - Encapsulate one agent's ECS task, workspace bucket prefix, and memory store
    - _Requirements: 16.2_

  - [~] 13.3 Create CDK app entry point (`infra/app.py`) and `infra/requirements.txt`
    - Wire all stacks together, support `cdk deploy --all`
    - `aws-cdk-lib` as sole CDK dependency, Python 3.12+
    - _Requirements: 16.1, 16.4, 16.5_

  - [ ]* 13.4 Write property test for tenant isolation (`tests/property/tenant-isolation.prop.py`)
    - **Property 22: Tenant Isolation** — for any two distinct tenant IDs: S3 prefixes are disjoint, memory store IDs differ, Secrets Manager ARNs are scoped to non-overlapping namespaces
    - **Validates: Requirements 18.1, 18.2, 18.3**

  - [ ]* 13.5 Write CDK assertion tests for Tenant Isolation (`tests/unit/infra/tenant_isolation_test.py`)
    - Assert per-tenant IAM policies restrict S3 access to tenant prefix
    - Assert separate memory store per tenant
    - Assert Secrets Manager scoped to `openclaw/{tenantId}/`
    - _Requirements: 18.1, 18.2, 18.3_

- [ ] 14. Checkpoint — Verify Phase 3 CDK stacks
  - Ensure all CDK assertion tests pass, `cdk synth` produces valid templates. Commit: `feat: phase 3 CDK stacks — storage, gateway, identity, tenant isolation`. Ask the user if questions arise.

- [ ] 15. Phase 4 — Webhook Lambda Handler
  - [~] 15.1 Implement Webhook Lambda Handler (`lambdas/webhook_handler.py`)
    - Extract platform from path: `/webhook/{platform}`
    - Verify platform-specific signature (HMAC-SHA256) per platform: Telegram (bot token), Slack (`X-Slack-Signature` with `v0:timestamp:body`), GitHub (`X-Hub-Signature-256`)
    - Check DynamoDB dedup table for webhook ID; if duplicate return 200
    - If new: store ID in dedup table (TTL 24h), forward to SQS
    - Retrieve secrets from Secrets Manager via cached client
    - Return HTTP 401 on signature verification failure, log rejected request
    - _Requirements: 6.3, 6.4, 6.6, 8.4, 8.5, 20.1, 20.2, 20.3_

  - [ ]* 15.2 Write property tests for webhook signature verification (`tests/property/webhook.prop.py`)
    - **Property 9: Webhook Signature Verification** — correct HMAC-SHA256 returns true, any other signature returns false, for all platforms
    - **Validates: Requirements 6.4, 8.5**

  - [ ]* 15.3 Write property test for webhook idempotency (`tests/property/webhook.prop.py`)
    - **Property 23: Webhook Idempotency** — processing same webhook ID twice produces same side effects as once; second invocation returns 200 without downstream processing
    - **Validates: Requirements 20.1, 20.2**

  - [ ]* 15.4 Write property test for deduplication TTL (`tests/property/webhook.prop.py`)
    - **Property 24: Deduplication TTL** — every new dedup entry has TTL set to current time + 24 hours (epoch seconds)
    - **Validates: Requirements 20.3**

  - [ ]* 15.5 Write unit tests for Webhook Lambda Handler (`tests/unit/lambdas/webhook_handler_test.py`)
    - Test Telegram signature verification with known good/bad signatures
    - Test Slack signature verification with `v0:timestamp:body` format
    - Test GitHub signature verification
    - Test dedup check: new webhook processed, duplicate returns 200
    - Test SQS forwarding on new webhook
    - Test HTTP 401 on bad signature
    - _Requirements: 6.3, 6.4, 6.6, 8.5, 20.1, 20.2_

- [ ] 16. Phase 4 — CDK API Stack
  - [~] 16.1 Implement CDK API Stack (`infra/stacks/api_stack.py`)
    - Create HTTP API Gateway with route `POST /webhook/{proxy+}` forwarding to webhook Lambda
    - Create WebSocket API Gateway proxying to OpenClaw Gateway WebSocket server
    - Configure API Gateway throttling: 100 requests/second per route
    - Wire webhook Lambda with SQS, DynamoDB dedup table, and Secrets Manager permissions
    - _Requirements: 6.1, 6.2, 6.5_

  - [ ]* 16.2 Write CDK assertion tests for API Stack (`tests/unit/infra/api_stack_test.py`)
    - Assert HTTP API Gateway with webhook route
    - Assert WebSocket API Gateway exists
    - Assert throttling configuration at 100 req/s
    - Assert Lambda has SQS, DynamoDB, Secrets Manager permissions
    - _Requirements: 6.1, 6.2, 6.5_

- [ ] 17. Phase 4 — Messaging Handlers
  - [~] 17.1 Implement Telegram message handler (`adapters/telegram-handler.ts`)
    - Parse webhook payload, check user authorization
    - Route message text to Supervisor Agent
    - Split responses >4096 chars into chunks, send sequentially with 100ms delay
    - Handle voice notes: download audio, transcribe via Amazon Transcribe, route text
    - _Requirements: 12.1, 12.2, 12.3_

  - [ ]* 17.2 Write property test for Telegram message chunking (`tests/property/messaging.prop.ts`)
    - **Property 18: Telegram Message Chunking** — if response >4096 chars, split into chunks ≤4096 each, concatenation equals original
    - **Validates: Requirements 12.2**

  - [~] 17.3 Implement Slack event handler (`adapters/slack-handler.ts`)
    - Verify request signature, ignore bot messages to prevent loops
    - Parse event, route message to Supervisor Agent
    - _Requirements: 12.4_

  - [ ]* 17.4 Write property test for Slack bot message filtering (`tests/property/messaging.prop.ts`)
    - **Property 19: Slack Bot Message Filtering** — bot-sender events are not routed to Supervisor Agent
    - **Validates: Requirements 12.4**

  - [~] 17.5 Implement WebSocket handler (`adapters/websocket-handler.ts`)
    - Maintain connection state in DynamoDB with 1-hour TTL
    - Stream agent responses back to client as progressive chunks
    - Clean up DynamoDB entry on connection drop
    - _Requirements: 12.5_

  - [ ]* 17.6 Write property test for WebSocket connection TTL (`tests/property/messaging.prop.ts`)
    - **Property 26: WebSocket Connection TTL** — every new connection stored with TTL = current time + 1 hour (epoch seconds)
    - **Validates: Requirements 12.5**

  - [ ]* 17.7 Write unit tests for messaging handlers (`tests/unit/messaging/telegram.test.ts`, `tests/unit/messaging/slack.test.ts`, `tests/unit/messaging/websocket.test.ts`)
    - Test Telegram payload parsing and chunking with specific examples
    - Test Slack signature verification and bot filtering
    - Test WebSocket connection lifecycle (connect, message, disconnect)
    - _Requirements: 12.1, 12.2, 12.4, 12.5_

- [ ] 18. Phase 4 — Workspace Assembly & Structured Logging
  - [~] 18.1 Implement workspace file assembly for system prompt
    - Assemble workspace files in order: IDENTITY → SOUL → AGENTS → USER → MEMORY → TOOLS
    - Match OpenClaw core's ordering exactly
    - _Requirements: 17.1_

  - [ ]* 18.2 Write property test for workspace assembly order (`tests/property/workspace-assembly.prop.ts`)
    - **Property 21: Workspace Assembly Order** — files concatenated in IDENTITY → SOUL → AGENTS → USER → MEMORY → TOOLS order
    - **Validates: Requirements 16.1, 17.1**

  - [~] 18.3 Implement structured JSON logging for Gateway container
    - Emit JSON logs to CloudWatch including `sessionId`, `agentId`, `channel`, `responseLatency`
    - _Requirements: 15.6_

  - [ ]* 18.4 Write property test for structured log format (`tests/property/logging.prop.ts`)
    - **Property 25: Structured Log Format** — every log entry contains `sessionId`, `agentId`, `channel`, `responseLatency`
    - **Validates: Requirements 15.6**

- [ ] 19. Checkpoint — Verify Phase 4 API and messaging
  - Ensure all tests pass for webhook handler, API stack, messaging handlers, workspace assembly, and logging. Commit: `feat: phase 4 API gateway, webhooks, messaging handlers, structured logging`. Ask the user if questions arise.

- [ ] 20. Phase 5 — EventBridge Scheduling
  - [~] 20.1 Implement Heartbeat Lambda (`lambdas/heartbeat_handler.py`)
    - Load HEARTBEAT.md from S3 workspace
    - Invoke Supervisor Agent with the checklist
    - If response is `HEARTBEAT_OK`: silently drop
    - If response contains action/alert: forward to operator via configured messaging channel
    - On HEARTBEAT.md not found: log error, send SNS alert, skip cycle
    - On Supervisor timeout (>60s): log timeout, send SNS alert
    - _Requirements: 7.2, 7.3, 7.4_

  - [ ]* 20.2 Write property test for heartbeat response routing (`tests/property/heartbeat.prop.ts`)
    - **Property 10: Heartbeat Response Routing** — `HEARTBEAT_OK` → no notification; any other response → forwarded to operator
    - **Validates: Requirements 7.3, 7.4**

  - [ ]* 20.3 Write unit tests for Heartbeat Lambda (`tests/unit/lambdas/heartbeat_handler_test.py`)
    - Test HEARTBEAT_OK silent drop
    - Test action/alert forwarding
    - Test missing HEARTBEAT.md error handling
    - Test Supervisor timeout handling
    - _Requirements: 7.2, 7.3, 7.4_

  - [~] 20.4 Implement EventBridge schedule creation for agent-created schedules
    - Create EventBridge Scheduler rules in `openclaw-agent-schedules` group
    - One-time schedules: set `ActionAfterCompletion: DELETE`
    - _Requirements: 7.6, 7.7_

  - [ ]* 20.5 Write property test for one-time schedule auto-deletion (`tests/property/scheduling.prop.ts`)
    - **Property 11: One-Time Schedule Auto-Deletion** — one-time schedules have `ActionAfterCompletion` set to `DELETE`
    - **Validates: Requirements 7.7**

  - [~] 20.6 Implement CDK Scheduler Stack (`infra/stacks/scheduler_stack.py`)
    - Create EventBridge Scheduler rule: every 30 minutes → heartbeat Lambda
    - Create nightly schedule (cron: `0 2 * * ? *` UTC) → Memory Consolidation Lambda
    - _Requirements: 7.1, 7.5_

  - [ ]* 20.7 Write CDK assertion tests for Scheduler Stack (`tests/unit/infra/scheduler_stack_test.py`)
    - Assert 30-minute heartbeat rule exists
    - Assert nightly consolidation rule at 0 2 * * ? * UTC
    - _Requirements: 7.1, 7.5_

- [ ] 21. Checkpoint — Verify Phase 5 scheduling
  - Ensure all tests pass for heartbeat handler, scheduling, and scheduler stack. Commit: `feat: phase 5 EventBridge scheduling, heartbeat, and nightly consolidation`. Ask the user if questions arise.

- [ ] 22. Phase 6 — Supervisor Agent
  - [~] 22.1 Implement Supervisor Agent (`agents/supervisor.ts`)
    - Implement `SupervisorAgent.process` method
    - Receive all incoming messages, decide whether to handle directly or delegate
    - Delegation depth capped at 2 (supervisor → specialist, no further)
    - Session ID format for sub-agents: `{rootSessionId}-{agentType}-{step}`
    - On specialist failure after 2 retries: handle task directly
    - Specialist types: `infrastructure`, `code`, `communications`, `research`
    - Proactive notifications via operator's Telegram chat ID from SSM Parameter Store
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 12.6_

  - [ ]* 22.2 Write property test for delegation depth limit (`tests/property/orchestration.prop.ts`)
    - **Property 13: Delegation Depth Limit** — delegation depth never exceeds 2
    - **Validates: Requirements 9.4**

  - [ ]* 22.3 Write property test for sub-agent session ID format (`tests/property/orchestration.prop.ts`)
    - **Property 14: Sub-Agent Session ID Format** — session ID matches `{rootSessionId}-{agentType}-{step}`
    - **Validates: Requirements 9.6**

  - [ ]* 22.4 Write unit tests for Supervisor Agent (`tests/unit/agents/supervisor.test.ts`)
    - Test direct handling of simple messages
    - Test delegation to specialist sub-agents
    - Test fallback on specialist failure after 2 retries
    - Test delegation depth enforcement
    - Test session ID generation
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [ ] 23. Phase 6 — Builder Sub-Agent
  - [~] 23.1 Implement Builder Sub-Agent (`agents/builder.py`)
    - Implement `BuilderAgent` class: `generate_cdk`, `test_and_deploy`, `deploy_lambda`
    - Generate CDK Python code from natural language instruction
    - Test via Code Interpreter, run `cdk synth`, then `cdk deploy`
    - Apply Bedrock Guardrails to every model call that could trigger infrastructure changes
    - All agent-deployed resources prefixed with `agent-`
    - On CDK deployment failure: report CloudFormation failure events to Supervisor, notify operator
    - Deploy Lambda: package handler, store artifact in S3, create/update function, wait for Active state
    - Store previous Lambda version before deploying (enable rollback)
    - Code generation pipeline: write code + tests → execute in Code Interpreter → deploy only if tests pass → retry up to 3 iterations on failure
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

  - [ ]* 23.2 Write property test for builder resource prefix (`tests/property/builder.prop.py`)
    - **Property 15: Builder Resource Prefix** — every resource name starts with `agent-`
    - **Validates: Requirements 10.5**

  - [ ]* 23.3 Write property test for code deployment gate (`tests/property/builder.prop.py`)
    - **Property 16: Code Deployment Gate** — deployment proceeds only if unit tests pass; if tests fail, deployment does not occur
    - **Validates: Requirements 11.1**

  - [ ]* 23.4 Write property test for Lambda version tracking (`tests/property/builder.prop.py`)
    - **Property 17: Lambda Version Tracking** — every deployment records previous function version before deploying new version
    - **Validates: Requirements 11.6**

  - [ ]* 23.5 Write unit tests for Builder Sub-Agent (`tests/unit/agents/builder_test.py`)
    - Test CDK code generation from instruction
    - Test deployment pipeline: synth → deploy → record outputs
    - Test Lambda deployment: package → S3 → create/update → wait Active
    - Test failure handling: CDK synth failure, deploy failure, Code Interpreter test failure
    - Test retry logic (up to 3 iterations on test failure)
    - _Requirements: 10.1, 10.2, 10.4, 10.7, 11.1, 11.2, 11.3, 11.4_

  - [~] 23.6 Implement CDK Builder Stack (`infra/stacks/builder_stack.py`)
    - Create Builder agent IAM role with Permission Boundary denying IAM, Organizations, Account, Billing actions
    - _Requirements: 10.6_

  - [ ]* 23.7 Write CDK assertion tests for Builder Stack (`tests/unit/infra/builder_stack_test.py`)
    - Assert Permission Boundary denies `iam:*`, `organizations:*`, `account:*`, `aws-portal:*`, `budgets:*`, `ce:*`, `cur:*`
    - _Requirements: 10.6_

- [ ] 24. Checkpoint — Verify Phase 6 agents
  - Ensure all tests pass for Supervisor Agent, Builder Sub-Agent, and Builder Stack. Commit: `feat: phase 6 supervisor agent, builder sub-agent, multi-agent orchestration`. Ask the user if questions arise.

- [ ] 25. Phase 7 — Observability & Production Hardening
  - [~] 25.1 Implement CloudWatch dashboard and alarms
    - Create dashboard: messages/hour, Bedrock token usage, memory ops count, active sessions, error rate, builder deployment count
    - Alarm: agent unresponsive >5 minutes → SNS notification
    - Alarm: Bedrock error rate >5% over 5-minute window → SNS notification
    - Alarm: builder deployment failure → SNS with stack name and failure reason
    - Alarm: heartbeat not fired within 35 minutes → missed-heartbeat alarm
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

  - [~] 25.2 Implement circuit breaker for Bedrock API calls
    - Closed (normal): all calls go through
    - Open (after 5 consecutive failures in 1 minute): reject calls for 30 seconds
    - Half-open (after cooldown): allow one test call; if success, close circuit
    - _Requirements: 2.4 (error handling enhancement)_

  - [~] 25.3 Implement CDK Memory Stack (`infra/stacks/memory_stack.py`)
    - AgentCore Memory store configuration
    - Memory Consolidation Lambda with nightly trigger
    - _Requirements: 3.4, 4.1_

  - [ ]* 25.4 Write CDK assertion tests for Memory Stack (`tests/unit/infra/memory_stack_test.py`)
    - Assert AgentCore Memory store resource
    - Assert consolidation Lambda with EventBridge trigger
    - _Requirements: 3.4, 4.1_

  - [ ]* 25.5 Write CDK assertion tests for observability resources (`tests/unit/infra/observability_test.py`)
    - Assert CloudWatch dashboard exists with expected widgets
    - Assert all 4 alarms configured with correct thresholds
    - Assert SNS topic for alarm notifications
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

- [ ] 26. Phase 7 — Integration Wiring & Final Assembly
  - [~] 26.1 Wire all adapters into OpenClaw Gateway startup
    - Provider-based adapter selection: `bedrock` → S3 Workspace + Bedrock Model + AgentCore Memory; `anthropic` → native local paths
    - Inject Model Router into Bedrock Model Adapter
    - Connect Supervisor Agent to messaging handlers and memory
    - Connect structured logging to all components
    - _Requirements: 2.7, 3.5, 17.1, 19.1_

  - [~] 26.2 Wire CDK app to deploy all stacks
    - Ensure `infra/app.py` instantiates all 7 stacks: Gateway, API, Storage, Memory, Identity, Scheduler, Builder
    - Verify `cdk deploy --all` works from `infra/` directory
    - _Requirements: 16.1, 16.4, 16.5_

  - [ ]* 26.3 Write integration test for CDK synth (`tests/integration/cdk-synth.test.py`)
    - Verify `cdk synth` produces valid CloudFormation templates for all stacks
    - _Requirements: 16.1, 16.4_

- [ ] 27. Final Checkpoint — Full test suite and final commit
  - Ensure all unit tests, property tests, and CDK assertion tests pass. Run `cdk synth` to validate all templates. Commit: `feat: phase 7 observability, production hardening, full integration`. Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use `fast-check` (TypeScript) and `hypothesis` (Python) with minimum 100 iterations
- Checkpoints at phase boundaries ensure incremental validation and clean git history
- CDK stacks are tested via `aws_cdk.assertions` template matching
- The design specifies 27 correctness properties; all are covered by property test tasks above
