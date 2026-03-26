---
inclusion: auto
---

# AgentCore Runtime Vision: Dynamic Self-Extending Agent Hands

## The Concept

The OpenClaw agent on Fargate is the "brain" — it reasons, plans, and decides what to do.
AWS compute services provide the "hands" — dynamically provisioned execution environments
tailored to each task. The agent doesn't have a fixed set of tools or a single sandbox.
It intelligently selects and provisions the right compute environment for the job.

The spectrum of execution environments:

- **AgentCore Runtime** (Firecracker microVMs): Quick interactive tasks, code testing,
  shell commands. Starts in seconds, scales to zero, consumption-based pricing.
- **AWS CodeBuild**: CI/CD pipelines, Docker builds, CDK deployments. Managed build
  environments with caching and artifact management.
- **EC2 Instances**: Long-running processes, GPU workloads, custom OS requirements.
  Provisioned on demand via SSM, auto-terminated after use.
- **ECS Fargate**: Persistent containerized services (APIs, workers, scheduled jobs).
  Deployed and managed by the agent.
- **AgentCore Code Interpreter**: Sandboxed code execution for testing before deployment.
- **AgentCore Browser**: Cloud browser for web interaction tasks.

The agent decides which environment to use based on task characteristics: duration,
resource needs, required tools, persistence, and cost sensitivity.

## Architecture

```
User (Telegram) → OpenClaw Gateway (Fargate, brain)
                      ↓
              Claude Sonnet 4.6 reasons about the task
                      ↓
              ACPX (local workspace — draft, test, validate)
                ├── MCP tools via AgentCore Gateway bridge
                │     ├── deploy_static_site (Lambda)
                │     ├── manage_s3 (Lambda)
                │     └── ... self-extending tool registry
                ├── AWS CLI (local, for quick queries and delegation)
                │     ├── aws codebuild start-build (CI/CD tasks)
                │     ├── agentcore invoke (AgentCore Runtime tasks)
                │     ├── aws ec2 run-instances (specialized compute)
                │     └── aws ecs create-service (persistent services)
                ├── Local file operations (draft CDK code, buildspecs)
                └── AgentCore Code Interpreter (sandboxed testing)
```

## Key Architectural Decision: ACPX as Local Workspace + Remote Delegation

We keep OpenClaw's default ACPX runtime as the agent's local workspace. ACPX
provides the coding sandbox (file editing, shell commands, code execution, MCP
tools) that the agent uses to draft, test, and validate work before executing it.

Heavy execution is delegated to remote AWS environments via shell commands and
MCP tools. The agent uses ACPX as a "staging area" — it writes CDK code locally,
validates it, then submits it to CodeBuild. It generates a buildspec, tests it,
then starts the build remotely.

### Why not a custom ACP backend?

We evaluated replacing ACPX with a custom AcpRuntime implementation that routes
directly to AgentCore Runtime / CodeBuild / EC2. The custom approach is cleaner
architecturally but has significant downsides:

- **Massive implementation effort**: The AcpRuntime interface has 8+ methods with
  complex streaming, session management, and error handling. Months of work.
- **Fragile coupling**: OpenClaw's ACP protocol evolves actively. A custom backend
  would break on upgrades.
- **No local reasoning**: The agent can't do quick local operations without spinning
  up a remote environment. Even trivial tasks become remote calls.
- **Lost features**: ACPX provides file editing, code execution, MCP tools, and the
  full Pi coding agent experience for free.

The ACPX + delegation approach gives us:
- Immediate access to all OpenClaw coding features
- Local workspace for drafting and testing before remote execution
- Incremental adoption (add delegation tools one at a time)
- Stays on OpenClaw's upgrade path
- The real isolation happens in the remote environments, not the local sandbox

## Key Design Decisions

1. **Custom ACP Runtime Backend**: Implements OpenClaw's `AcpRuntime` interface but
   delegates to AgentCore Runtime API instead of local acpx process. The Gateway
   thinks it's talking to a local sandbox.

2. **Dynamic Runtime Selection**: The agent decides what kind of Runtime to spin up
   based on the task. "Deploy infrastructure" → infra Runtime. "Analyze data" →
   data science Runtime. Not a fixed mapping.

3. **Session Persistence**: AgentCore Runtime supports persistent filesystems across
   session stop/resume. Multi-step tasks maintain state.

4. **Consumption-Based**: Runtimes scale to zero when idle. Pay only for active
   execution time. No idle costs.

5. **Security Chain**: Telegram user ID → OpenClaw allowlist → AgentCore Gateway
   OAuth → Cedar Policy → Runtime IAM role with permission boundary.

## Implementation Path

### Phase 1: Enable ACPX + MCP Tools
- Install acpx in the Docker image (Dockerfile.gateway)
- Enable the ACPX plugin in openclaw.json config
- Verify MCP tools (AgentCore Gateway bridge) work from within ACPX
- Test: agent can call deploy_static_site via MCP from the coding sandbox
- Install AWS CLI in the ACPX environment for delegation commands

### Phase 2: Delegation Tools (CodeBuild + AgentCore Runtime)
- Create SKILL.md files that teach the agent how to delegate to CodeBuild
- Create SKILL.md files for AgentCore Runtime delegation
- Agent can: write buildspec → submit to CodeBuild → monitor → report results
- Agent can: write code → invoke AgentCore Runtime → stream output → report
- Add cost estimation before delegation

### Phase 3: EC2 + ECS Fargate Delegation
- SKILL.md for EC2 instance provisioning (GPU, long-running tasks)
- SKILL.md for ECS Fargate service deployment
- Agent manages lifecycle: provision → execute → cleanup
- Auto-termination and orphan cleanup Lambda

### Phase 4: Self-Extending Capabilities
- Agent can create new Lambda functions and register them as Gateway tools
- Agent can create new APIs and add them as OpenAPI targets
- Tool registry in DynamoDB tracks agent-created capabilities
- The agent's toolset grows over time based on what it builds

### Phase 5: Observability + Cost Control
- Cost tracking per environment and per day
- Budget enforcement (configurable monthly limit)
- Concurrency limits (max 3 active environments)
- Daily cost summary via Telegram
- CloudWatch dashboard widgets for execution environment usage

## AgentCore Services Used

- **AgentCore Runtime**: Firecracker microVMs for isolated execution
- **AgentCore Gateway**: MCP tool server for structured tool access
- **AgentCore Code Interpreter**: Sandboxed code execution for testing
- **AgentCore Identity**: OAuth credential management
- **AgentCore Policy**: Cedar-based authorization for tool access control
- **AgentCore Memory**: Persistent semantic memory across sessions
- **AgentCore Browser**: Cloud browser for web interaction tasks

## Current State (as of March 2026)

### Deployed and Working
- OpenClaw Gateway on ECS Fargate with Claude Sonnet 4.6
- Telegram channel locked to operator's user ID
- AgentCore Gateway created with Lambda tool targets
- MCP bridge script ready (mcp-gateway-bridge.mjs)
- 8 CDK stacks deployed (Storage, Gateway, Identity, Api, Memory, Scheduler, Builder, AgentCoreTools)

### Next Step
- Build the custom ACP runtime backend that bridges OpenClaw to AgentCore Runtime
- This is the critical integration that enables the "dynamic hands" vision
