---
inclusion: auto
---

# AgentCore Runtime Vision: Dynamic Self-Extending Agent Hands

## The Concept

The OpenClaw agent on Fargate is the "brain" — it reasons, plans, and decides what to do.
AgentCore Runtime provides the "hands" — dynamically provisioned Firecracker microVMs
that execute tasks in isolated, purpose-built environments.

The agent doesn't have a fixed set of tools. It can intelligently spin up new,
purpose-built AgentCore Runtime environments to execute specific tasks. Each microVM
is tailored to the task at hand, used, and discarded.

## Architecture

```
User (Telegram) → OpenClaw Gateway (Fargate, brain)
                      ↓
              Claude Sonnet 4.6 reasons about the task
                      ↓
              Custom ACP Runtime Backend (bridges to AgentCore)
                      ↓
              Dynamically creates purpose-built Firecracker microVMs:
                ├── Infra Runtime: CDK, AWS CLI, CloudFormation
                ├── Data Runtime: Python, pandas, matplotlib
                ├── Code Runtime: Node.js, testing frameworks
                ├── Browser Runtime: AgentCore Browser for web tasks
                └── Custom Runtime: agent decides what tools to install
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

### Phase 1: Basic ACP-to-AgentCore Bridge
- Create a custom OpenClaw plugin that registers as an ACP runtime backend
- Implement `ensureSession()` → `CreateAgentRuntime` or reuse existing
- Implement `runTurn()` → `InvokeAgentRuntime` (for reasoning tasks)
- Implement shell execution → `InvokeAgentRuntimeCommand` (for deterministic ops)
- Single Runtime type initially (general-purpose with CDK + AWS CLI)

### Phase 2: Dynamic Runtime Selection
- Agent analyzes the task and selects the appropriate Runtime type
- Multiple pre-configured Runtime templates (infra, data, code, browser)
- Runtime selection logic in the ACP backend based on task metadata

### Phase 3: Self-Extending Runtimes
- Agent can request custom Runtime configurations
- Install packages dynamically in the microVM
- Create new tool definitions on the fly
- Register new MCP tools in the Gateway based on what the agent builds

### Phase 4: Multi-Runtime Orchestration
- Agent can run multiple Runtimes in parallel
- Results from one Runtime feed into another
- Supervisor pattern: brain coordinates multiple specialized hands

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
