# Implementation Plan: OpenClaw AWS Extension

## Overview

This plan follows the build phases from the design document. All CDK stacks, adapters, agents, lambdas, scripts, and workspace seeds have been implemented and deployed. The remaining work focuses on wiring the Gateway to use OpenClaw's native channel system: reading secrets from Secrets Manager into `openclaw.json`, registering the Bedrock adapter as an OpenClaw provider plugin, configuring the native Telegram channel, end-to-end verification, and updating DEPLOYMENT.md.

TypeScript adapters use `vitest` + `fast-check`; Python components use `pytest` + `hypothesis`. CDK stacks use `aws_cdk.assertions`.

## Tasks

- [x] 1. Phase 0 — Local OpenClaw Verification & Project Scaffolding
  - [x] 1.1 Create directory structure for AWS extension code
    - Created `adapters/`, `agents/`, `lambdas/`, `infra/stacks/`, `infra/constructs/`, `tests/unit/adapters/`, `tests/unit/lambdas/`, `tests/unit/agents/`, `tests/unit/messaging/`, `tests/property/`, `workspace-seeds/`, `scripts/`
    - Created placeholder workspace seed files (SOUL.md, MEMORY.md, HEARTBEAT.md, AGENTS.md, TOOLS.md, USER.md, IDENTITY.md)
    - _Requirements: 17.1, 17.2, 17.3, 15.1_

  - [x] 1.2 Create `scripts/test-local.sh` for Phase 0 local verification
    - _Requirements: 18.1, 18.2_

  - [x] 1.3 Create `scripts/seed-workspace.py` for S3 workspace seeding
    - _Requirements: 17.4, 18.3_

  - [x] 1.4 Create `scripts/migrate-memory.py` for local-to-cloud memory migration
    - _Requirements: 18.4_

- [x] 2. Checkpoint — Phase 0 scaffolding verified

- [x] 3. Phase 1 — S3 Workspace Adapter
  - [x] 3.1 Implement S3 Workspace Adapter (`adapters/s3-workspace.ts`)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 3.2 Write property tests for S3 Workspace Adapter (`tests/property/s3-workspace.prop.ts`)
    - **Property 1: S3 Workspace File Round-Trip** — writing files and reading them back produces byte-identical content
    - **Validates: Requirements 1.1, 1.3, 16.2, 16.3**

  - [ ]* 3.3 Write property test for dual-write consistency (`tests/property/s3-workspace.prop.ts`)
    - **Property 2: Dual-Write Consistency** — after write completes, local FS and S3 content are identical
    - **Validates: Requirements 1.2, 4.3**

  - [ ]* 3.4 Write property test for S3 key structure (`tests/property/s3-workspace.prop.ts`)
    - **Property 3: S3 Key Structure** — key equals `{tenantId}/{agentId}/{filename}`; daily logs match `{tenantId}/{agentId}/memory/YYYY-MM-DD.md`
    - **Validates: Requirements 1.5, 16.3, 16.4**

  - [ ]* 3.5 Write unit tests for S3 Workspace Adapter (`tests/unit/adapters/s3-workspace.test.ts`)
    - Test startup sync, exponential backoff retry, write failure logging, empty workspace
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 4. Phase 1 — Bedrock Model Adapter
  - [x] 4.1 Implement Bedrock Model Adapter (`adapters/bedrock-model.ts`)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

  - [ ]* 4.2 Write property tests for Bedrock Model Adapter (`tests/property/bedrock-model.prop.ts`)
    - **Property 4: Converse API Request Formation** — every invocation produces valid Converse API request with model ID and guardrailConfig
    - **Validates: Requirements 2.1, 2.3, 2.5, 10.3**

  - [ ]* 4.3 Write property test for token metrics emission (`tests/property/bedrock-model.prop.ts`)
    - **Property 5: Token Metrics Emission** — every response emits metrics with non-negative `inputTokens`, non-negative `outputTokens`, non-empty `modelId`
    - **Validates: Requirements 2.6**

  - [ ]* 4.4 Write unit tests for Bedrock Model Adapter (`tests/unit/adapters/bedrock-model.test.ts`)
    - Test ThrottlingException retry, streaming response assembly, provider=anthropic bypass
    - _Requirements: 2.1, 2.2, 2.4, 2.7_

- [x] 5. Phase 1 — Model Router
  - [x] 5.1 Implement Model Router (`adapters/model-router.ts`)
    - _Requirements: 13.1, 13.2, 13.3, 13.4_

  - [ ]* 5.2 Write property tests for Model Router (`tests/property/model-router.prop.ts`)
    - **Property 11: Model Router Selection** — deterministic selection: Haiku for simple+short or consolidation; Opus for complex; Sonnet for default
    - **Validates: Requirements 13.1, 13.2, 13.3, 13.4**

  - [ ]* 5.3 Write unit tests for Model Router (`tests/unit/adapters/model-router.test.ts`)
    - Test each routing rule, 500-char boundary, pattern matching edge cases
    - _Requirements: 13.1, 13.2, 13.3, 13.4_

- [x] 6. Checkpoint — Phase 1 adapters verified

- [x] 7. Phase 2 — AgentCore Memory Adapter
  - [x] 7.1 Implement AgentCore Memory Adapter (`adapters/agentcore_memory.py`)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 7.2 Write property tests for AgentCore Memory (`tests/property/memory.prop.py`)
    - **Property 8: Memory Retrieval Bound** — semantic search returns at most 10 records
    - **Validates: Requirements 3.2**

  - [ ]* 7.3 Write property test for memory ingest completeness (`tests/property/memory.prop.py`)
    - **Property 7: Memory Ingest Completeness** — every completed conversation turn ingests both user message and agent response
    - **Validates: Requirements 3.1**

  - [ ]* 7.4 Write unit tests for AgentCore Memory Adapter (`tests/unit/adapters/agentcore_memory_test.py`)
    - Test ingest, retrieve, create_store, graceful degradation, inactive in local mode
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 8. Phase 2 — Memory Consolidation Lambda
  - [x] 8.1 Implement Memory Consolidation Lambda (`lambdas/memory_consolidation.py`)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [ ]* 8.2 Write property test for consolidation line limit (`tests/property/memory.prop.py`)
    - **Property 9: Memory Consolidation Line Limit** — output MEMORY.md contains at most 100 lines
    - **Validates: Requirements 4.2**

  - [ ]* 8.3 Write property test for archive key format (`tests/property/memory.prop.py`)
    - **Property 10: Memory Archive Key Format** — archive key matches `memory-archive/{agentId}/{YYYY-MM-DD}/memories.json`
    - **Validates: Requirements 4.4**

  - [ ]* 8.4 Write unit tests for Memory Consolidation Lambda (`tests/unit/lambdas/consolidation_test.py`)
    - Test happy path, failure mode, truncation, SNS alert
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [x] 9. Checkpoint — Phase 2 memory components verified

- [x] 10. Phase 3 — CDK Stacks & Infrastructure
  - [x] 10.1 Implement CDK Storage Stack (`infra/stacks/storage_stack.py`)
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [x] 10.2 Implement CDK Gateway Stack (`infra/stacks/gateway_stack.py`)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 10.3 Implement CDK Identity Stack (`infra/stacks/identity_stack.py`)
    - _Requirements: 8.1, 8.2, 8.5_

  - [x] 10.4 Implement CDK API Stack (`infra/stacks/api_stack.py`)
    - _Requirements: 6.4 (future use — GitHub webhooks, admin API)_

  - [x] 10.5 Implement CDK Memory Stack (`infra/stacks/memory_stack.py`)
    - _Requirements: 3.4, 4.1_

  - [x] 10.6 Implement CDK Scheduler Stack (`infra/stacks/scheduler_stack.py`)
    - _Requirements: 7.1, 7.4_

  - [x] 10.7 Implement CDK Builder Stack (`infra/stacks/builder_stack.py`)
    - _Requirements: 10.6_

  - [x] 10.8 Implement Tenant Isolation construct (`infra/constructs/tenant_isolation.py`)
    - _Requirements: 17.1, 17.2, 17.3_

  - [x] 10.9 Implement OpenClawAgent reusable construct (`infra/constructs/openclaw_agent.py`)
    - _Requirements: 15.2_

  - [x] 10.10 Create CDK app entry point (`infra/app.py`) and deploy all 7 stacks
    - _Requirements: 15.1, 15.4, 15.5_

- [x] 11. Checkpoint — All 7 CDK stacks deployed and verified

- [x] 12. Phase 4 — Agents, Adapters & Supporting Components
  - [x] 12.1 Implement Supervisor Agent (`agents/supervisor.ts`)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [x] 12.2 Implement Builder Sub-Agent (`agents/builder.py`)
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

  - [x] 12.3 Implement Heartbeat Lambda (`lambdas/heartbeat_handler.py`)
    - _Requirements: 7.1, 7.2, 7.3_

  - [x] 12.4 Implement EventBridge Scheduler adapter (`adapters/eventbridge-scheduler.ts`)
    - _Requirements: 7.2, 7.3_

  - [x] 12.5 Implement Workspace Assembly (`adapters/workspace-assembly.ts`)
    - _Requirements: 16.1_

  - [x] 12.6 Implement Structured Logger (`adapters/structured-logger.ts`)
    - _Requirements: 14.5_

  - [x] 12.7 Implement Circuit Breaker (`adapters/circuit-breaker.ts`)
    - _Requirements: 2.4_

  - [x] 12.8 Implement Gateway Bootstrap (`adapters/gateway-bootstrap.ts`)
    - _Requirements: 5.3, 2.7, 3.5, 18.1_

  - [x] 12.9 Implement Dockerfile.gateway and docker-entrypoint.sh
    - Custom Docker image with S3 sync, AWS CLI, and OpenClaw Gateway startup
    - _Requirements: 5.1, 5.3, 5.6_

  - [x] 12.10 Implement CloudWatch dashboard and alarms
    - _Requirements: 14.1, 14.2, 14.3, 14.4_

- [x] 13. Phase 4 — Unit Tests (completed)
  - [x] 13.1 Write unit tests for workspace-assembly (`tests/unit/adapters/workspace-assembly.test.ts`)
    - _Requirements: 16.1_

  - [x] 13.2 Write unit tests for structured-logger (`tests/unit/adapters/structured-logger.test.ts`)
    - _Requirements: 14.5_

  - [x] 13.3 Write unit tests for eventbridge-scheduler (`tests/unit/adapters/eventbridge-scheduler.test.ts`)
    - _Requirements: 7.2, 7.3_

  - [x] 13.4 Write unit tests for circuit-breaker (`tests/unit/adapters/circuit-breaker.test.ts`)
    - _Requirements: 2.4_

  - [x] 13.5 Write unit tests for supervisor agent (`tests/unit/agents/supervisor.test.ts`)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [x] 14. Checkpoint — All existing adapters, agents, lambdas, and unit tests verified

- [x] 15. Phase 5 — Gateway Configuration Manager & Native Channel Wiring
  - [x] 15.1 Implement Gateway Configuration Manager (`adapters/gateway-config.ts`)
    - Read channel secrets from Secrets Manager at container startup under `openclaw/{tenantId}/` namespace
    - Retrieve `openclaw/{tenantId}/telegram-bot-token` (and optionally `slack-bot-token`, `slack-signing-secret`)
    - Write `~/.openclaw/openclaw.json` with the channel configuration block (Telegram botToken, Slack credentials)
    - If a secret is not found, omit that channel from the config — Gateway starts without it
    - If Secrets Manager is unreachable, write minimal config without channel credentials (WebSocket-only mode)
    - Must run before the Gateway process starts (called from `docker-entrypoint.sh`)
    - _Requirements: 6.1, 6.3, 8.2, 8.3_

  - [x] 15.2 Update `docker-entrypoint.sh` to invoke Gateway Configuration Manager
    - After S3 workspace sync and before starting the Gateway process, call the Gateway Config Manager
    - Use `node` or `npx ts-node` to execute `adapters/gateway-config.ts` (or a compiled JS entrypoint)
    - Pass `TENANT_ID`, `AGENT_ID`, and AWS region as environment variables
    - The script should generate `~/.openclaw/openclaw.json` with Telegram bot token from Secrets Manager
    - Ensure the entrypoint still works if Secrets Manager is unreachable (graceful fallback)
    - _Requirements: 6.1, 8.3_

  - [ ]* 15.3 Write property test for channel configuration generation (`tests/property/gateway-config.prop.ts`)
    - **Property 6: Channel Configuration Generation** — for any set of channel secrets, the generated `openclaw.json` contains corresponding secret values for configured channels and omits channels whose secrets are absent
    - **Validates: Requirements 6.1, 6.3, 8.2, 8.3**

  - [ ]* 15.4 Write unit tests for Gateway Configuration Manager (`tests/unit/adapters/gateway-config.test.ts`)
    - Test Telegram token retrieval and config generation
    - Test missing secret → channel omitted from config
    - Test Secrets Manager unreachable → minimal config written
    - Test Slack credentials included when present
    - _Requirements: 6.1, 6.3, 8.2, 8.3_

- [x] 16. Phase 5 — Register Bedrock as OpenClaw Provider Plugin
  - [x] 16.1 Register Bedrock adapter as an OpenClaw provider plugin
    - Investigate OpenClaw's provider plugin API (how providers are registered, what interface they must implement)
    - Update `adapters/bedrock-model.ts` to conform to OpenClaw's provider plugin registration interface
    - Register the Bedrock provider so the Gateway natively routes model calls through `bedrock-model.ts` when `PROVIDER=bedrock`
    - Ensure the Model Router is injected into the Bedrock provider plugin for cost-based model selection
    - Verify that when `PROVIDER=anthropic`, the Bedrock plugin is not registered and OpenClaw's native Anthropic SDK path is used
    - _Requirements: 2.1, 2.2, 2.7, 13.1, 13.2, 13.3_

- [x] 17. Phase 5 — Configure Native Telegram Channel
  - [x] 17.1 Configure OpenClaw's native Telegram handler via `openclaw.json`
    - Verify that the `openclaw.json` generated by the Gateway Config Manager (task 15.1) is in the correct format for OpenClaw's native Telegram handler
    - Test that the Gateway picks up the Telegram bot token from `openclaw.json` and registers the webhook or starts polling
    - Verify message flow: Telegram → Gateway (native handler) → Supervisor → Bedrock → response → Telegram
    - If OpenClaw requires additional Telegram config fields beyond `botToken`, add them to the Gateway Config Manager
    - _Requirements: 6.1, 6.2, 8.4_

  - [x] 17.2 Update CDK Gateway Stack to grant Secrets Manager read access to the ECS task role
    - Add IAM policy statement allowing `secretsmanager:GetSecretValue` on `openclaw/*` secrets
    - Ensure the task role can read the Telegram bot token secret at container startup
    - _Requirements: 8.2, 8.3, 5.2_

- [x] 18. Checkpoint — Gateway Configuration Manager and native Telegram channel wired
  - Ensure Gateway Config Manager reads secrets and writes `openclaw.json`, Bedrock provider plugin is registered, and Telegram channel is configured. Ask the user if questions arise.

- [ ] 19. Phase 6 — End-to-End Verification
  - [x] 19.1 End-to-end test: Telegram → Gateway → Bedrock → response
    - Redeploy the Gateway ECS task with the updated `docker-entrypoint.sh` and Gateway Config Manager
    - Send a test message to the Telegram bot
    - Verify the full flow: Telegram message → Gateway (native Telegram handler) → Supervisor Agent → Bedrock Converse API → response delivered back to Telegram
    - Check CloudWatch logs for structured JSON log entries with `sessionId`, `agentId`, `channel: telegram`, and `responseLatency`
    - Verify Bedrock token metrics are emitted (InputTokens, OutputTokens, ModelId)
    - _Requirements: 6.1, 6.2, 2.1, 2.2, 2.6, 14.5_

  - [ ] 19.2 Verify workspace file persistence across container restart
    - Update MEMORY.md via the agent (send a message that triggers a memory write)
    - Restart the ECS task (force new deployment)
    - Verify MEMORY.md content persists in S3 and is synced back on startup
    - _Requirements: 1.1, 1.2, 5.3, 5.5_

- [ ] 20. Checkpoint — End-to-end flow verified
  - Ensure Telegram messages flow through the Gateway natively, Bedrock responses are delivered, and workspace files persist. Ask the user if questions arise.

- [ ] 21. Phase 7 — Documentation Update
  - [x] 21.1 Update DEPLOYMENT.md with revised architecture
    - Document the native channel architecture (no webhook Lambda for messaging)
    - Document the Gateway Configuration Manager flow (Secrets Manager → openclaw.json → Gateway)
    - Document how to add/change Telegram bot token (update secret → restart ECS task)
    - Document how to add Slack channel support (add secrets → restart ECS task)
    - Document the Bedrock provider plugin registration
    - Remove or mark as "future use" any references to webhook Lambda → SQS pipeline for messaging
    - Include the `cdk deploy --all` command and AWS CLI profile (`openclaw-dev`, `us-east-1`)
    - _Requirements: 6.5, 8.3_

- [ ] 22. Final Checkpoint — Full system verified and documented
  - Ensure all tests pass, end-to-end flow works, DEPLOYMENT.md is updated. Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use `fast-check` (TypeScript) and `hypothesis` (Python) with minimum 100 iterations
- Checkpoints ensure incremental validation
- Tasks 1–14 are already completed (all infrastructure, adapters, agents, lambdas, CDK stacks deployed)
- Tasks 15–22 are the remaining work: Gateway Config Manager, Bedrock provider plugin registration, native Telegram channel, E2E testing, and documentation
- The Api stack (HTTP API Gateway) is deployed but not in the core messaging path — it's for future GitHub webhooks and admin API
- AWS CLI profile: `openclaw-dev`, region: `us-east-1`
