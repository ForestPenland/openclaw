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
              Compute Router (analyzes task → selects backend)
                      ↓
              ┌─────────────────────────────────────────────┐
              │ AgentCore Runtime  │ Quick tasks, code test  │
              │ AWS CodeBuild      │ Builds, CDK deploy      │
              │ EC2 Instance       │ GPU, long-running       │
              │ ECS Fargate        │ Persistent services     │
              │ Code Interpreter   │ Sandboxed testing       │
              │ AgentCore Browser  │ Web interaction         │
              └─────────────────────────────────────────────┘
                      ↓
              Results flow back to brain → Telegram response
```

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

### Phase 1: ACP-to-AgentCore Bridge (Core)
- Create a custom OpenClaw plugin that registers as an ACP runtime backend
- Implement `ensureSession()` → AgentCore Runtime session management
- Implement `runTurn()` → `InvokeAgentRuntime` (for reasoning tasks)
- Implement shell execution → `InvokeAgentRuntimeCommand` (for deterministic ops)
- Single Runtime type initially (general-purpose with CDK + AWS CLI)

### Phase 2: Compute Router + Multiple Backends
- Add CodeBuild backend for builds and CDK deployments
- Add EC2 backend for long-running and GPU workloads
- Implement the compute router that analyzes tasks and selects backends
- Agent can override the router's decision when it knows better

### Phase 3: Self-Extending Capabilities
- Agent can create new Lambda functions and register them as Gateway tools
- Agent can create new APIs and add them as OpenAPI targets
- Tool registry in DynamoDB tracks agent-created capabilities
- The agent's toolset grows over time based on what it builds

### Phase 4: ECS Fargate Backend + Persistent Services
- Agent can deploy containerized services on Fargate
- Services persist beyond the task (APIs, workers, scheduled jobs)
- Agent manages service lifecycle (deploy, update, teardown)

### Phase 5: Multi-Environment Orchestration
- Agent can run multiple environments in parallel
- Results from one environment feed into another
- Supervisor pattern: brain coordinates multiple specialized hands
- Cost tracking and budget enforcement across all environments

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
