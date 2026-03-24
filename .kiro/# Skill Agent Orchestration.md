# Skill: Agent Orchestration

**Purpose:** How the AWS-enhanced OpenClaw system orchestrates multiple agents — building on OpenClaw's native `sessions_spawn` multi-agent system and extending it with AgentCore Runtime for long-running sub-agents and SQS for async task queuing.

---

## Hybrid Approach: OpenClaw Sub-Agents vs. AWS Extensions

OpenClaw already has a well-designed multi-agent system. **Use it as the foundation.** Extend only for use cases where the local defaults fall short.

| Pattern | OpenClaw Native | AWS Extension | When to Use AWS |
|---------|----------------|---------------|-----------------|
| Spawn sub-agent | `sessions_spawn` tool | AgentCore Runtime | Long-running tasks (> 15 min) |
| Concurrent agents | `maxConcurrent: 8` | AgentCore Runtime (auto-scales) | > 8 concurrent; elastic scaling |
| Task queuing | Synchronous / in-process | SQS queue | Async; decouple supervisor from workers |
| Agent state | In-memory session | DynamoDB `openclaw-sessions` | Persist across restarts; cloud deploy |
| Sub-agent result routing | Built-in session callbacks | EventBridge events | Cross-service; fanout patterns |

**Keep using `sessions_spawn`** for the standard supervisor → specialist pattern (Iris for support, Remy for sales). This is OpenClaw's proven pattern with known semantics.

**Use AgentCore Runtime** for:
- Builder sub-agents (CDK deploys can take 20–30 minutes)
- Long research or analysis jobs
- Any task where `runTimeoutSeconds: 900` (15 min) is insufficient

**Default config to preserve:**
```
maxSpawnDepth: 1     # allows supervisor → worker (depth 0 → depth 1)
maxChildrenPerAgent: 5
maxConcurrent: 8
runTimeoutSeconds: 900
```
Only override these when you have a specific reason. The defaults are well-considered.

---

## AWS Services Used

- **Amazon Bedrock AgentCore Runtime** — executes all agents in isolated serverless sessions
- **Amazon Bedrock (Strands SDK)** — the agent framework handling the orchestration loop
- **Amazon DynamoDB** — session state and agent registry
- **Amazon SQS** — async task queuing between agents (for long-running tasks)
- **Amazon EventBridge** — event-driven agent triggering
- **Amazon CloudWatch** — multi-agent trace visibility

---

## Key APIs and Operations

### AgentCore Runtime Invocation

```python
import boto3
import json

agentcore_runtime = boto3.client("bedrock-agentcore-runtime", region_name="us-east-1")

def invoke_agent(
    agent_arn: str,
    message: str,
    session_id: str,
    context: dict = None
) -> str:
    """
    Invoke an AgentCore Runtime agent and collect the full response.
    """
    payload = {
        "message": message,
        **(context or {})
    }

    response = agentcore_runtime.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        payload=json.dumps(payload),
        sessionId=session_id
    )

    # Collect streamed response
    full_response = ""
    for event in response.get("stream", []):
        if "payloadPart" in event:
            chunk = event["payloadPart"]["bytes"].decode("utf-8")
            try:
                chunk_data = json.loads(chunk)
                full_response += chunk_data.get("content", chunk)
            except json.JSONDecodeError:
                full_response += chunk

    return full_response
```

### Strands Multi-Agent Pattern (Sub-Agents as Tools)

The cleanest pattern for multi-agent orchestration in Strands:

```python
from strands import Agent, tool
from strands.models.bedrock import BedrockModel

model = BedrockModel(model_id="anthropic.claude-sonnet-4-6-20251015-v1:0")

# Define specialist agents
infra_agent = Agent(
    model=model,
    system_prompt="""You are an AWS infrastructure specialist.
    You deploy and manage Lambda functions, CDK stacks, ECS services,
    and other AWS resources. You always prefix agent-deployed resources with 'agent-'.
    You always confirm before deploying to production.""",
    tools=[deploy_lambda, deploy_cdk_stack, deploy_ecs_service, list_resources]
)

code_agent = Agent(
    model=model,
    system_prompt="""You are a software engineer specialized in writing production-ready code.
    You write clean, tested Python/TypeScript/JavaScript code.
    You always run tests before declaring code ready for deployment.
    You use the Code Interpreter to test your code.""",
    tools=[run_code_in_interpreter, write_tests, format_code]
)

comms_agent = Agent(
    model=model,
    system_prompt="""You are a customer support specialist.
    You respond to customers with warmth, clarity, and efficiency.
    You handle refunds up to $50 autonomously; anything larger requires operator approval.
    You never make promises you can't keep.""",
    tools=[send_email, reply_to_support_ticket, check_order_status]
)

research_agent = Agent(
    model=model,
    system_prompt="""You are a research specialist.
    You use the browser and search tools to gather accurate information.
    You synthesize findings into clear, actionable summaries.
    You always cite your sources.""",
    tools=[browse_web, search_internet, read_url]
)


# Supervisor orchestrator uses specialists as tools
@tool
def delegate_to_infra(task: str) -> str:
    """
    Delegate an infrastructure task to the infrastructure specialist agent.
    Use for: deploying Lambda functions, CDK stacks, ECS services,
    checking deployment status, scaling services.
    """
    return str(infra_agent(task))


@tool
def delegate_to_code(task: str) -> str:
    """
    Delegate a code writing or testing task to the code specialist agent.
    Use for: writing Lambda handlers, writing tests, creating scripts,
    refactoring code, debugging errors.
    """
    return str(code_agent(task))


@tool
def delegate_to_comms(task: str) -> str:
    """
    Delegate a communication task to the communications specialist agent.
    Use for: responding to customers, drafting emails, handling support tickets,
    writing announcements.
    """
    return str(comms_agent(task))


@tool
def delegate_to_research(task: str) -> str:
    """
    Delegate a research task to the research specialist agent.
    Use for: gathering information from the web, researching competitors,
    finding documentation, summarizing articles.
    """
    return str(research_agent(task))


# The supervisor is the primary agent that orchestrates all others
supervisor = Agent(
    model=model,
    system_prompt=SUPERVISOR_SYSTEM_PROMPT,
    tools=[
        delegate_to_infra,
        delegate_to_code,
        delegate_to_comms,
        delegate_to_research,
        # Plus direct tools for simple tasks
        send_telegram_message,
        get_aws_costs,
        check_deployment_status
    ]
)
```

### AgentCore Runtime Multi-Agent (Distributed Pattern)

For production deployments where each agent runs independently:

```python
# Agent ARNs (configured via environment variables)
AGENT_ARNS = {
    "supervisor": os.environ["SUPERVISOR_AGENT_ARN"],
    "infra": os.environ["INFRA_AGENT_ARN"],
    "code": os.environ["CODE_AGENT_ARN"],
    "comms": os.environ["COMMS_AGENT_ARN"],
    "research": os.environ["RESEARCH_AGENT_ARN"]
}


def route_to_agent(task: str, task_type: str = None) -> str:
    """
    Route a task to the appropriate specialist agent.
    """
    if task_type is None:
        task_type = classify_task(task)

    agent_arn = AGENT_ARNS.get(task_type, AGENT_ARNS["supervisor"])
    session_id = f"routed-{task_type}-{uuid.uuid4().hex[:8]}"

    return invoke_agent(
        agent_arn=agent_arn,
        message=task,
        session_id=session_id
    )


def classify_task(task: str) -> str:
    """Simple keyword-based task classification. Upgrade to model-based for production."""
    task_lower = task.lower()

    infra_keywords = ["deploy", "lambda", "ecs", "cdk", "cloudformation", "stack",
                       "fargate", "infrastructure", "provision", "scale", "aws"]
    if any(k in task_lower for k in infra_keywords):
        return "infra"

    code_keywords = ["write code", "build function", "create script", "test",
                      "debug", "implement", "program", "develop"]
    if any(k in task_lower for k in code_keywords):
        return "code"

    comms_keywords = ["customer", "email", "support", "refund", "reply", "respond",
                       "message", "communicate", "slack", "announce"]
    if any(k in task_lower for k in comms_keywords):
        return "comms"

    research_keywords = ["research", "search", "find", "look up", "investigate",
                          "browse", "web", "information", "what is", "how does"]
    if any(k in task_lower for k in research_keywords):
        return "research"

    return "supervisor"
```

### Parallel Agent Execution

For tasks that can be parallelized:

```python
import concurrent.futures
import threading


def run_agents_parallel(tasks: list[dict]) -> list[dict]:
    """
    Run multiple agent tasks in parallel.
    Each task: {"agent": "infra|code|comms|research", "task": "..."}

    Returns list of results in order.
    """
    results = [None] * len(tasks)
    errors = [None] * len(tasks)

    def run_task(index: int, task: dict):
        try:
            agent_arn = AGENT_ARNS[task["agent"]]
            session_id = f"parallel-{task['agent']}-{uuid.uuid4().hex[:8]}"
            result = invoke_agent(
                agent_arn=agent_arn,
                message=task["task"],
                session_id=session_id
            )
            results[index] = {"agent": task["agent"], "task": task["task"], "result": result}
        except Exception as e:
            errors[index] = str(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(run_task, i, task) for i, task in enumerate(tasks)]
        concurrent.futures.wait(futures, timeout=120)

    return [r for r in results if r is not None], [e for e in errors if e is not None]


# Example: Build and deploy a feature (code + infra in sequence)
def build_and_deploy(feature_description: str) -> dict:
    """Full feature build: write code → test → deploy."""

    # Step 1: Code agent writes the code
    code_result = route_to_agent(
        f"Write a Lambda function for: {feature_description}. "
        f"Test it in the Code Interpreter. Return the final code.",
        task_type="code"
    )

    # Step 2: Infra agent deploys it
    infra_result = route_to_agent(
        f"Deploy the following Lambda function code to AWS. "
        f"Name it based on its purpose. Here's the code:\n\n{code_result}",
        task_type="infra"
    )

    return {
        "code_written": code_result,
        "deployment_result": infra_result
    }
```

---

## Implementation Patterns

### Pattern 1: Supervisor-Specialist Model

The primary orchestration pattern — mirrors OpenClaw's main agent + sub-agents:

```
Supervisor Agent (Felix equivalent)
├── Receives all incoming messages
├── Plans and decides what needs to happen
├── Delegates specialist tasks to sub-agents
│   ├── Infrastructure Agent → AWS deployments
│   ├── Code Agent → Code writing and testing
│   ├── Comms Agent → Customer interactions (Iris equivalent)
│   └── Research Agent → Information gathering
└── Aggregates results and responds to user
```

### Pattern 2: Sequential Pipeline (Build → Test → Deploy)

```python
def execute_pipeline(steps: list[dict]) -> dict:
    """
    Execute a sequential pipeline of agent tasks.
    Each step can depend on the output of the previous step.

    steps = [
        {"agent": "code", "task": "Write stripe webhook handler"},
        {"agent": "code", "task": "Write tests for: {previous_output}"},
        {"agent": "infra", "task": "Deploy this code: {previous_output}"}
    ]
    """
    results = []
    previous_output = ""

    for step in steps:
        task = step["task"].replace("{previous_output}", previous_output)
        result = route_to_agent(task=task, task_type=step["agent"])
        results.append({"step": step["task"], "result": result})
        previous_output = result

    return {"pipeline_results": results, "final_output": previous_output}
```

### Pattern 3: Heartbeat Multi-Agent Check

```python
def run_heartbeat_checks() -> dict:
    """
    Parallel heartbeat checks across all domains.
    Runs every 30 minutes via EventBridge.
    """
    checks = [
        {"agent": "infra", "task": "Check for any Lambda errors, failed deployments, or unusual CloudWatch metrics in the last 30 minutes. Return brief status."},
        {"agent": "comms", "task": "Check for any unread customer support messages or pending tickets. Return count and priority items."},
        {"agent": "research", "task": "Quick check: any critical GitHub notifications, failed CI builds, or important alerts?"}
    ]

    results, errors = run_agents_parallel(checks)

    # Synthesize results with supervisor
    all_results = "\n\n".join([f"**{r['agent'].upper()}**: {r['result']}" for r in results])
    has_issues = any(
        word in all_results.lower()
        for word in ["error", "failed", "issue", "critical", "urgent", "problem"]
    )

    return {
        "checks": results,
        "has_issues": has_issues,
        "summary": all_results,
        "errors": errors
    }
```

### Pattern 4: Agent Registry and Dynamic Routing

```python
# DynamoDB table: openclaw-agent-registry
# Records: agent_id, capabilities[], arn, status

def get_available_agents() -> list:
    """Get all registered agents and their capabilities."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table("openclaw-agent-registry")
    response = table.scan()
    return [item for item in response.get("Items", []) if item.get("status") == "active"]


def route_by_capability(task: str, required_capability: str) -> str:
    """Route a task to an agent with the required capability."""
    agents = get_available_agents()
    capable_agents = [a for a in agents if required_capability in a.get("capabilities", [])]

    if not capable_agents:
        raise ValueError(f"No agent found with capability: {required_capability}")

    # Use first available (add load balancing logic here if needed)
    selected = capable_agents[0]
    session_id = f"capability-{required_capability}-{uuid.uuid4().hex[:8]}"

    return invoke_agent(
        agent_arn=selected["arn"],
        message=task,
        session_id=session_id
    )
```

---

## Example Code Snippets

### Full Orchestration Loop (Production Pattern)

```python
# gateway/router.py — the complete orchestration entry point

def handle_user_message(user_id: str, message: str, channel: str) -> str:
    """
    Complete message handling flow with full orchestration.
    This is what runs for every incoming user message.
    """
    # 1. Session management
    session_id = get_or_create_session(user_id, channel)

    # 2. Quick classification: is this a simple Q&A or a complex task?
    if is_simple_query(message):
        # Route directly to supervisor for simple questions
        return invoke_agent(AGENT_ARNS["supervisor"], message, session_id)

    # 3. For complex tasks, use the full supervisor orchestration
    # The supervisor will automatically delegate to specialists
    response = invoke_agent(
        agent_arn=AGENT_ARNS["supervisor"],
        message=message,
        session_id=session_id,
        context={
            "user_id": user_id,
            "channel": channel,
            "allow_delegation": True
        }
    )

    return response


def is_simple_query(message: str) -> bool:
    """Detect simple informational queries that don't need sub-agent delegation."""
    simple_starters = ["what", "how", "when", "who", "show me", "tell me",
                        "status", "check", "list"]
    message_lower = message.lower().strip()
    return any(message_lower.startswith(s) for s in simple_starters) and len(message) < 200
```

### Task Queue for Long-Running Multi-Agent Work

```python
import boto3

sqs = boto3.client("sqs")
TASK_QUEUE_URL = os.environ["TASK_QUEUE_URL"]


def queue_long_running_task(task: dict, agent_type: str, callback_session: str) -> str:
    """
    Queue a long-running task (e.g., deploying a complex CDK stack).
    Returns task_id for tracking. Results will be sent to callback_session.
    """
    import uuid
    task_id = uuid.uuid4().hex

    message_body = {
        "task_id": task_id,
        "agent_type": agent_type,
        "task": task,
        "callback_session": callback_session,
        "enqueued_at": datetime.now(timezone.utc).isoformat()
    }

    sqs.send_message(
        QueueUrl=TASK_QUEUE_URL,
        MessageBody=json.dumps(message_body),
        MessageGroupId=agent_type  # FIFO queue grouping by agent type
    )

    return task_id


# Lambda triggered by SQS to process queued tasks
def process_task_queue(event, context):
    for record in event["Records"]:
        task_data = json.loads(record["body"])

        result = route_to_agent(
            task=task_data["task"],
            task_type=task_data["agent_type"]
        )

        # Send result back to user session
        notify_operator(
            session_id=task_data["callback_session"],
            message=f"✅ Task `{task_data['task_id'][:8]}` complete:\n\n{result}"
        )
```

---

## Gotchas & Best Practices

### Session ID Consistency
Within a single multi-step task, use a consistent root session ID. For sub-agent calls, append a suffix: `{root_session}-{agent_type}-{step}`. This enables tracing the full workflow in CloudWatch.

### Timeout Management
AgentCore Runtime supports sessions up to 8 hours. For long-running tasks:
- Default agent timeout: 15 minutes
- Complex multi-agent pipeline: up to 60 minutes
- Full product build: up to 4 hours
Set appropriate timeouts in agent runtime configuration.

### Avoid Deep Delegation Chains
A supervisor calling an agent that calls another agent creates hard-to-debug chains. Maximum recommended depth: 2 (supervisor → specialist). If a specialist needs more help, it should return to the supervisor, not chain further.

### Context Passing Between Agents
When delegating to a sub-agent, include enough context for it to work independently. Don't assume the sub-agent has access to the same memory as the supervisor. Be explicit:
```
BAD: "Deploy the code"  (sub-agent doesn't know what code)
GOOD: "Deploy this Lambda function code to AWS as 'agent-stripe-handler':\n\n[code]"
```

### Circuit Breaker Pattern
If a specialist agent fails repeatedly, fall back to the supervisor doing the task directly:
```python
MAX_AGENT_RETRIES = 2

def safe_delegate(task: str, agent_type: str) -> str:
    for attempt in range(MAX_AGENT_RETRIES):
        try:
            return route_to_agent(task=task, task_type=agent_type)
        except Exception as e:
            if attempt == MAX_AGENT_RETRIES - 1:
                # Fall back to supervisor
                return route_to_agent(task=f"[Specialist failed] {task}", task_type="supervisor")
    return "Task failed after retries"
```

### Parallel Agents Have Independent Memory
Each parallel agent invocation runs in its own session with its own memory context. If you need agents to share state mid-task, use a shared DynamoDB item as a "blackboard" that all agents read/write to.