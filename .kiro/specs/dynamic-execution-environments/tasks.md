# Implementation Plan: Dynamic Execution Environments

## Overview

This plan implements the hybrid execution architecture for OpenClaw in 5 phases, following the design document. Phase 1 establishes the two execution paths (host exec + ACPX/MCP bridge) with SKILL.md files and the Role Manager. Phases 2–3 add remote compute backends (CodeBuild, AgentCore Runtime, EC2, ECS Fargate). Phase 4 adds self-extending tool capabilities. Phase 5 adds observability and cost control.

TypeScript adapters use `vitest` + `fast-check`; the cleanup Lambda uses `pytest` + `hypothesis`. CDK stacks use `aws_cdk.assertions`.

## Tasks

- [x] 1. Phase 1 — ACPX Enablement + MCP Gateway Bridge + configure-gateway.mjs
  - [x] 1.1 Update Dockerfile to include ACPX binary and AWS CLI
    - Add `acpx` binary installation to the Docker image
    - Ensure AWS CLI v2 is installed and available on PATH
    - Verify both `acpx` and `aws` commands are available at runtime
    - _Requirements: 1.6, 1.2_

  - [x] 1.2 Update `configure-gateway.mjs` to write ACPX + MCP bridge config
    - Write MCP bridge config to `plugins.acpx.mcpServers` (not `mcp.servers`)
    - Configure `aws-tools` MCP server pointing to `mcp-gateway-bridge.mjs` with OAuth2 env vars (`GATEWAY_MCP_URL`, `GATEWAY_CLIENT_ID`, `GATEWAY_CLIENT_SECRET`, `GATEWAY_TOKEN_URL`, `GATEWAY_SCOPE`)
    - Set `meta.lastTouchedVersion` to prevent OpenClaw from overwriting the config on startup
    - Read OAuth2 credentials from environment variables or Secrets Manager
    - _Requirements: 1.3, 1.7_

  - [ ]\* 1.3 Write unit tests for configure-gateway.mjs ACPX config generation
    - Test that `plugins.acpx.mcpServers.aws-tools` is written correctly
    - Test that `meta.lastTouchedVersion` is set
    - Test that `mcp.servers` is NOT used for Gateway tool access
    - Test graceful handling when OAuth2 credentials are missing
    - _Requirements: 1.3, 1.7_

- [x] 2. Phase 1 — SKILL.md Files + SOUL.md Updates
  - [x] 2.1 Create `skills/aws-infrastructure/SKILL.md`
    - Teach the agent to use `exec` → `aws` CLI for S3, CloudFormation, EC2 queries, and general AWS operations
    - Include examples of common AWS CLI patterns (list buckets, describe stacks, query instances)
    - _Requirements: 1.1, 1.2_

  - [x] 2.2 Create `skills/acpx-mcp-tools/SKILL.md`
    - Teach the agent when to enter ACPX for MCP Gateway tools (deploy_static_site, manage_s3, etc.)
    - Explain that MCP tools only work inside ACPX sessions
    - Include when NOT to use ACPX (simple AWS CLI operations should use `exec` directly)
    - _Requirements: 1.1, 1.3_

  - [x] 2.3 Create `skills/role-factory/SKILL.md`
    - Teach the agent how to create `agent-task-` prefixed IAM roles with permission boundary
    - Explain when elevated permissions are needed and how to assume roles via STS
    - Include role lifecycle and cleanup expectations
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

  - [x] 2.4 Update SOUL.md with execution path instructions
    - Instruct the agent on when to use `exec` + AWS CLI (direct operations) vs ACPX (MCP Gateway tools) vs delegation (heavy execution)
    - Reference SKILL.md files for specific patterns
    - _Requirements: 1.4_

  - [x] 2.5 Create S3 upload script for skills
    - Upload SKILL.md files to `s3://{bucket}/workspaces/{agent-id}/skills/`
    - Ensure skills are loaded by the Gateway at startup
    - _Requirements: 1.5_

- [x] 3. Phase 1 — Role Manager + Permission Boundary
  - [x] 3.1 Implement Role Manager (`adapters/role-manager.ts`)
    - Implement `createRole()` — create IAM roles with `agent-task-` prefix and `agent-permission-boundary` attached
    - Implement `assumeRole()` — STS AssumeRole with configurable session duration (default: 1hr, max: 4hr)
    - Implement `listActiveRoles()` — query `openclaw-agent-roles` DynamoDB table for active roles
    - Implement `deleteRole()` — delete IAM role and update DynamoDB record
    - Implement `canCreateRole()` — enforce max 5 concurrent active roles
    - Track all roles in `openclaw-agent-roles` DynamoDB table with creation time, purpose, and expiry
    - _Requirements: 7.1, 7.2, 7.3, 7.5, 7.6, 7.7, 7.9_

  - [ ]\* 3.2 Write property test for Role Manager permission boundary enforcement
    - **Property 1: Permission Boundary Enforcement** — every role created by `createRole()` has `agent-permission-boundary` attached as a permission boundary
    - **Validates: Requirements 7.3, 7.6**

  - [ ]\* 3.3 Write property test for Role Manager concurrent role limit
    - **Property 2: Concurrent Role Limit** — `canCreateRole()` returns `allowed: false` when 5 or more active roles exist
    - **Validates: Requirements 7.9**

  - [ ]\* 3.4 Write unit tests for Role Manager
    - Test role creation with correct naming prefix (`agent-task-{timestamp}-{random}`)
    - Test STS AssumeRole session duration clamping (max 4 hours)
    - Test DynamoDB tracking on create/delete
    - Test `canCreateRole()` enforcement at limit
    - _Requirements: 7.1, 7.2, 7.3, 7.5, 7.9_

- [x] 4. Phase 1 — CDK Compute Environments Stack
  - [x] 4.1 Implement CDK Compute Environments Stack (`infra/stacks/compute_environments_stack.py`)
    - Create `openclaw-environments` DynamoDB table (partition key: `environmentId`)
    - Create `openclaw-agent-roles` DynamoDB table (partition key: `roleName`)
    - Create `openclaw-tool-registry` DynamoDB table (partition key: `toolName`)
    - Create `openclaw-cost-ledger` DynamoDB table (partition key: `date`, sort key: `environmentId`)
    - Create `agent-permission-boundary` IAM managed policy that denies IAM (except scoped role creation), Organizations, Account, Billing, Budgets, CE, CUR actions
    - Update ECS task role with base read-only permissions + scoped `agent-task-` role creation with `iam:PermissionsBoundary` condition key
    - Create SNS topic for environment lifecycle notifications
    - _Requirements: 7.3, 7.4, 7.7, 7.10, 8.1, 8.3, 9.3, 10.2_

  - [ ]\* 4.2 Write CDK assertion tests for Compute Environments Stack
    - Assert DynamoDB tables are created with correct key schemas
    - Assert permission boundary policy denies the correct actions
    - Assert ECS task role has `iam:PermissionsBoundary` condition on `iam:CreateRole`
    - Assert SNS topic is created
    - _Requirements: 7.3, 7.4, 8.1_

- [x] 5. Checkpoint — Phase 1 complete
  - Ensure ACPX is enabled, MCP bridge is configured, SKILL.md files are created, Role Manager works, and CDK stack is deployed. Ask the user if questions arise.

- [ ] 6. Phase 2 — Compute Router
  - [ ] 6.1 Implement Compute Router (`adapters/compute-router.ts`)
    - Implement `route()` — analyze task text and metadata to select optimal backend
    - Apply routing rules: quick/interactive → AgentCore Runtime, CDK/Docker/CI → CodeBuild, GPU/long-running → EC2, persistent service → ECS Fargate
    - Implement `canProvision()` — check budget (monthly limit, default $100) and concurrency limits
    - Include cost estimation per backend in routing decisions
    - Allow explicit backend override from the agent
    - Log every routing decision with task description, selected backend, and reasoning
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 7.10_

  - [ ]\* 6.2 Write property test for Compute Router routing rules
    - **Property 3: Routing Rule Determinism** — for any task with `requiresGpu: true`, the router always selects EC2; for `requiresDocker: true`, always CodeBuild; for `requiresPersistence: true`, always ECS Fargate
    - **Validates: Requirements 6.2**

  - [ ]\* 6.3 Write property test for Compute Router cost estimation
    - **Property 4: Cost Estimation Non-Negative** — every routing decision has `estimatedCostUsd >= 0`
    - **Validates: Requirements 6.5**

  - [ ]\* 6.4 Write unit tests for Compute Router
    - Test each routing rule from the requirements table
    - Test explicit backend override
    - Test budget enforcement (reject when monthly spend exceeds limit)
    - Test concurrency limit enforcement
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 7.10_

- [ ] 7. Phase 2 — CodeBuild Backend
  - [ ] 7.1 Implement CodeBuild Backend (`adapters/codebuild-backend.ts`)
    - Implement `startBuild()` — create on-demand build projects with `agent-` prefix and custom buildspec
    - Implement `streamLogs()` — stream build logs via CloudWatch Logs in real time
    - Implement `getBuildResult()` — return build status, artifacts location, and outputs
    - Support custom Docker images as build environment
    - Support environment variables from Secrets Manager
    - Store artifacts in S3 with `agent-` prefix
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [ ]\* 7.2 Write property test for CodeBuild Backend naming
    - **Property 5: CodeBuild Project Naming** — every build project name starts with `agent-` prefix
    - **Validates: Requirements 3.4**

  - [ ]\* 7.3 Write unit tests for CodeBuild Backend
    - Test build project creation with custom buildspec
    - Test log streaming via CloudWatch Logs
    - Test build result retrieval (SUCCEEDED, FAILED, STOPPED)
    - Test Secrets Manager environment variable injection
    - _Requirements: 3.1, 3.2, 3.3, 3.6, 3.7_

- [ ] 8. Phase 2 — AgentCore Runtime Backend
  - [ ] 8.1 Implement AgentCore Runtime Backend (`adapters/agentcore-runtime-backend.ts`)
    - Implement `createSession()` — create runtime sessions with pre-configured templates
    - Implement `executeCommand()` — run commands via `InvokeAgentRuntimeCommand`, stream output
    - Implement `destroySession()` — stop and clean up sessions
    - Support persistent filesystem across session stop/resume
    - Auto-cleanup idle sessions after configurable timeout (default: 15 minutes)
    - Notify agent when approaching 8-hour session limit
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [ ]\* 8.2 Write property test for AgentCore Runtime session lifecycle
    - **Property 6: Session Lifecycle Consistency** — after `createSession()`, the session status is `active`; after `destroySession()`, the session status is `terminated`
    - **Validates: Requirements 2.1, 2.4**

  - [ ]\* 8.3 Write unit tests for AgentCore Runtime Backend
    - Test session creation with different templates (infra-tools, data-science, general)
    - Test command execution and output streaming
    - Test idle timeout cleanup
    - Test 8-hour limit notification
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [ ] 9. Phase 2 — Delegation SKILL.md Files
  - [ ] 9.1 Create `skills/codebuild-delegation/SKILL.md`
    - Teach the agent when to use CodeBuild (CDK deploys, Docker builds, CI/CD)
    - Include buildspec.yml writing patterns
    - Show how to start builds via `exec` → `aws codebuild start-build`
    - Show how to stream logs via `exec` → `aws logs tail`
    - _Requirements: 1.1, 3.1_

  - [ ] 9.2 Create `skills/agentcore-runtime/SKILL.md`
    - Teach the agent when to use AgentCore Runtime (quick interactive tasks, code testing)
    - Show how to invoke via `exec` → `aws bedrock-agentcore invoke-agent-runtime`
    - Include session management patterns (create, resume, stop)
    - _Requirements: 1.1, 2.1_

- [ ] 10. Checkpoint — Phase 2 complete
  - Ensure Compute Router, CodeBuild Backend, AgentCore Runtime Backend, and delegation skills are working. Ask the user if questions arise.

- [ ] 11. Phase 3 — EC2 Backend
  - [ ] 11.1 Implement EC2 Backend (`adapters/ec2-backend.ts`)
    - Implement `launchInstance()` — launch from pre-approved AMIs with `agent-` name prefix
    - Implement `executeCommand()` — run commands via SSM Session Manager, stream output
    - Implement `terminateInstance()` — terminate and clean up
    - Support instance type selection based on task requirements (CPU, memory, GPU)
    - Auto-terminate after task completion or max lifetime (default: 4 hours)
    - Support persistent EBS volumes across launches
    - Apply permission boundary to instance role
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [ ]\* 11.2 Write property test for EC2 Backend naming
    - **Property 7: EC2 Instance Naming** — every launched instance has a Name tag starting with `agent-`
    - **Validates: Requirements 4.1**

  - [ ]\* 11.3 Write unit tests for EC2 Backend
    - Test instance launch from pre-approved AMI
    - Test SSM command execution and output streaming
    - Test auto-termination after max lifetime
    - Test EBS volume reattachment
    - Test permission boundary on instance role
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

- [ ] 12. Phase 3 — ECS Fargate Backend
  - [ ] 12.1 Implement ECS Fargate Backend (`adapters/ecs-fargate-backend.ts`)
    - Implement `deployService()` — create task definitions and services with `agent-` prefix
    - Implement `updateService()` — update container image or env vars without full redeployment
    - Implement `destroyService()` — stop service and clean up resources
    - Support health checks and auto-restart
    - Return service endpoint (ALB URL or task IP) after deployment
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [ ]\* 12.2 Write property test for ECS Fargate Backend naming
    - **Property 8: Fargate Service Naming** — every deployed service name starts with `agent-`
    - **Validates: Requirements 5.2**

  - [ ]\* 12.3 Write unit tests for ECS Fargate Backend
    - Test service deployment with health check configuration
    - Test service update (new image, new env vars)
    - Test service teardown and resource cleanup
    - Test endpoint return after deployment
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

- [ ] 13. Phase 3 — Specialized Compute SKILL.md Files
  - [ ] 13.1 Create `skills/ec2-delegation/SKILL.md`
    - Teach the agent when to use EC2 (GPU, long-running, custom OS)
    - Show how to launch instances from pre-approved AMIs
    - Include SSM Session Manager command execution patterns
    - _Requirements: 1.1, 4.1_

  - [ ] 13.2 Create `skills/ecs-fargate-delegation/SKILL.md`
    - Teach the agent when to use Fargate (persistent APIs, workers, scheduled jobs)
    - Show how to create task definitions and services
    - Include health check and service update patterns
    - _Requirements: 1.1, 5.1_

- [ ] 14. Checkpoint — Phase 3 complete
  - Ensure EC2 Backend, ECS Fargate Backend, and specialized compute skills are working. Ask the user if questions arise.

- [ ] 15. Phase 3 — Environment Lifecycle Manager + Cleanup Lambda
  - [ ] 15.1 Implement Lifecycle Manager (`adapters/lifecycle-manager.ts`)
    - Implement `register()` — register new environments in `openclaw-environments` DynamoDB table
    - Implement `heartbeat()` — update last activity timestamp
    - Implement `markTerminated()` — mark environment as terminated
    - Implement `listActive()` — get all active environments
    - Implement `reconcile()` — check for orphaned resources not in the table and clean them up
    - _Requirements: 8.1, 8.5_

  - [ ] 15.2 Implement Cleanup Lambda (`lambdas/environment_cleanup.py`)
    - Every 30 minutes: scan `openclaw-environments` for expired environments, terminate via appropriate backend, send SNS notification
    - Every 6 hours: scan `openclaw-agent-roles` for roles older than 24 hours, delete via IAM API, update DynamoDB status
    - Log all actions to CloudWatch
    - _Requirements: 7.8, 8.2, 8.3_

  - [ ] 15.3 Add EventBridge schedules to CDK Compute Environments Stack
    - Add 30-minute schedule for environment cleanup
    - Add 6-hour schedule for role cleanup
    - Grant Lambda permissions to terminate environments and delete IAM roles
    - _Requirements: 7.8, 8.2_

  - [ ]\* 15.4 Write property test for Lifecycle Manager reconciliation
    - **Property 9: Reconciliation Completeness** — after `reconcile()`, every resource in the DynamoDB table that no longer exists in AWS is marked as `orphaned` or `terminated`
    - **Validates: Requirements 8.5**

  - [ ]\* 15.5 Write unit tests for Cleanup Lambda
    - Test environment expiry detection and termination
    - Test role expiry detection and deletion (24-hour threshold)
    - Test SNS notification on auto-termination
    - Test graceful handling of already-terminated resources
    - _Requirements: 7.8, 8.2, 8.3_

- [ ] 16. Checkpoint — Lifecycle management complete
  - Ensure Lifecycle Manager tracks environments, Cleanup Lambda runs on schedule, and orphan reconciliation works. Ask the user if questions arise.

- [ ] 17. Phase 4 — Self-Extending Tool Registry
  - [ ] 17.1 Implement Tool Registry (`adapters/tool-registry.ts`)
    - Implement `register()` — register agent-created tools in `openclaw-tool-registry` DynamoDB table
    - Implement `list()` — list all registered tools with metadata
    - Implement `remove()` — remove a tool from the registry
    - Implement `syncToGateway()` — update Gateway target configuration without CDK redeploy
    - Support both Lambda and API tool types
    - Track usage count per tool
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [ ]\* 17.2 Write property test for Tool Registry round-trip
    - **Property 10: Tool Registry Round-Trip** — registering a tool and then listing tools always includes the registered tool with matching metadata
    - **Validates: Requirements 9.3, 9.4**

  - [ ]\* 17.3 Write unit tests for Tool Registry
    - Test Lambda tool registration and listing
    - Test API tool registration and listing
    - Test tool removal
    - Test Gateway sync without CDK redeploy
    - Test usage count tracking
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [ ] 17.4 Create `skills/self-extending/SKILL.md`
    - Teach the agent how to create Lambda functions and register them on the Gateway
    - Teach how to create APIs and add them as OpenAPI targets
    - Include tool registry management patterns
    - _Requirements: 1.1, 9.1, 9.2_

- [ ] 18. Checkpoint — Phase 4 complete
  - Ensure Tool Registry works, Gateway sync updates targets without redeploy, and self-extending skill is created. Ask the user if questions arise.

- [ ] 19. Phase 5 — Observability + Cost Control
  - [ ] 19.1 Implement cost ledger tracking in Compute Router
    - Record per-environment cost estimates in `openclaw-cost-ledger` DynamoDB table
    - Track per-day aggregate costs
    - Enforce monthly budget limit (configurable, default: $100) — reject provisioning when exceeded
    - _Requirements: 7.10, 10.2, 6.5_

  - [ ] 19.2 Implement CloudWatch metrics emission
    - Emit metrics for: active environments count, environment provisioning latency, task execution duration, estimated cost per environment
    - Add metrics emission to Compute Router, Lifecycle Manager, and each backend
    - _Requirements: 10.1_

  - [ ] 19.3 Add CloudWatch dashboard widgets for execution environments
    - Add widgets for active environment count, provisioning latency, cost per day, and backend distribution
    - Integrate with existing agent metrics dashboard
    - _Requirements: 10.4_

  - [ ] 19.4 Implement daily cost summary via Telegram
    - Create a mechanism (heartbeat or scheduled Lambda) that sends daily cost summary to the operator via Telegram
    - Support on-demand cost query ("how much have my agents spent today?")
    - _Requirements: 10.3_

  - [ ]\* 19.5 Write unit tests for cost ledger and budget enforcement
    - Test cost recording per environment
    - Test daily aggregate calculation
    - Test monthly budget enforcement (reject when exceeded)
    - Test daily summary generation
    - _Requirements: 7.10, 10.2, 10.3_

- [ ] 20. Phase 5 — Manual Environment Termination + Reconciliation on Restart
  - [ ] 20.1 Implement manual environment termination via Telegram
    - Support "shut down all running environments" command
    - Terminate all active environments and update DynamoDB records
    - _Requirements: 8.4_

  - [ ] 20.2 Implement reconciliation on Gateway restart
    - On container startup, reconcile DynamoDB state with actual running AWS resources
    - Clean up orphaned environments that are running but not tracked (or tracked but not running)
    - _Requirements: 8.5_

- [ ] 21. Final Checkpoint — All phases complete
  - Ensure all 5 phases are implemented: ACPX + MCP bridge, compute backends, lifecycle management, self-extending tools, and observability. All tests pass. Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use `fast-check` (TypeScript) and `hypothesis` (Python)
- Checkpoints ensure incremental validation between phases
- The design follows 5 phases: Phase 1 (Skills + ACPX + MCP Bridge + Role Factory), Phase 2 (CodeBuild + AgentCore Runtime), Phase 3 (EC2 + ECS Fargate + Lifecycle), Phase 4 (Self-Extending), Phase 5 (Observability + Cost)
- SKILL.md files are the primary mechanism for teaching the agent execution patterns — they are natural language markdown, not code
- The `configure-gateway.mjs` script must handle OpenClaw's config overwrite behavior by setting `meta.lastTouchedVersion`
- AWS CLI profile: `openclaw-dev`, region: `us-east-1`
