# AWS Bedrock & Bedrock AgentCore — Comprehensive Capabilities Reference

*Deep technical reference for extending the OpenClaw open-source project with AWS Bedrock and AgentCore services*

---

## Overview

Amazon Bedrock is AWS's fully managed service for accessing foundation models (FMs) from leading AI companies through a single API. Amazon Bedrock **AgentCore** is the production-grade platform layered on top of Bedrock for building, deploying, and operating AI agents at enterprise scale.

AgentCore reached **General Availability in October 2025** (initially previewed at AWS Summit NYC in March 2025). It is available in 9 AWS regions: US East (N. Virginia), US East (Ohio), US West (Oregon), Asia Pacific (Mumbai/Singapore/Sydney/Tokyo), Europe (Frankfurt/Ireland).

---

## OpenClaw Integration Map

This section maps each AWS Bedrock / AgentCore service to the specific OpenClaw component it replaces or enhances. Use this as the primary reference when deciding whether to use an AWS service or keep OpenClaw's native implementation.

| OpenClaw Component | OpenClaw Implementation | AWS Bedrock / AgentCore Replacement | Section |
|---|---|---|---|
| Model provider | Direct Anthropic SDK (`@anthropic-ai/sdk`) | **Bedrock Converse API** | Section 1 |
| Memory semantic index | SQLite + BM25/vector hybrid | **AgentCore Memory** | Section 3 |
| Sub-agent execution (long-running) | Local sessions (`sessions_spawn`) | **AgentCore Runtime** | Section 2 |
| Tool server | Manual tool registration in config | **AgentCore Gateway (MCP)** | Section 4 |
| User authentication | DM pairing code | **AgentCore Identity + Cognito** | Section 5 |
| Code execution sandbox | `bash` tool (no isolation) | **AgentCore Code Interpreter** | Section 6 |
| Content safety | None | **Bedrock Guardrails** | Section 1 |
| Observability | Console logs | **AgentCore Observability (OTEL)** | Section 8 |
| Policy enforcement | None | **AgentCore Policy (Cedar)** | Section 7 |

**Important:** Not all of these need to be adopted at once. See `research_summary.md` Part 7 for the recommended migration sequence and decision criteria for each component.

**Keep OpenClaw native when:**
- Running locally for development
- The feature works well and you don't need cloud-scale
- Switching would require significant testing with unknown benefit

**Switch to AWS when:**
- You need the feature to work across multiple ECS instances
- You need managed scalability (e.g., memory index for > 1 agent)
- The feature introduces risk that AWS can mitigate (e.g., Guardrails for builder capability)

---

## Section 1: Amazon Bedrock — Foundation Model Access

### Supported Models (Bedrock)

Bedrock provides access to models from multiple providers through a unified API:

| Provider | Models | Context Window |
|----------|--------|---------------|
| Anthropic | Claude Opus 4.6, Sonnet 4.6, Sonnet 4.5, Sonnet 4, Haiku 4.5 | 1M tokens (Opus 4.6, Sonnet 4.6) |
| Amazon | Nova Pro, Nova Lite, Nova Micro | Varies |
| Meta | Llama 3.x family | Varies |
| Mistral | Mistral 7B, Mixtral 8x7B, Mistral Large | Varies |
| AI21 Labs | Jamba models | Varies |
| Cohere | Command R, Command R+ | Varies |

**Recommended for OpenClaw AWS:** Claude Sonnet 4.6 (balance of capability/cost), Claude Opus 4.6 (complex reasoning), Amazon Nova Pro (cost-optimized, AWS-native).

### Bedrock Inference APIs

#### Converse API (Recommended)

The **Converse API** is the primary, consistent interface that works across all supported models:

```python
import boto3

bedrock_runtime = boto3.client("bedrock-runtime", region_name="us-east-1")

response = bedrock_runtime.converse(
    modelId="anthropic.claude-sonnet-4-6-20251015-v1:0",
    messages=[
        {
            "role": "user",
            "content": [{"text": "What should I build today?"}]
        }
    ],
    system=[{"text": "You are a helpful AI assistant."}],
    inferenceConfig={
        "maxTokens": 4096,
        "temperature": 0.7,
        "topP": 0.9
    }
)

output_text = response["output"]["message"]["content"][0]["text"]
```

#### ConverseStream API (Streaming)

```python
response = bedrock_runtime.converse_stream(
    modelId="anthropic.claude-sonnet-4-6-20251015-v1:0",
    messages=[...],
    system=[...],
)

for event in response["stream"]:
    if "contentBlockDelta" in event:
        delta = event["contentBlockDelta"]["delta"]
        if "text" in delta:
            print(delta["text"], end="", flush=True)
```

#### Tool Use with Converse API

```python
tools = [
    {
        "toolSpec": {
            "name": "deploy_lambda",
            "description": "Deploy a Lambda function to AWS",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "function_name": {"type": "string"},
                        "runtime": {"type": "string"},
                        "code_zip_path": {"type": "string"}
                    },
                    "required": ["function_name", "runtime"]
                }
            }
        }
    }
]

response = bedrock_runtime.converse(
    modelId="anthropic.claude-sonnet-4-6-20251015-v1:0",
    messages=[...],
    toolConfig={"tools": tools}
)
```

### Bedrock Model IDs (March 2026)

```
anthropic.claude-opus-4-6-20260115-v1:0       # Claude Opus 4.6
anthropic.claude-sonnet-4-6-20251015-v1:0     # Claude Sonnet 4.6
anthropic.claude-haiku-4-5-20251001-v1:0      # Claude Haiku 4.5
amazon.nova-pro-v1:0                           # Amazon Nova Pro
amazon.nova-lite-v1:0                          # Amazon Nova Lite
amazon.nova-micro-v1:0                         # Amazon Nova Micro
```

### IAM Permissions for Bedrock

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:Converse",
        "bedrock:ConverseStream"
      ],
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-6*",
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-opus-4-6*"
      ]
    }
  ]
}
```

---

## Section 2: Amazon Bedrock AgentCore — Platform Overview

AgentCore is a composable platform: each service operates independently, allowing flexible combination based on use case. Eight core services:

1. **Runtime** — Serverless agent execution environment
2. **Memory** — Managed short-term and long-term memory
3. **Gateway** — MCP-compatible tool server and API converter
4. **Identity** — Agent authentication and access management
5. **Tools** — Built-in Code Interpreter and Browser Tool
6. **Observability** — OpenTelemetry-compatible tracing and monitoring
7. **Evaluations** — Agent quality assessment framework
8. **Policy** — Cedar-based access control for tool invocations

---

## Section 3: AgentCore Runtime

### What It Is

A **secure, serverless runtime environment** for deploying and scaling AI agents and tools. Key characteristics:

- **Fast cold starts** for real-time interactions
- **Extended runtime** for async agents (up to **8 hours**)
- **True session isolation** per execution
- **Built-in identity** (no separate auth setup needed for basic deployments)
- **Multi-modal and multi-agent** workload support
- **VPC support** for private network access

### Deployment Methods

**Option 1: Bedrock AgentCore Starter Toolkit (CLI)**

```bash
pip install bedrock-agentcore-starter-toolkit

# Scaffold new agent project
agentcore create --framework strands --model claude-sonnet-4-6

# Deploy to Runtime
agentcore deploy --region us-east-1
```

**Option 2: AWS CDK (Python)**

```python
from aws_cdk import Stack
import aws_cdk.aws_bedrock_agentcore_alpha as agentcore

class AgentStack(Stack):
    def __init__(self, scope, id, **kwargs):
        super().__init__(scope, id, **kwargs)

        runtime = agentcore.Runtime(
            self,
            "OpenClawAgent",
            artifact=agentcore.AgentRuntimeArtifact.from_code_asset(
                directory="./agent",
                runtime=agentcore.PythonRuntime.PYTHON_3_13,
                entrypoint="agent.py"
            )
        )
```

**Option 3: CloudFormation**

```yaml
Resources:
  AgentRuntime:
    Type: AWS::BedrockAgentCore::Runtime
    Properties:
      RuntimeName: openclaw-agent
      ContainerImageUri: !Ref ContainerImageUri
      RoleArn: !GetAtt AgentRuntimeRole.Arn
      NetworkConfig:
        NetworkMode: VPC
        VpcConfig:
          SubnetIds: !Ref PrivateSubnets
          SecurityGroupIds: !Ref AgentSecurityGroup
```

### Agent Code Pattern (Strands + AgentCore)

```python
# agent.py — entrypoint for AgentCore Runtime
from strands import Agent
from strands.models.bedrock import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient

app = BedrockAgentCoreApp()

model = BedrockModel(
    model_id="anthropic.claude-sonnet-4-6-20251015-v1:0",
    region_name="us-east-1"
)

memory_client = MemoryClient()

@app.entrypoint
def agent_handler(payload: dict, context) -> dict:
    session_id = context.session_id
    user_message = payload.get("message", "")

    # Retrieve relevant memories
    memories = memory_client.retrieve_memories(
        query=user_message,
        session_id=session_id
    )

    agent = Agent(
        model=model,
        system_prompt=build_system_prompt(memories),
        tools=[deploy_infrastructure, run_code, browse_web]
    )

    response = agent(user_message)

    # Store new memories
    memory_client.store_event(session_id=session_id, content=user_message, actor="user")
    memory_client.store_event(session_id=session_id, content=str(response), actor="agent")

    return {"response": str(response)}

if __name__ == "__main__":
    app.run()
```

### Runtime Invocation (Client Side)

```python
import boto3

agentcore_runtime = boto3.client("bedrock-agentcore-runtime", region_name="us-east-1")

response = agentcore_runtime.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:us-east-1:123456789:agent-runtime/openclaw-agent",
    payload={"message": "Deploy a new Lambda function for image processing"},
    sessionId="user-123-session-456"
)

for event in response["stream"]:
    if "payloadPart" in event:
        print(event["payloadPart"]["bytes"].decode())
```

---

## Section 4: AgentCore Memory

### Architecture

AgentCore Memory is a fully managed service supporting both short-term (within-session) and long-term (cross-session) memory.

#### Short-Term Memory
- Captures raw interaction data as immutable events
- Organized by actor (user/agent) and session
- Synchronous storage; events stored in order
- Serves as input for long-term memory extraction

#### Long-Term Memory Strategies

| Strategy | What It Does |
|----------|-------------|
| **Semantic** | Extracts facts and knowledge from conversations |
| **Episodic** | Captures meaningful moments/experiences as compact records (not raw events) |
| **User Profile** | Builds persistent user preference models |

### Memory API Operations

```python
import boto3

memory_client = boto3.client("bedrock-agentcore-memory", region_name="us-east-1")

# Create a memory store
memory_store = memory_client.create_memory_store(
    name="openclaw-agent-memory",
    description="Long-term memory for OpenClaw AWS agent",
    memoryStrategies=[
        {"semanticMemoryStrategy": {"name": "facts-strategy"}},
        {"episodicMemoryStrategy": {"name": "experiences-strategy"}}
    ]
)

# Ingest events (short-term memory)
memory_client.ingest_conversation_events(
    memoryStoreArn=memory_store["memoryStoreArn"],
    sessionId="session-123",
    conversationEvents=[
        {
            "conversationEvent": {
                "content": [{"text": "Deploy a Stripe integration for my e-commerce app"}],
                "role": "USER",
                "timestamp": "2026-03-21T10:00:00Z"
            }
        },
        {
            "conversationEvent": {
                "content": [{"text": "I've deployed the Stripe integration. Lambda function 'stripe-handler' is live."}],
                "role": "ASSISTANT",
                "timestamp": "2026-03-21T10:05:00Z"
            }
        }
    ]
)

# Retrieve relevant memories (semantic search)
memories = memory_client.retrieve_memory_records(
    memoryStoreArn=memory_store["memoryStoreArn"],
    query="What Stripe work has been done?",
    maxResults=10
)

# Get a specific memory record
record = memory_client.get_memory_record(
    memoryStoreArn=memory_store["memoryStoreArn"],
    memoryRecordId="record-id-123"
)

# List all memories
all_records = memory_client.list_memory_records(
    memoryStoreArn=memory_store["memoryStoreArn"]
)
```

### Episodic Memory Strategy

Episodic memory captures meaningful slices of interactions rather than raw events. It:
- Identifies important moments in conversations
- Summarizes them into compact records
- Organizes them for relevance-based retrieval
- Avoids storing every raw event (reduces noise)

Particularly useful for: past decisions, problem-solving approaches, user preferences demonstrated through actions, deployment history.

---

## Section 5: AgentCore Gateway

### What It Is

AgentCore Gateway is a **managed MCP server** that converts APIs, Lambda functions, and existing services into MCP-compatible tools. It serves as the centralized tool server for agents.

### Key Capabilities

- Converts **OpenAPI specs → MCP tools** automatically
- Converts **Lambda functions → MCP tools** automatically
- Converts **Smithy models → MCP tools** automatically
- Aggregates multiple MCP servers into a single endpoint
- Handles **inbound auth** (OAuth/JWT validation) and **outbound auth** (credential injection)
- **Policy integration** (Cedar policies evaluated on every tool call)

### Creating a Gateway

```python
import boto3

gateway_client = boto3.client("bedrock-agentcore-gateway", region_name="us-east-1")

# Create a gateway
gateway = gateway_client.create_gateway(
    name="openclaw-tool-gateway",
    description="Tool gateway for OpenClaw AWS agents",
    roleArn="arn:aws:iam::123456789:role/AgentCoreGatewayRole",
    authorizerConfig={
        "allowedJwtAudiences": ["openclaw-agent"],
        "cognitoTokenEndpoint": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_xxx"
    }
)

# Add a Lambda function as a tool target
target = gateway_client.create_gateway_target(
    gatewayArn=gateway["gatewayArn"],
    name="aws-infrastructure-tool",
    description="Tools for provisioning AWS infrastructure",
    targetConfig={
        "lambda": {
            "lambdaArn": "arn:aws:lambda:us-east-1:123456789:function:infra-provisioner",
            "toolSchema": {
                "smithy": {
                    "inlineSmithyDefinition": "..."  # Smithy model defining tool interface
                }
            }
        }
    }
)

# Add an OpenAPI spec as a tool target
openapi_target = gateway_client.create_gateway_target(
    gatewayArn=gateway["gatewayArn"],
    name="github-api-tool",
    targetConfig={
        "openApi": {
            "inlineDocument": open("github_openapi.json").read(),
            "credentialProvider": {
                "apiKeyCredentialProvider": {
                    "credentialParameterName": "GITHUB_TOKEN",
                    "credentialPrefix": "Bearer "
                }
            }
        }
    }
)
```

### Using Gateway with Strands Agent

```python
from strands import Agent
from strands.mcp import MCPClient

# Connect to AgentCore Gateway as MCP server
mcp_client = MCPClient(
    server_url=f"https://{gateway_endpoint}/mcp",
    auth_token=get_agent_access_token()
)

agent = Agent(
    model=model,
    mcp_clients=[mcp_client]  # All Gateway tools available to agent
)
```

---

## Section 6: AgentCore Identity

### What It Is

A **secure, scalable agent identity and access management service** compatible with standard identity providers (Okta, Entra, Amazon Cognito, Auth0, Ping Identity, custom OAuth providers).

### Authentication Patterns

**Pattern 1: User-Delegated Access (OAuth 2.0 Authorization Code Grant)**
- Agent accesses user-specific data with explicit user consent
- Includes consent step where resource owner authorizes agent within specific scopes
- Use case: Agent acting on behalf of a specific user (Felix acting on Nat's behalf)

**Pattern 2: Machine-to-Machine (OAuth 2.0 Client Credentials Grant)**
- Direct authentication between systems without user interaction
- Use case: Autonomous agents running scheduled tasks, infrastructure management

### Setting Up Identity

```python
import boto3

identity_client = boto3.client("bedrock-agentcore-identity", region_name="us-east-1")

# Register an OAuth credential provider (for outbound tool calls)
credential_provider = identity_client.create_oauth2_credential_provider(
    name="github-credential-provider",
    credentialProviderVendor="CustomOIDC",
    oauth2ProviderConfigInput={
        "customOAuthProviderConfig": {
            "oauthDiscoveryUrl": "https://github.com/.well-known/openid-configuration",
            "clientId": "your-github-app-client-id",
            "clientSecretArn": "arn:aws:secretsmanager:us-east-1:123456789:secret:github-app-secret",
            "scopes": ["repo", "workflow"]
        }
    }
)

# Configure inbound authorizer (for incoming requests to Runtime/Gateway)
authorizer_config = {
    "jwtAuthorizer": {
        "issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_xxx",
        "allowedAudiences": ["openclaw-agent-runtime"],
        "allowedClients": ["openclaw-web-client", "openclaw-mobile-client"],
        "allowedScopes": ["agent:invoke", "agent:read"]
    }
}
```

### Getting Credentials for Outbound Tool Calls

When an agent needs to call external services (GitHub, Slack, AWS APIs), it requests credentials from the AgentCore Credential Provider:

```python
# Inside agent code — get credentials for a specific tool/service
credentials = identity_client.get_token_for_resource(
    workloadAccessToken=context.workload_access_token,
    resourceScope="github:repo:write",
    credentialProviderArn=github_credential_provider_arn
)

# Use credentials to call GitHub API
import requests
headers = {"Authorization": f"Bearer {credentials['accessToken']}"}
requests.post("https://api.github.com/repos/org/repo/dispatches", headers=headers, json={...})
```

---

## Section 7: AgentCore Tools

### Code Interpreter

A **fully managed, sandboxed code execution environment** supporting:

- **Languages:** Python, JavaScript, TypeScript
- **Capabilities:** Complex data analysis, mathematical computations, visualization generation, file operations
- **AWS CLI:** Can run AWS CLI commands directly within the sandbox
- **Security:** Isolated execution, no access to host system

```python
from bedrock_agentcore.tools import CodeInterpreterClient

code_client = CodeInterpreterClient()

# Execute Python code in sandbox
result = code_client.execute(
    language="python",
    code="""
import boto3
import json

# List all Lambda functions
client = boto3.client('lambda')
functions = client.list_functions()
for fn in functions['Functions']:
    print(f"{fn['FunctionName']}: {fn['Runtime']}")
""",
    session_id="code-session-123"
)

print(result.output)
print(result.files)  # Any generated files (charts, data exports)
```

### Browser Tool

A **cloud-based browser runtime** for AI agents to interact with websites:

- **Fully managed** — no browser infrastructure to maintain
- **Enterprise-grade security** — sandboxed, isolated sessions
- **Comprehensive observability** — full session tracing
- **Scale:** Multiple parallel browser sessions supported

```python
from bedrock_agentcore.tools import BrowserToolClient

browser = BrowserToolClient()

session = browser.create_session()

# Navigate and interact
result = browser.navigate(session_id=session.id, url="https://github.com/org/repo")
content = browser.get_content(session_id=session.id)
screenshot = browser.take_screenshot(session_id=session.id)

# Fill forms, click elements
browser.click(session_id=session.id, selector="#deploy-button")
browser.fill(session_id=session.id, selector="#branch-name", value="main")

browser.close_session(session_id=session.id)
```

---

## Section 8: AgentCore Observability

### Overview

AgentCore emits **OpenTelemetry (OTEL)-compatible telemetry**, integrating with existing observability stacks.

### Key Metrics

- Token usage per agent/session
- Latency (cold start, execution, tool call)
- Session duration
- Error rates and types
- Tool call success/failure rates
- Memory retrieval relevance scores

### Setup

```python
# requirements.txt
aws-opentelemetry-distro>=0.9.0

# agent.py
from amazon.opentelemetry.distro import OpenTelemetryDistro
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

# Configure OTEL
tracer_provider = TracerProvider()
tracer = trace.get_tracer("openclaw-agent")

with tracer.start_as_current_span("agent-invocation") as span:
    span.set_attribute("session.id", session_id)
    span.set_attribute("model.id", model_id)
    response = agent(user_message)
```

### Third-Party Integration

- **Langfuse** (open-source LLM observability)
- **Dynatrace**
- **Elastic Observability**
- **Amazon CloudWatch** (native metrics/dashboards)
- **Datadog** (via OTEL)
- **Grafana** (via OTEL)

---

## Section 9: AgentCore Policy

### What It Is

A **Cedar-based policy engine** that intercepts every tool call in real time. Sits outside the agent, providing deterministic control regardless of model behavior.

- Policies written in **natural language** → automatically converted to Cedar (AWS open-source policy language)
- Evaluated before every tool invocation
- Zero-latency enforcement (in-path, not out-of-band)
- Integrated with AgentCore Gateway

### Example Policy (Natural Language → Cedar)

Natural language: *"Agents can only deploy Lambda functions in production with approval from a human reviewer"*

Cedar equivalent:
```
permit (
  principal is AgentCore::Agent,
  action == AgentCore::Action::"invoke_tool",
  resource == AgentCore::Tool::"deploy_lambda"
) when {
  context.environment == "staging" ||
  (context.environment == "production" && context.human_approved == true)
};
```

---

## Section 10: AgentCore Evaluations

### Overview

Built-in evaluation framework with 13 evaluators for common quality dimensions:

- Helpfulness
- Tool selection accuracy
- Response accuracy
- Groundedness (for RAG)
- Coherence
- Instruction following
- Safety

Custom model-based evaluators can be created. Can run during development AND in production (continuous evaluation).

```python
import boto3

eval_client = boto3.client("bedrock-agentcore-evaluations", region_name="us-east-1")

evaluation = eval_client.create_evaluation(
    name="openclaw-quality-eval",
    evaluators=[
        {"builtinEvaluator": {"evaluatorType": "Helpfulness"}},
        {"builtinEvaluator": {"evaluatorType": "ToolSelection"}},
        {"builtinEvaluator": {"evaluatorType": "Accuracy"}}
    ],
    datasetConfig={
        "s3DatasetConfig": {
            "s3Uri": "s3://openclaw-evals/test-dataset.jsonl"
        }
    }
)
```

---

## Section 11: Strands Agents SDK

### Overview

AWS's open-source framework for building production-ready AI agents. [github.com/strands-agents/sdk-python](https://github.com/strands-agents/sdk-python)

- **License:** Apache 2.0
- **Philosophy:** Model-driven, minimal boilerplate, the agent loop handles itself
- **Deployment targets:** Lambda, Fargate, EKS, Bedrock AgentCore, Docker, Kubernetes

### Core Pattern

```python
from strands import Agent, tool
from strands.models.bedrock import BedrockModel

# Define tools as decorated Python functions
@tool
def deploy_lambda_function(
    function_name: str,
    runtime: str,
    code_path: str,
    handler: str = "handler.main"
) -> dict:
    """
    Deploy a new AWS Lambda function.
    Returns the function ARN and endpoint URL.
    """
    import boto3
    client = boto3.client("lambda")

    with open(code_path, "rb") as f:
        code_zip = f.read()

    response = client.create_function(
        FunctionName=function_name,
        Runtime=runtime,
        Code={"ZipFile": code_zip},
        Handler=handler,
        Role=os.environ["LAMBDA_EXECUTION_ROLE_ARN"]
    )

    return {
        "function_arn": response["FunctionArn"],
        "function_name": response["FunctionName"]
    }

# Create agent with Bedrock model
model = BedrockModel(
    model_id="anthropic.claude-sonnet-4-6-20251015-v1:0",
    region_name="us-east-1",
    streaming=True
)

agent = Agent(
    model=model,
    system_prompt="""You are an AWS infrastructure engineer AI agent.
    You can deploy Lambda functions, ECS services, and CloudFormation stacks.
    Always confirm before deploying to production environments.""",
    tools=[deploy_lambda_function, list_running_services, check_cloudwatch_logs]
)

# Run the agent
response = agent("Deploy a new Lambda function for processing Stripe webhooks")
```

### Multi-Agent Patterns

```python
from strands import Agent
from strands.models.bedrock import BedrockModel

# Subagent as a tool
infra_agent = Agent(model=model, tools=[deploy_lambda, deploy_ecs, create_rds],
                    system_prompt="You are an infrastructure specialist...")

code_agent = Agent(model=model, tools=[write_code, run_tests, commit_to_github],
                   system_prompt="You are a software engineer...")

# Orchestrator uses sub-agents as tools
orchestrator = Agent(
    model=model,
    tools=[infra_agent.as_tool(), code_agent.as_tool()],
    system_prompt="""You are an orchestrator. Delegate infrastructure tasks
    to the infrastructure agent and coding tasks to the code agent."""
)

orchestrator("Build and deploy a new microservice for order processing")
```

### MCP Integration

```python
from strands import Agent
from strands.mcp import MCPClient

# Connect to any MCP server (including AgentCore Gateway)
github_mcp = MCPClient(server_url="https://gateway.bedrock-agentcore.../mcp")
slack_mcp = MCPClient(server_url="https://gateway.bedrock-agentcore.../mcp")

agent = Agent(
    model=model,
    mcp_clients=[github_mcp, slack_mcp]
)
```

---

## Section 12: Amazon Bedrock Agents (Classic)

*Note: "Amazon Bedrock Agents" is the original managed agent service, distinct from AgentCore. Both coexist.*

### What It Is

Fully managed agents with built-in orchestration, action groups, and knowledge bases. Less flexible than AgentCore but more managed.

### Multi-Agent Collaboration (Bedrock Agents)

- **Supervisor + Sub-agent model:** Designate one Bedrock Agent as supervisor, associate collaborator agents
- **Routing mode:** Supervisor routes simple requests directly to specialized sub-agents
- **Supervisor mode:** Full orchestration for complex multi-step tasks
- **Parallel execution:** Sub-agents can run concurrently for efficiency
- **Trace and debug console:** Visual inspection of multi-agent interactions

### Action Groups

Action groups connect Bedrock Agents to Lambda functions:

```python
action_group = {
    "actionGroupName": "InfrastructureActions",
    "actionGroupExecutor": {
        "lambda": "arn:aws:lambda:us-east-1:123456789:function:infra-executor"
    },
    "apiSchema": {
        "s3": {
            "s3BucketName": "my-bucket",
            "s3ObjectKey": "infra-api-schema.json"
        }
    }
}
```

---

## Section 13: IAM Patterns for Agents with Infrastructure Powers

For an agent that can BUILD AWS infrastructure, it needs elevated IAM permissions. Best practices:

### Scoped Infrastructure Role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockModelAccess",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:Converse", "bedrock:ConverseStream"],
      "Resource": "*"
    },
    {
      "Sid": "LambdaManagement",
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:InvokeFunction",
        "lambda:GetFunction",
        "lambda:ListFunctions",
        "lambda:DeleteFunction",
        "lambda:AddPermission",
        "lambda:PublishVersion",
        "lambda:CreateAlias"
      ],
      "Resource": "arn:aws:lambda:*:123456789:function:agent-deployed-*"
    },
    {
      "Sid": "CloudFormationDeployment",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateStack",
        "cloudformation:UpdateStack",
        "cloudformation:DeleteStack",
        "cloudformation:DescribeStacks",
        "cloudformation:GetTemplate",
        "cloudformation:ValidateTemplate",
        "cloudformation:ListStacks",
        "cloudformation:DescribeStackEvents"
      ],
      "Resource": "arn:aws:cloudformation:*:123456789:stack/agent-*"
    },
    {
      "Sid": "ECSManagement",
      "Effect": "Allow",
      "Action": [
        "ecs:CreateService",
        "ecs:UpdateService",
        "ecs:DescribeServices",
        "ecs:RegisterTaskDefinition",
        "ecs:RunTask",
        "ecs:StopTask"
      ],
      "Resource": "*",
      "Condition": {
        "StringLike": {"ecs:cluster": "arn:aws:ecs:*:123456789:cluster/agent-deployed-*"}
      }
    },
    {
      "Sid": "ECRAccess",
      "Effect": "Allow",
      "Action": [
        "ecr:GetAuthorizationToken",
        "ecr:CreateRepository",
        "ecr:PutImage",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer"
      ],
      "Resource": "*"
    },
    {
      "Sid": "S3ForDeploymentArtifacts",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::openclaw-agent-artifacts",
        "arn:aws:s3:::openclaw-agent-artifacts/*"
      ]
    },
    {
      "Sid": "SecretsManagerAccess",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue", "secretsmanager:CreateSecret"],
      "Resource": "arn:aws:secretsmanager:*:123456789:secret:openclaw-*"
    },
    {
      "Sid": "AgentCoreMemoryAndGateway",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:InvokeAgentRuntime",
        "bedrock-agentcore:CreateMemoryStore",
        "bedrock-agentcore:IngestConversationEvents",
        "bedrock-agentcore:RetrieveMemoryRecords",
        "bedrock-agentcore:InvokeGateway"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## Section 14: CDK Infrastructure-as-Code for Agent-Deployed Resources

For the agent itself to deploy infrastructure via CDK:

```python
# Tool that the agent calls to deploy CDK stacks
@tool
def deploy_cdk_stack(
    stack_name: str,
    stack_code: str,
    environment: str = "dev"
) -> dict:
    """
    Deploy an AWS CDK stack. Accepts Python CDK code as a string.
    Creates a temporary directory, synthesizes the stack, and deploys.
    """
    import subprocess
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write stack code
        stack_file = os.path.join(tmpdir, "stack.py")
        app_file = os.path.join(tmpdir, "app.py")

        with open(stack_file, "w") as f:
            f.write(stack_code)

        with open(app_file, "w") as f:
            f.write(f"""
import aws_cdk as cdk
from stack import {stack_name}Stack

app = cdk.App()
{stack_name}Stack(app, "{stack_name}", env=cdk.Environment(
    account=os.environ["CDK_DEFAULT_ACCOUNT"],
    region="{os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')}"
))
app.synth()
""")

        # Install CDK dependencies
        subprocess.run(["pip", "install", "aws-cdk-lib", "constructs"], cwd=tmpdir)

        # Synth and deploy
        synth_result = subprocess.run(
            ["cdk", "synth", "--app", "python3 app.py"],
            cwd=tmpdir, capture_output=True, text=True
        )

        if synth_result.returncode != 0:
            return {"error": synth_result.stderr}

        deploy_result = subprocess.run(
            ["cdk", "deploy", "--require-approval", "never", "--app", "python3 app.py"],
            cwd=tmpdir, capture_output=True, text=True,
            env={**os.environ, "CDK_DEFAULT_ACCOUNT": boto3.client("sts").get_caller_identity()["Account"]}
        )

        return {
            "success": deploy_result.returncode == 0,
            "output": deploy_result.stdout,
            "errors": deploy_result.stderr
        }
```

---

## Section 15: Key Architectural Decisions for AWS-Native OpenClaw

| Decision | OpenClaw Original | AWS-Native Equivalent |
|----------|------------------|----------------------|
| Model Provider | Claude via Anthropic API | Claude via Bedrock (same model, different endpoint) |
| Memory Storage | SQLite + Markdown files | AgentCore Memory (managed, scalable) |
| Agent Runtime | Local Node.js process | AgentCore Runtime (serverless, scalable) |
| Tool Discovery | Skills registry (ClawHub) | AgentCore Gateway (MCP endpoint) |
| Identity/Auth | Local config + keychain | AgentCore Identity (OAuth 2.0) |
| Multi-Agent | sessions_spawn (local) | AgentCore Runtime (distributed) |
| Scheduling | Cron in Gateway | EventBridge Scheduler |
| Webhooks | Local HTTP endpoint | API Gateway → Lambda → Agent |
| Observability | Minimal/local logs | AgentCore Observability + CloudWatch |
| Policy | None (SOUL.md behavior rules) | AgentCore Policy (Cedar, enforced) |

---

*Sources: [AWS Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/) · [AgentCore Samples](https://github.com/awslabs/amazon-bedrock-agentcore-samples) · [Starter Toolkit](https://github.com/aws/bedrock-agentcore-starter-toolkit) · [Strands SDK](https://github.com/strands-agents/sdk-python) · [CDK AgentCore Alpha](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-bedrock-agentcore-alpha-readme.html)*