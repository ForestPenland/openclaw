---
name: agentcore-agent-builder
description: "Build, deploy, and register AI agents on AWS AgentCore Runtime and expose them as MCP tools through AgentCore Gateway so OpenClaw can invoke them. Use when: creating a new specialized sub-agent, redeploying an existing AgentCore Runtime agent, adding a new MCP tool to the Gateway, or debugging agent registration failures."
metadata:
  openclaw:
    emoji: "🏗️"
    requires:
      bins: ["aws", "python3", "docker"]
---

# AgentCore Agent Builder

Pattern for building a Strands-powered MCP server agent, deploying it to
AgentCore Runtime, and registering it as a tool in AgentCore Gateway so
OpenClaw can call it.

## Architecture

```
OpenClaw -> AgentCore Gateway (MCP) -> AgentCore Identity (OAuth M2M) -> AgentCore Runtime (FastMCP server)
```

Key constraint: Gateway's mcpServer target outbound auth only supports
JWT OAuth M2M, not IAM SigV4. The agent container must expose an MCP
streamable-HTTP server, not a plain HTTP endpoint.

## Prerequisites

Before building agents, you need these resources (created by the CDK stacks):

- AgentCore Gateway (from OpenClawAgentCoreTools stack)
- Cognito User Pool (from OpenClawIdentity stack)
- CodeBuild service role (from role-factory pattern)
- ECR access (from Gateway task role)
- Build artifacts S3 bucket

Get the resource IDs from your deployment outputs or from the agent's
memory/knowledge/resources/aws-infrastructure.md file.

## Isolation Checklist

Before creating any resource, verify nothing from the new agent will
collide with an existing one:

```bash
AGENT_NAME="<your-agent-name>"

# 1. ECR repo
aws ecr describe-repositories --repository-names "$AGENT_NAME" 2>&1

# 2. CodeBuild project
aws codebuild batch-get-projects --names "${AGENT_NAME}-build" --query 'projects[0].name' --output text 2>&1

# 3. IAM execution role
aws iam get-role --role-name "agent-task-${AGENT_NAME}-exec" 2>&1

# 4. Cognito resource server
aws cognito-idp list-resource-servers \
  --user-pool-id $COGNITO_POOL_ID \
  --query "ResourceServers[?Identifier=='${AGENT_NAME}-runtime'].Identifier" --output text 2>&1
```

## Step-by-Step: Deploying a New Agent

### Step 0: Choose a Name

Pick a short, lowercase-hyphenated name (e.g. memory-agent, research-agent).
This becomes the prefix for ALL resources:

| Resource                | Naming Pattern                 |
| ----------------------- | ------------------------------ |
| ECR repo                | `<agent-name>`                 |
| CodeBuild project       | `<agent-name>-build`           |
| IAM execution role      | `agent-task-<agent-name>-exec` |
| Cognito resource server | `<agent-name>-runtime`         |
| Gateway target          | `<AgentName>` (PascalCase)     |

### Step 1: Write the Agent as a FastMCP Server

```python
# agent.py
from mcp.server.fastmcp import FastMCP
from strands import Agent
from strands.models.bedrock import BedrockModel

mcp = FastMCP(name="AgentName", host="0.0.0.0", stateless_http=True)

@mcp.tool()
def tool_name(param: str) -> str:
    """Clear description — becomes the MCP tool schema shown to OpenClaw."""
    model = BedrockModel(model_id="us.amazon.nova-lite-v1:0")
    agent = Agent(model=model, system_prompt="You are ...")
    return str(agent(param))

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

### Step 2: Create ECR Repository

```bash
aws ecr create-repository --repository-name $AGENT_NAME --image-scanning-configuration scanOnPush=true
```

### Step 3: Build and Push via CodeBuild

Package source, upload to S3, trigger build. See the buildspec.yml template
in agents/memory-agent/ for the pattern.

### Step 4: Create IAM Execution Role

Use the role-factory skill, Pattern B. Minimum permissions:

- ECR pull from the agent's repo
- CloudWatch logs
- bedrock:InvokeModel (if the agent calls Bedrock)
- Any service-specific APIs

Wait ~10 seconds after put-role-policy before creating the runtime.

### Step 5: Cognito Resource Server + M2M Client

```bash
# Resource server
aws cognito-idp create-resource-server \
  --user-pool-id $COGNITO_POOL_ID \
  --identifier "${AGENT_NAME}-runtime" \
  --name "${AGENT_NAME}Runtime" \
  --scopes '[{"ScopeName":"invoke","ScopeDescription":"Invoke MCP server"}]'

# M2M client
aws cognito-idp create-user-pool-client \
  --user-pool-id $COGNITO_POOL_ID \
  --client-name "${AGENT_NAME}-m2m-client" \
  --generate-secret \
  --allowed-o-auth-flows client_credentials \
  --allowed-o-auth-scopes "${AGENT_NAME}-runtime/invoke" \
  --allowed-o-auth-flows-user-pool-client
```

### Step 6: Create AgentCore Runtime

```bash
aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name "<AgentName>" \
  --description "<what this agent does>" \
  --agent-runtime-artifact '{"containerConfiguration":{"containerUri":"<ecr-uri>:latest"}}' \
  --role-arn "arn:aws:iam::<account>:role/agent-task-<agent-name>-exec" \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --protocol-configuration '{"serverProtocol":"MCP"}' \
  --authorizer-configuration '{"customJWTAuthorizer":{"allowedClients":["<m2m-client-id>"],"discoveryUrl":"<cognito-oidc-url>"}}'
```

### Step 7: Create Credential Provider

```bash
aws bedrock-agentcore-control create-oauth2-credential-provider \
  --name "<agent-name>-m2m-creds" \
  --credential-provider-vendor "CustomOauth2" \
  --oauth2-provider-config '{
    "customOauth2ProviderConfig": {
      "clientId": "<m2m-client-id>",
      "clientSecret": "<m2m-client-secret>",
      "oauthDiscovery": {"discoveryUrl": "<cognito-oidc-url>"}
    }
  }'
```

### Step 8: Register as Gateway Target

```bash
aws bedrock-agentcore-control create-gateway-target \
  --gateway-identifier "<gateway-id>" \
  --name "<AgentName>" \
  --description "<what this agent does>" \
  --target-configuration '{"mcp":{"mcpServer":{"endpoint":"<runtime-endpoint>"}}}' \
  --credential-provider-configurations '[{
    "credentialProviderType": "OAUTH",
    "credentialProvider": {
      "oauthCredentialProvider": {
        "providerArn": "<credential-provider-arn>",
        "scopes": ["<agent-name>-runtime/invoke"]
      }
    }
  }]'
```

### Step 9: Verify

Tool names follow the pattern: `<GatewayTargetName>___<tool_function_name>`

## Reference Implementation

See `agents/memory-agent/` for a complete working example of a FastMCP
agent with Dockerfile, buildspec, and requirements.
