# Skill: Bedrock Model Invocation

**Purpose:** How the AWS-enhanced OpenClaw system invokes AI models via Amazon Bedrock — replacing the direct Anthropic API calls in the OpenClaw codebase with Bedrock's Converse API for centralized cost tracking, guardrails, and cross-region inference.

---

## Hybrid Approach: OpenClaw Model Config vs. Bedrock

OpenClaw is **model-agnostic by design**. It supports Claude, OpenAI, Gemini, Mistral, and local models. You are not replacing OpenClaw's model-agnostic architecture — you are configuring it to use Bedrock as the provider in production.

| Scenario | Use | Notes |
|----------|-----|-------|
| Local development | Direct Anthropic API (`@anthropic-ai/sdk`) | Simpler setup, no AWS credentials needed |
| Cloud / production | Bedrock Converse API | CloudWatch cost tracking, Guardrails, cross-region |
| Testing a new model | Bedrock (any supported model) | Unified interface via Converse API |
| Local models (Ollama) | Keep Ollama config as-is | Bedrock doesn't support local models |

**What changes:** The OpenClaw model provider config (environment variables or config file) is switched from `provider: anthropic` to `provider: bedrock`. The model ID format changes from `claude-sonnet-4-6-20251015` to `anthropic.claude-sonnet-4-6-20251015-v1:0` or the cross-region inference profile `us.anthropic.claude-sonnet-4-6-v1:0`.

**What stays the same:** All of OpenClaw's prompt structure, workspace file injection, skill injection, tool calling, and session management behavior is unchanged. These are model-provider agnostic.

**Guardrails:** For the builder capability specifically, always use a Bedrock Guardrail. Apply `guardrailIdentifier` and `guardrailVersion` to every model call that could trigger infrastructure changes.

---

## AWS Services Used

- **Amazon Bedrock Runtime** — primary model invocation service
- **Amazon Bedrock** — model access management and quotas
- **AWS IAM** — permissions for model invocation
- **Amazon CloudWatch** — token usage and latency metrics

---

## Key APIs and Operations

### Bedrock Runtime Client Setup

```python
import boto3
from botocore.config import Config

# Standard client
bedrock_runtime = boto3.client(
    "bedrock-runtime",
    region_name="us-east-1",
    config=Config(
        retries={"max_attempts": 3, "mode": "adaptive"},
        read_timeout=300,    # 5 min for long generations
        connect_timeout=10
    )
)
```

### Converse API (Primary Interface)

The Converse API provides a consistent interface across all supported models. **Always use this over InvokeModel** unless you need model-specific features not exposed by Converse.

```python
def invoke_with_converse(
    model_id: str,
    messages: list,
    system_prompt: str = None,
    tools: list = None,
    max_tokens: int = 4096,
    temperature: float = 0.7
) -> dict:
    """
    Invoke a Bedrock model using the Converse API.

    Args:
        model_id: Bedrock model ID (e.g., 'anthropic.claude-sonnet-4-6-20251015-v1:0')
        messages: List of {"role": "user"|"assistant", "content": [{"text": "..."}]}
        system_prompt: Optional system prompt
        tools: Optional list of tool definitions
        max_tokens: Maximum response tokens
        temperature: Sampling temperature (0-1)

    Returns:
        Dict with 'content', 'stop_reason', 'usage'
    """
    kwargs = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": {
            "maxTokens": max_tokens,
            "temperature": temperature,
        }
    }

    if system_prompt:
        kwargs["system"] = [{"text": system_prompt}]

    if tools:
        kwargs["toolConfig"] = {"tools": tools}

    response = bedrock_runtime.converse(**kwargs)

    output_message = response["output"]["message"]
    content = output_message["content"]
    stop_reason = response["stopReason"]
    usage = response["usage"]

    return {
        "content": content,
        "text": content[0]["text"] if content and "text" in content[0] else None,
        "stop_reason": stop_reason,
        "input_tokens": usage["inputTokens"],
        "output_tokens": usage["outputTokens"],
        "tool_uses": [c for c in content if "toolUse" in c]
    }
```

### ConverseStream API (Streaming)

For real-time streaming responses — use this for Telegram/Slack/web chat where users expect progressive output:

```python
def invoke_streaming(
    model_id: str,
    messages: list,
    system_prompt: str = None,
    on_token: callable = None
) -> str:
    """
    Stream a response from Bedrock, calling on_token for each chunk.

    Args:
        model_id: Bedrock model ID
        messages: Conversation messages
        system_prompt: Optional system prompt
        on_token: Callback called with each text token (for real-time display)

    Returns:
        Complete response text
    """
    kwargs = {
        "modelId": model_id,
        "messages": messages,
        "inferenceConfig": {"maxTokens": 4096, "temperature": 0.7}
    }
    if system_prompt:
        kwargs["system"] = [{"text": system_prompt}]

    response = bedrock_runtime.converse_stream(**kwargs)

    full_text = ""
    for event in response.get("stream", []):
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"]["delta"]
            if "text" in delta:
                token = delta["text"]
                full_text += token
                if on_token:
                    on_token(token)

        elif "messageStop" in event:
            stop_reason = event["messageStop"]["stopReason"]
            break

    return full_text
```

### Tool Use Pattern (Agentic Loop)

The complete agentic loop with tool use via Converse API:

```python
def agentic_loop(
    model_id: str,
    system_prompt: str,
    initial_message: str,
    tools: list,
    tool_executor: callable,
    max_iterations: int = 10
) -> str:
    """
    Run the full agentic loop: model → tool calls → results → model → ...
    Stops when model returns stop_reason='end_turn' or max_iterations reached.
    """
    messages = [{"role": "user", "content": [{"text": initial_message}]}]

    for iteration in range(max_iterations):
        result = invoke_with_converse(
            model_id=model_id,
            messages=messages,
            system_prompt=system_prompt,
            tools=tools
        )

        # Add assistant's response to history
        messages.append({
            "role": "assistant",
            "content": result["content"]
        })

        # If no tool use, we're done
        if result["stop_reason"] == "end_turn":
            return result["text"]

        # Execute tool calls
        if result["stop_reason"] == "tool_use" and result["tool_uses"]:
            tool_results = []
            for tool_use in result["tool_uses"]:
                tool_name = tool_use["toolUse"]["name"]
                tool_input = tool_use["toolUse"]["input"]
                tool_use_id = tool_use["toolUse"]["toolUseId"]

                # Execute the tool
                try:
                    tool_output = tool_executor(tool_name, tool_input)
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": str(tool_output)}],
                            "status": "success"
                        }
                    })
                except Exception as e:
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_use_id,
                            "content": [{"text": f"Error: {str(e)}"}],
                            "status": "error"
                        }
                    })

            # Add tool results to conversation
            messages.append({
                "role": "user",
                "content": tool_results
            })

    return "Max iterations reached. Task may be incomplete."
```

---

## Model IDs Reference (March 2026)

| Model | Bedrock Model ID | Context | Best For |
|-------|-----------------|---------|---------|
| Claude Sonnet 4.6 | `anthropic.claude-sonnet-4-6-20251015-v1:0` | 1M tokens | Default — best capability/cost |
| Claude Opus 4.6 | `anthropic.claude-opus-4-6-20260115-v1:0` | 1M tokens | Complex reasoning, architecture |
| Claude Haiku 4.5 | `anthropic.claude-haiku-4-5-20251001-v1:0` | 200K tokens | Simple tasks, high volume, low cost |
| Amazon Nova Pro | `amazon.nova-pro-v1:0` | 300K tokens | AWS-native, cost-optimized |
| Amazon Nova Lite | `amazon.nova-lite-v1:0` | 300K tokens | High throughput, low latency |
| Amazon Nova Micro | `amazon.nova-micro-v1:0` | 128K tokens | Extremely fast, minimal cost |

---

## Implementation Patterns

### Pattern 1: Strands Agent with Bedrock (Recommended)

Use Strands SDK instead of raw Converse API calls wherever possible:

```python
from strands import Agent
from strands.models.bedrock import BedrockModel

model = BedrockModel(
    model_id="anthropic.claude-sonnet-4-6-20251015-v1:0",
    region_name="us-east-1",
    streaming=True,
    max_tokens=4096,
    temperature=0.7,
    # Cross-region inference profile (for capacity flexibility)
    cross_region_inference=True
)

agent = Agent(
    model=model,
    system_prompt=system_prompt,
    tools=my_tools
)

# The agent loop is handled automatically by Strands
response = agent(user_message)
```

### Pattern 2: Cross-Region Inference

For higher throughput and availability, use cross-region inference profiles:

```python
# Instead of a single region model ID, use an inference profile
model = BedrockModel(
    # Cross-region inference profile (routes to available regions)
    model_id="us.anthropic.claude-sonnet-4-6-20251015-v1:0",
    region_name="us-east-1"
)
```

### Pattern 3: Model Routing by Task Complexity

Route to different models based on task complexity to optimize cost:

```python
def select_model(task_description: str) -> str:
    """Select the most cost-effective model for the task."""
    task_lower = task_description.lower()

    # Simple tasks → Haiku (fast, cheap)
    simple_patterns = ["summarize", "classify", "extract", "translate", "format"]
    if any(p in task_lower for p in simple_patterns) and len(task_description) < 500:
        return "anthropic.claude-haiku-4-5-20251001-v1:0"

    # Complex reasoning → Opus
    complex_patterns = ["architect", "design", "complex", "strategy", "plan infrastructure"]
    if any(p in task_lower for p in complex_patterns):
        return "anthropic.claude-opus-4-6-20260115-v1:0"

    # Default → Sonnet
    return "anthropic.claude-sonnet-4-6-20251015-v1:0"
```

### Pattern 4: Token Budget Management (1M Context Window)

For long context operations, manage the context window explicitly:

```python
def build_messages_within_budget(
    conversation_history: list,
    memories: list,
    max_input_tokens: int = 800_000  # Leave room for output
) -> list:
    """
    Build a message list that fits within the token budget.
    Prioritizes: recent messages > relevant memories > older messages
    """
    # Approximate token counts (4 chars ≈ 1 token)
    def estimate_tokens(text: str) -> int:
        return len(text) // 4

    messages = []
    token_count = 0

    # Always include last N messages
    recent = conversation_history[-5:]
    for msg in recent:
        tokens = estimate_tokens(str(msg))
        messages.append(msg)
        token_count += tokens

    # Add relevant memories
    for memory in memories[:10]:
        tokens = estimate_tokens(str(memory))
        if token_count + tokens < max_input_tokens:
            messages.insert(0, {"role": "user", "content": [{"text": f"[Memory: {memory['content']}]"}]})
            token_count += tokens

    return messages
```

---

## Example Code Snippets

### Direct Tool Definition for Converse API

```python
INFRASTRUCTURE_TOOLS = [
    {
        "toolSpec": {
            "name": "deploy_lambda_function",
            "description": "Deploy a new Lambda function to AWS with the provided code.",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "function_name": {
                            "type": "string",
                            "description": "Name for the Lambda function (will be prefixed with 'agent-')"
                        },
                        "code": {
                            "type": "string",
                            "description": "The Python code for the Lambda function"
                        },
                        "handler": {
                            "type": "string",
                            "description": "Handler path (e.g., 'handler.main')",
                            "default": "handler.main"
                        },
                        "description": {
                            "type": "string",
                            "description": "What this function does"
                        }
                    },
                    "required": ["function_name", "code"]
                }
            }
        }
    }
]
```

### Tracking Token Usage for Cost Control

```python
class TokenTracker:
    def __init__(self, budget_per_session: int = 500_000):
        self.budget = budget_per_session
        self.used = 0

    def track(self, usage: dict):
        self.used += usage.get("inputTokens", 0) + usage.get("outputTokens", 0)

    def within_budget(self) -> bool:
        return self.used < self.budget

    def estimated_cost(self, model_id: str) -> float:
        # Approximate costs per 1K tokens (input/output)
        costs = {
            "claude-sonnet-4-6": (0.003, 0.015),
            "claude-opus-4-6": (0.015, 0.075),
            "claude-haiku-4-5": (0.00025, 0.00125)
        }
        for key, (input_cost, output_cost) in costs.items():
            if key in model_id:
                return (self.used / 1000) * input_cost  # Simplified estimate
        return 0.0
```

---

## Gotchas & Best Practices

### Always Use Converse API, Not InvokeModel
The Converse API works with all models through a consistent interface. `InvokeModel` requires model-specific formatting — avoid it unless absolutely necessary.

### Streaming for User-Facing Interactions
Always use `converse_stream` for any interaction where a user is waiting for a response. Even for Telegram, stream and send chunks rather than waiting for full completion.

### System Prompt Strategy
Keep the system prompt focused and at most 2,000 tokens. Don't inject the entire workspace into the system prompt — load it once at session start and reference key sections.

### Handle ThrottlingException
Bedrock has per-model throughput limits. Implement exponential backoff:
```python
from botocore.exceptions import ClientError
import time

def invoke_with_retry(model_id, messages, max_retries=3):
    for attempt in range(max_retries):
        try:
            return bedrock_runtime.converse(modelId=model_id, messages=messages)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ThrottlingException":
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                time.sleep(wait_time)
            else:
                raise
    raise Exception("Max retries exceeded")
```

### Model ID Versions Change
Model IDs include version dates (e.g., `claude-sonnet-4-6-20251015`). Pin to specific versions in production; use a config value rather than hardcoding throughout.

### Cross-Region Inference for Production
Use `us.anthropic.claude-*` prefix (cross-region inference profile) for production deployments to benefit from automatic traffic routing to regions with available capacity. This significantly reduces throttling.

### Cost Optimization
- Use Claude Haiku for classification, routing, simple summarization tasks
- Use Claude Sonnet for most agent work
- Reserve Claude Opus for architectural decisions, complex multi-step reasoning
- Monitor token usage in CloudWatch; set up cost alarms